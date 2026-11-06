# Agent 操作只传参数

日常操作使用已经实现好的 MCP 工具或 `iphone` 命令。不要再为每一步生成 Python heredoc、urllib 请求、临时请求脚本或自己处理 base64。HTTP 代码已经收进固定客户端，MCP 与 CLI 共用它。

这次优化减少的是模型需要生成的代码和读回的数据量；没有改动后台 OCR 调度，也没有承诺消除每轮模型等待。实际任务提速仍需下一轮实测。

## MCP：优先入口

先启动 [观察及动作服务](action-api.md)，然后注册本地 MCP。以 Codex 为例，在仓库根目录执行一次：

```sh
codex mcp add iphone-pilot -- uv run --python 3.12 --script "$PWD/tools/observe_mcp.py"
codex mcp get iphone-pilot --json
```

查看客户端工具列表，确认已经出现 `iphone_status`、`iphone_observe`、`iphone_act`、`iphone_type`。配置文件里有服务器不等于当前会话已经加载；工具尚不可见时，新建或重新加载会话，当前任务可先使用下面的固定 CLI。

长操作可在 Codex 的 `mcp_servers.iphone-pilot` 配置节设置 `startup_timeout_sec = 30`、`tool_timeout_sec = 180`。只修改本服务器配置，不改模型、权限或其他 MCP 服务。注册方法依据 [OpenAI 官方 MCP 配置说明](https://developers.openai.com/learn/docs-mcp)。

调用示例：

```text
iphone_observe()
iphone_observe(include_image=true, observation_id="当前编号")
iphone_status()
iphone_act(action="tap", observation_id="本次返回的编号", target_id=12,
           request_id="p1", wait_for_text="发布")
iphone_act(action="tap", observation_id="本次返回的编号", target_id=12,
           request_id="p2", include_image=true)
iphone_type(text="完整中文正文。", request_id="t1", include_image=true)
```

每次根据返回结果决定下一步，动作已经返回观察时直接复用。`request_id` 在本次预定输入/点击期间保持不变，新的物理动作才使用新编号。

MCP/CLI 默认返回精简文字结果：保留 ID、文字、中心、置信度、可点击性未知标记、画面尺寸、stale、读取模式、帧龄、连接信息及动作状态；省略重复哈希、矩形框、接收器进程状态、帧序号、接收时间和通用说明。观察立即返回最近可读帧，不等待它变“新鲜”。没有新帧但有缓存时，照常返回最近截图和 OCR，并标记 `stale=true`；过期画面可查看，不可用于点击或输入，动作端会继续拦截。只有没有可读缓存时才返回暂不可用错误。`iphone_status()` 分开报告 `fresh_frame_available`（帧龄是否满足动作门槛）和 `cached_frame_available`（是否仍有旧图可查看）；这两项都不判断手机镜像是否连接。排障时不必直接调用接收器 HTTP 端口。`full=true` 恢复完整 OCR 与来源诊断元数据。HTTP 接口仍提供本机诊断字段。图片仍单独通过 MCP ImageContent 返回。

## CLI：当前会话即可使用

在仓库根目录：

```sh
./iphone status
./iphone observe
./iphone observe --include-image
./iphone tap 12 --id p1 --wait 完成 --include-image
./iphone tap --xy 378 296 --id stars1 --timeout 2000
./iphone swipe 250 850 250 350 --id scroll1 --ms 300
./iphone type --id t1 --text-file runtime/draft.txt --include-image
./iphone image
./iphone wait '成功|完成|OK' --timeout 8000
./iphone wait 空格 --gone --timeout 4000
./iphone dismiss --id kb1
./iphone pixel --xy 379 456
```

`tap/swipe` 坐标参数两用：`tap 421 655`（两个位置参数）与 `tap --xy 421 655` 等价；单值位置参数仍指条目编号（`tap 12`）。`wait` 只轮询 OCR，不发送物理动作，`--gone` 反转为等待文本消失，文本支持 `|` 分隔的多个候选；`--include-result` 附上最终观察。`dismiss` 自动寻找键盘上方的收起箭头点击并验证键盘消失，失败时报错而非盲点。`pixel` 从最近帧采样指定坐标的平均颜色（OCR 空间坐标），用于 OCR 无法判断的控件状态（选中/未选中、开关、评级等）。

`--include-image` 会在同一次 API 调用中取得对应帧；CLI 将图片写入本地 JPEG，并返回 `image_file`，MCP 则附加图片内容块。默认不带图片。需要视觉核对时才打开/查看它；若只是读文字与坐标，使用默认输出即可。可用 `--image-output PATH` 指定 CLI 图片路径。

`pixel` 支持多点：`./iphone pixel --xy x1 y1 x2 y2 x3 y3` 一次抓帧采样多个点，返回 `samples` 数组；单点时结果直接平铺在顶层。采样点若靠近上一次点击的位置（光标停留处会压暗颜色），结果带 `near_last_tap` 警告，此时应交叉核对同类相邻元素而不是重拍，也不要点击"空白处"挪开光标——表单页面上任何坐标都可能是活动控件。加 `--compare R G B [R G B ...] [--tolerance 60]` 后每个采样点带 `filled` 与 `distance`：选中/激活色随 app 主题而不同，不要假设固定值，先在已知选中状态采样，或传入多组参考色让 `filled` 匹配任一。

`taps` 支持一次调用顺序多点：`./iphone taps --id s1 --xy x1 y1 x2 y2 --expect 锚点文本`。`--expect` 是必填的防盲发锚点：当前帧上找不到该文本时拒绝执行任何点击，防止复合动作在过期页面上误发。

基础设施排障全部收敛在 CLI 下（均为只读，token 留在服务端不出命令行）：`./iphone health` 看观察 API 的动作/输入可用性；点击全部无效但观察正常时，通常是动作被禁用或板子 IP 变了。`./iphone board discover --save` 并发探测局域网 HID 桥端口（自动排除回环/链路本地/代理 fake-ip 网段），把找到的板子 IP 存入 `runtime/board.json`；`./iphone board status [--host IP]` 直连板子返回固件/USB/网络原始状态；随后 `tools/serve.sh [BOARD_IP]` 一键重启接收端与观察 API（可用 `UXPLAY_BIN`、`SECRETS` 环境变量覆盖路径）。`./iphone receiver` 一次返回接收端帧状态与观察 API 健康。`dismiss` 自动收起键盘，按顺序尝试三种策略并逐一验证：app 自带的收起箭头 → AirType 面板的 ✕ 关闭按钮 → 内容区小幅滚动触发失焦；全部失败才报错（不再要求页面必须有箭头）。`tap --retry N` 用于画面持续变化的页面（如相机取景）：动作因页面不稳定被拒时自动重新观察并以子编号重试，被拒的尝试不会发送任何物理动作；它也覆盖"点击被接受但毫无效果"的情况——只有当当前帧与点击前布局一致（页面没有反应）且 `--wait` 文本始终未出现时才重发，页面已变化则绝不多点。`type` 的 `not_confirmed` 会跨行拼接所有 OCR 文本后复查插入前缀（文本换行显示不影响判定）。`./iphone scroll --id s1 --until '立即评价'` 反复滚动直到目标文本出现（浮动横幅遮挡控件、长列表找按钮的场景），支持 `--gone`/`--max`/`--dir`，未找到则超时返回并保留最终观察。

以上是独立用法示例，不是一组应当盲目批量执行的手机动作。`tap/swipe` 自动使用本 CLI 上一次见到的观察编号，并由服务再次核对页面，因此不用抄写 64 位哈希、地址、宽高或 HTTP JSON。需要明确指定画面时使用 `--from OBSERVATION_ID`。未知或变化的页面仍要重新观察。

### 一次等待到结果，不提前返回再查询

CLI 本身是同步请求。Agent 使用命令工具执行时，保留其前台等待默认值；对于 `exec_command`，使用 `yield_time_ms:10000`，已知较慢的操作可设为 30000，不要为了“异步”缩短到 1000。外层 `functions.exec` 也不要设置一秒提前返回。

```json
{"cmd":"iphone tap 12 --id task1-p1 --wait 发布","yield_time_ms":10000}
```

十秒是本次工具调用等待进程的上限，命令两秒完成就两秒返回，不会额外睡满十秒。它与 CLI `--timeout` 的页面结果检查预算是两个参数，不改变点击、OCR 或后台动作超时语义。

只有工具明确返回“仍在运行”及会话编号时，才等待该原会话，通常仍等待 10000 ms；正常已完成结果后不追加查询。不要为取回结果重新执行同一个点击/输入命令。原生 MCP 直接等待工具返回即可。

这条规则针对实测中的额外往返：上一轮有 7 次为了取回命令结果而单独查询。现在优先在首次调用中收齐结果，实际能减少多少模型轮次仍以实测为准。

正文文件按 UTF-8 原样发送，包括末尾换行。已确认是多行编辑器时可加 `type --allow-newlines`，不要为绕过检查而在发送栏盲目开启。

`image` 默认取回上一次观察对应的图片，写成私有本地文件并返回 `image_file`，Agent 直接用图片查看工具打开这个路径。不会向终端打印 base64。可用 `image --from ID --output runtime/view.jpg` 查看指定缓存帧；查看历史图不会替换当前动作所用的观察编号。

命令可以从任意工作目录通过绝对路径调用。也可以把仓库的可执行 `iphone` 链接到自己的 PATH：

```sh
ln -s /ABSOLUTE/PATH/iphone-pilot/iphone /PATH/ON/YOUR/PATH/iphone
```

仅在目标不存在时建立链接，不覆盖其他工具。入口会解析链接，找到正确仓库。CLI 只使用 Python 标准库，无需为每一步启动 MCP SDK 或下载依赖。

## 会话与重试

CLI 默认把当前观察编号和最近 100 个请求保存在忽略的 `runtime/cli-session.json`，权限 0600；输入请求可能包含正文，按私有运行数据处理。同一会话使用文件锁，避免两个命令同时修改状态。不同 Agent 使用不同文件：

```sh
./iphone --session runtime/agent-a.json observe
./iphone --session runtime/agent-a.json tap 12 --id p1 --wait 发布
```

不要同时让两个 Agent 操作同一手机。独立 CLI 状态不代表可以并行抢焦点；后台仍将动作和输入串行处理。

请求发出前，CLI 先保存完整参数；失败后不会自动重试物理动作。若错误明确写明“no action sent”，可重新观察并重新判断当前页面；只有确认同一动作仍合适时，才用新观察和新 request_id 执行。若结果不确定或动作已 accepted，必须检查手机并用原 request_id 取回结果，不能换 ID 重发。超时后即使执行过新的 observe，同一请求编号的重试仍绑定原画面和参数，并交给后台的防重放日志处理；它不会改为点击新页面上的同编号条目。更改目标、正文或等待条件后复用同一编号会被拒绝。参数超过 CLI 历史窗口时，后台日志仍负责阻止重复执行或拒绝不一致参数。

HTTP 失败或缺少操作后观察时，CLI 会清除默认动作画面，避免下一条新动作隐式使用操作前旧帧。原请求的重试参数仍被保留。重新观察后，依照实际结果决定下一步，不盲目换编号重发。

全局 `--full` 恢复完整输出；`--api` 或 `IPHONE_OBSERVE_URL` 可指定另一个本地服务，禁止公共地址及携带凭据的 URL。会话文件绑定 API 地址，避免把一个服务的坐标传给另一个服务。
