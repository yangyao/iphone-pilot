# 先读文字，需要时再看图

Agent 不必每一步都重新理解整张手机截图。iPhone Pilot 持续接收 AirPlay 画面，用 Mac 自带的 Apple Vision 定位文字，向 Agent 返回文字和像素坐标。遇到星星、返回箭头、无文字图标或布局歧义，再请求原图。

## 启动一次，反复调用

需要 macOS、Xcode Command Line Tools、Python 3.11+。先按 [setup](setup.md) 启动接收端并让手机镜像到 **iPhone Agent Mac**。接着在仓库根目录：

```sh
bash tools/build-ocr.sh
python3 observe.py
```

服务仅监听 `127.0.0.1:18766`，接收端默认 `18765`。OCR 子进程按需启动并常驻，超时或崩溃后在下一次请求重建；相同 JPEG 的识别结果复用。不会调用云模型，也不会操作手机。退出服务使用 Ctrl+C。

```sh
curl 'http://127.0.0.1:18766/observe'
curl 'http://127.0.0.1:18766/observe?include_image=true'
curl 'http://127.0.0.1:18766/observe?include_image=true&observation_id=PREVIOUS_ID'
```

默认结果为 JSON：`observation_id`（图片 SHA-256）、`width/height`、`coordinate_origin: top-left`、`items`（`id/text/box/center/confidence/clickable`）、`source`、`stale`、`cached`。`box` 是 `[x,y,width,height]`，`center` 是 `[x,y]`，单位为原图像素。编号仅在该观察内有效。`clickable:null` 表示未验证可点击性。

HTTP 的 `include_image=true` 添加 `image: {mime_type, data}`，data 为 JPEG base64。带 `observation_id` 取回同一张图，即使手机已经翻页；最近 8 张观察保存在内存，过期返回 404。HTTP JSON 主要供程序使用，Agent 优先通过 MCP 取得真正的图片内容块。

最新帧超过 5 秒或接收源停止，返回 503，不冒充实时画面。`--max-age` 可配置。缓存观察过期仍可查看，但 `stale:true` 时不能用其坐标操作。接收时间只证明 Mac 收到了帧，不能证明 AirPlay 没有冻结或应用没有卡住。新接收端提供与图像原子绑定的时间和序号；兼容旧接收端时 `source.metadata_exact:false`，时间与序号只是近似状态，图片与 OCR 仍由哈希绑定。`/health` 仅表示 API 存活，不表示手机已连接。

## MCP

使用官方 Python MCP SDK 的维护版 1.30.0（固定版本，避免主版本接口变化），通过 stdio 提供一个工具：

```json
{"mcpServers":{"iphone-pilot":{"command":"uv","args":["run","/ABSOLUTE/PATH/iphone-pilot/tools/observe_mcp.py"]}}}
```

接收端和观察服务先独立启动。MCP 适配器只负责转发，不会再启动一套投屏。可用 `IPHONE_OBSERVE_URL` 指定另一个本地观察端口。依赖首次安装需要网络，后续观察使用本机服务。

```text
iphone_observe()
iphone_observe(include_image=true, observation_id="上一次返回的编号")
```

普通调用只有文字内容块；带图时另附 MCP `ImageContent`，文字中不塞 base64。API/MCP 只做观察，点击仍使用绝对坐标 HID，中文输入使用 AirType。不要把服务端口暴露到局域网或公网，结果包含手机屏幕内容。

## 操作方式与实测结论

1. 默认读文字，按当前页语义选择文字中心；点击后重新观察。
2. 文字不包含目标、点击没变化、评分星星等目标不明确时，请求该观察的图片。
3. 明确同一页的独立操作可批量执行；翻页或弹窗后重新获取坐标。
4. 输入后核对内容，提交后寻找成功文字；HTTP 成功不能替代界面验证。

淘宝花洒支架的一次评价流程已用 OCR 文字完成主要导航，用原图确认评分星星，用 AirType 输入中文，最后读到“发布成功”。这说明该组合可用；用户感受到误点减少，但尚未做速度和错误率的对照测量。OCR 也曾误认个别汉字，App 图标下方的文字中心有时点不开，需要点击图标本身。

此版本的默认接口是 Apple Vision OCR，不自动运行 OmniParser。研究实验中的 OmniParser 图标描述存在误识别，不能把检测框当作真实交互树。未来可将其作为可选补充，现阶段无文字目标按需查看原图更直接。
