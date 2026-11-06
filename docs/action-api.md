# 点一下，直接拿回结果

`iphone_act` 把“执行动作 → 等画面变化 → OCR → 核对预期 → 返回结果”放进一次 MCP 调用。支持绝对坐标点击与拖动，不需要改固件，不需要每次启动控制 CLI。遇到无法确认的结果，它返回具体状态，不自动重复点击。

## 启用

先完成 [连接设置](setup.md) 和 [观察服务设置](observation-api.md)。在运行观察服务时指定板子的局域网地址与本地凭据文件：

```sh
python3 observe.py --board-host BOARD_IP --board-config /ABSOLUTE/PATH/secrets.h
```

`--board-config` 默认是仓库的 `hardware/hid_bridge/secrets.h`，读取其中的 `HYBRID_HTTP_TOKEN`；也可指定原有本地固件工作目录中的文件。Token 不会返回给模型或写入动作结果。不要把凭据复制到公开配置、README 或 Skill。

也可设置 `IPHONE_BOARD_HOST` 和 `IPHONE_BOARD_CONFIG` 环境变量。没有指定板子时，观察与 AirType 仍可使用，动作请求会提示配置缺失。`/health` 返回 `actions_enabled`、`input_enabled`，仅代表功能已配置，不表示手机在线。`--read-only` 禁用全部写入；`--disable-input` 仅禁用 AirType。

现有 MCP 配置不变，重启 MCP 适配器即可发现第三个工具 `iphone_act`。无需给每个 Agent 单独配置板子 Token。

## 调用

先调用 `iphone_observe()`，根据本次文字与布局选择目标：

```python
iphone_act(
    action="tap",
    observation_id="刚才返回的图片哈希",
    target_id=12,
    request_id="open-reviews-001",
    wait_for_text="待评价商品"
)
```

它根据该观察的文字中心换算 HID 坐标，检查板子 USB 与控制占用状态，再用当前实时页面核对位置。文字中心不保证可点击；App 图标、星星等仍可能需要原图定位。

无文字目标可用原图像素坐标，不能同时传 `target_id`：

```python
iphone_act(action="tap", observation_id="当前编号", request_id="tap-icon-001", x=420, y=640)
iphone_act(action="swipe", observation_id="当前编号", request_id="scroll-001",
           x=250, y=850, end_x=250, end_y=350, duration_ms=400)
```

坐标单位是当前截图像素，尺寸由观察自动取得；无需再传宽高。拖动时长 60–2000 ms。接口只支持 tap/swipe，不接受原始指令串，不会隐式输入文字或自动执行下一步。

## 等待与返回

`timeout_ms` 默认 5000，范围 200–15000，是动作被板子确认后的等待预算；不包括预检查、板子调用和响应传输。底层一次 OCR 或网络读取可能耗尽甚至超过剩余预算，不能把它当作整个工具的硬截止时间。

等待只认板子确认之后收到的帧，忽略仅状态栏时钟变化；页面文字和位置在连续两帧中保持一致后，返回：

| wait.status | 含义 |
| --- | --- |
| `condition_met` | 内容变化且预期文字出现在稳定画面中 |
| `changed` | 未指定预期文字，观察到稳定的内容变化 |
| `timeout` | 收到后续画面，但未确认所需变化或文字 |
| `no_new_frame` | 等待期间没有收到可用的新帧 |
| `observation_unavailable` | 动作后无法读取画面，例如投屏断开 |
| `not_started` | 未进入结果等待，例如控制请求本身结果不确定 |

`accepted:true` 仅说明板子确认 HID 执行，不等于进入目标页面或完成发布。`changed` 只代表观察到变化；业务成功仍需“发布成功”等可靠页面证据。即使指定的文字在操作前已出现，也要等页面内容发生变化后才能返回 `condition_met`。

正常结果包含：

- `before_observation_id` / `after_observation_id`：操作前后画面编号。
- `observation`：操作后的完整 OCR，沿用文字、坐标、AirType 连接信息与 freshness 字段。
- `changes.added` / `changes.removed`：变化的文字与位置；位置移动也视作移除再新增。
- `timings_ms`：预检查、板子状态请求、动作请求、结果等待与合计耗时。预检查包含状态请求；等待包含 `wait.observation_ms` 的 OCR/取帧耗时，因此这些子项不应重复相加。合计不包括模型决策、客户端传输和最终图片序列化。

返回的观察已经是本次操作后的观察。只要它仍新鲜且足以确定下一目标，就直接使用其中的编号继续操作，不必额外调用一次 `iphone_observe()`。

需要减少模型阅读量时传 `changes_only=True`：仍返回画面元数据和变化条目，但省略 `observation.items`。新增条目的 ID 属于新画面，移除条目的 ID 属于旧画面；若要点未出现在变化列表里的目标，先取一次完整观察，不沿用旧编号。

需要看图时传 `include_image=True`。HTTP 会附 JPEG base64；MCP 自动转成图片内容块，文字中不包含 base64。也可用返回的 `after_observation_id` 调用 `iphone_observe(include_image=True, ...)` 查看同一帧。缓存已淘汰时返回明确说明，不拿别的截图替代。

## 拒绝过期坐标与重复动作

执行前以最新画面对照原观察：尺寸必须一致；若 JPEG 哈希不同，则正文 OCR 的文字和位置必须一致（忽略状态栏、空白及小幅坐标抖动）。内容、弹窗文字或位置改变时拒绝发送，要求重新观察。无正文可核对时，需要图像哈希仍一致。

这是 OCR 布局检查，不是 iOS 交互树。纯图形变化、无文字遮挡或 AirPlay 重播旧内容不一定能被它识别。新帧的接收时间也不代表手机的真实拍摄时间。不要让另一个 Agent、旧控制 App 或手动 CLI 同时操作；本服务内部会让点击、滑动与 AirType 输入共用一把操作锁，但无法锁住外部控制者。

每个动作使用唯一 `request_id`。网络超时后，用相同编号、相同动作参数重试，只返回之前的结果，不再次执行。可以改变 `include_image/changes_only` 取结果的格式；动作、目标、预期文字和等待预算不能改变。更换编号代表新的物理动作，不能用来盲目重试提交。

动作发送前，服务会先把请求摘要与状态写入私有的 `runtime/phone-actions.sqlite`。即使重启，也不会自动重放已尝试的请求；如果恰好在落盘后、发送前退出，会保守返回 `uncertain`，需要查看实际状态。动作日志可能含屏幕文字变化，属于私有运行数据，不应上传。

## HTTP

`POST http://127.0.0.1:18766/act`，`Content-Type: application/json`，JSON 参数与 MCP 相同。拒绝浏览器 Origin 请求。示意：

```json
{
  "action": "tap",
  "observation_id": "64位观察编号",
  "request_id": "open-reviews-001",
  "target_id": 12,
  "wait_for_text": "待评价商品",
  "timeout_ms": 5000,
  "changes_only": false,
  "include_image": false
}
```

调用前的页面不符或设备未就绪会返回错误且不发送动作；发送后响应不明则返回 `status:uncertain`。收到任何异常结果时先检查当前状态，不根据 HTTP 成功或失败推断是否发生了点击。

## 验证范围

已用模拟投屏和板子完成动作转换、页面核对、稳定等待、文字条件、时钟变化排除、断流、超时、防重放、重启恢复和 MCP 图片返回验证。底层绝对坐标固件已有实机使用记录；新的动作与观察封装仍需在实时镜像与无敏感操作页面上验收，不把模拟测试当作实机速度数据。
