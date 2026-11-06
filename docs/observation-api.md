# 先读文字，需要时再看图

Agent 不必每一步都重新理解整张手机截图。iPhone Pilot 持续接收 AirPlay 画面，用 Mac 自带的 Apple Vision 定位文字，向 Agent 返回文字和像素坐标。遇到星星、返回箭头、无文字图标或布局歧义，再请求原图。

## 启动一次，反复调用

需要 macOS、Xcode Command Line Tools、Python 3.11+。先按 [setup](setup.md) 启动接收端并让手机镜像到 **iPhone Agent Mac**。接着在仓库根目录：

```sh
bash tools/build-ocr.sh
./iphone serve start
```

服务仅监听 `127.0.0.1:18766`，接收端默认 `18765`。OCR 子进程按需启动并常驻，超时或崩溃后在下一次请求重建；相同 JPEG 的识别结果复用。观察请求不会调用云模型或操作手机。服务默认另提供 AirType 输入，配置板子后还可执行动作；使用 `--read-only` 可仅启用只读观察。退出服务使用 Ctrl+C。

```sh
curl 'http://127.0.0.1:18766/observe'
curl 'http://127.0.0.1:18766/observe?include_image=true'
curl 'http://127.0.0.1:18766/observe?include_image=true&observation_id=PREVIOUS_ID'
```

默认结果为 JSON：`observation_id`（图片 SHA-256）、`width/height`、`coordinate_origin: top-left`、`items`（`id/text/box/center/confidence/clickable`）、`source`、`stale`、`cached`。`box` 是 `[x,y,width,height]`，`center` 是 `[x,y]`，单位为原图像素。编号仅在该观察内有效。`clickable:null` 表示未验证可点击性。

同时从本帧 OCR 提取 `airtype`，不额外获取手机截图或请求模型。当 AirType Web Access 面板可辨认时，返回 `detected:true`、`endpoint`、`host`、`port`、`pairing_code`（字符串，保留前导零），以及 `connection_verified:false`。这只是当前画面中读到的信息，未尝试联网配对；不会返回 Token。面板未出现、连接信息有歧义或画面过期时，返回 `detected:false`、`pairing_code:null` 和原因。可识别地址但没有可读六位码时，`detected:true` 而 `pairing_code:null`，仍不能推断无需配对。

AirType 地址在部分屏幕上字号很小。检测到配对面板时，会额外在同一张图的底部做一次局部放大 OCR，URL 条目标记为 `ocr_region:airtype-footer`；不增加手机截图，也不请求模型识图。局部识别保留原图坐标。若面板并列 IP 与 `.local`，同端口相邻地址可作为替代，优先用唯一 IP；冲突的 IP/端口仍拒绝。此局部识别和首次 OCR 冷启动会增加耗时。

HTTP 的 `include_image=true` 添加 `image: {mime_type, data}`，data 为 JPEG base64。带 `observation_id` 取回同一张图，即使手机已经翻页；最近 8 张观察保存在内存，过期返回 404。HTTP JSON 主要供程序使用，Agent 优先通过 MCP 取得真正的图片内容块。

观察不等待画面达到某个“新鲜度”门槛：它立即读取接收器最近的帧并返回 OCR。如果帧超过 5 秒、接收器给不出新内容但还保留旧图，或服务只能使用内存中的上一帧，结果会标记 `stale:true`，并带上 `frame_read_mode`、`frame_age_seconds`。这样可以继续查看最后画面，同时让 Agent 知道它有多旧。`iphone_act` 与 `iphone_type` 仍拒绝使用 stale 画面执行点击或输入；只有从未取得过画面且没有缓存可读时，观察才返回 503。`--max-age` 可配置。接收时间只证明 Mac 收到了帧，不能证明 AirPlay 没有冻结或应用没有卡住。MCP/CLI 默认保留 `stale`、读取模式与帧龄；完整 `source` 诊断字段用 `full=true` 查看。新接收端提供与图像原子绑定的时间和序号；兼容旧接收端时 `source.metadata_exact:false`，时间与序号只是近似状态，图片与 OCR 仍由哈希绑定。`/health` 仅表示 API 存活，不表示手机已连接。

## MCP

使用官方 Python MCP SDK 的维护版 1.30.0（固定版本，避免主版本接口变化），通过 stdio 提供状态、观察、输入和动作四个工具：`iphone_status()` 分开报告近期帧是否可用于点击，以及接收器是否仍保留可查看的旧图；它不推断手机镜像连接状态，也不需要 Agent 直接访问接收器 HTTP 端口。

```json
{"mcpServers":{"iphone-pilot":{"command":"uv","args":["run","/ABSOLUTE/PATH/iphone-pilot/tools/observe_mcp.py"]}}}
```

接收端和观察服务先独立启动。MCP 适配器只负责转发，不会再启动一套投屏。可用 `IPHONE_OBSERVE_URL` 指定另一个本地观察端口。依赖首次安装需要网络，后续观察使用本机服务。

实际注册、固定短命令入口及会话保护见 [Agent 调用入口](agent-entrypoints.md)。日常操作直接使用工具或 `./iphone`，不再临时生成 HTTP 请求脚本。MCP 默认为精简 OCR；需要原始 box 等字段时传 `full=true`，HTTP 格式不变。

```text
iphone_observe()
iphone_observe(include_image=true, observation_id="上一次返回的编号")
iphone_type(text="需要输入的整段中文。", request_id="draft-001")
iphone_type(text="需要输入的整段中文。", request_id="draft-002", include_image=true)
iphone_act(action="tap", observation_id="当前编号", target_id=12,
           request_id="open-page-001", wait_for_text="目标页面文字")
iphone_act(action="tap", observation_id="当前编号", target_id=12,
           request_id="inspect-result-001", include_image=true)
```

普通观察调用只有文字内容块；带图时另附 MCP `ImageContent`，文字中不塞 base64。`iphone_type` 会把输入后用于 OCR 核验的观察及坐标直接放在同一结果中；输入结果不确定时也会尽量附上当前观察。`include_image=true` 再附对应截图，因此通常无需紧接着另调观察。输入不包含点击发布。点击仍使用绝对坐标 HID；`iphone_type` 自动从已展开的 AirType 键盘读取配对信息并一次输入，详见 [中文输入](chinese-input.md)。不要把服务端口暴露到局域网或公网，结果包含手机屏幕内容。

`iphone_act` 将点击或拖动、等待页面变化、返回 OCR 合为一次调用，支持文字条件、变化摘要和阶段耗时。板子配置及结果状态详见 [动作 API](action-api.md)。

## 操作方式与实测结论

1. 默认读文字，按当前页语义选择文字中心；点击后重新观察。
2. 文字不包含目标、点击没变化、评分星星等目标不明确时，请求该观察的图片。
3. 明确同一页的独立操作可批量执行；翻页或弹窗后重新获取坐标。
4. 输入后核对内容，提交后寻找成功文字；HTTP 成功不能替代界面验证。

淘宝花洒支架的一次评价流程已用 OCR 文字完成主要导航，用原图确认评分星星，用 AirType 输入中文，最后读到“发布成功”。这说明该组合可用；用户感受到误点减少，但尚未做速度和错误率的对照测量。OCR 也曾误认个别汉字，App 图标下方的文字中心有时点不开，需要点击图标本身。

默认接口仍为 Apple Vision OCR；可显式启用下述 OmniParser 混合模式补充图示定位。检测框不是 App 真实交互树，图示功能应结合原图确认。

## SQLite 背景觀察

觀察服務現在由背景執行緒每 0.5 秒讀取最新影格，完成 OCR 後提交到 `runtime/observations/observations.sqlite`（WAL 模式）。`/observe` 直接查詢最新完成的紀錄，不在請求內等待 OCR；首次啟動尚無紀錄時返回 503，可稍後重試。同一圖片雜湊復用 OCR 與圖片檔案，只更新最近接收時間。背景處理不建立待處理影格隊列。

每筆結果增加 `received_at`、`ocr_completed_at`（Unix 秒）、`image_path`（本機絕對路徑）與 `image_url`（相對觀察 API 的地址）。圖片使用 UTC 接收時間、序號與雜湊命名，例如 `frames/20261106/101530.123000_s123_abcd12345678.jpg`。`GET /image?observation_id=HASH` 可按需讀取該筆 JPEG；CLI/MCP 的 `include_image` 仍可用。

`--observation-directory` 指定資料目錄，`--observation-interval` 指定背景間隔（至少 0.1 秒），`--retain-observations` 指定保留數量（預設 1000）。超出上限會刪除舊紀錄與對應圖片，過期 ID 返回 404。服務重啟保留已完成紀錄，讀取時重新計算過期狀態；最新一筆不保證仍可用於操作。接收時間仍只代表 Mac 收到影格。

## 實驗：OmniParser 圖示偵測

`./iphone serve restart --api --parser-backend omniparser-hybrid` 切換至 Apple Vision 文字＋微軟 OmniParser V2 圖示偵測。`--parser-backend apple` 切回預設。混合模式使用獨立的 observations-omniparser 資料目錄，保留同一張圖的 OCR 與圖示框；圖示以 `type:icon`、center、box、confidence 回傳，description 為 null，尚未啟用圖示描述模型。偵測框不代表已驗證可點擊，可查看原圖後使用座標操作。

先執行 `tools/setup-omniparser.sh` 安裝依賴與權重（可用 HTTPS_PROXY 指定既有 HTTP 代理，uv 可用 UV_DEFAULT_INDEX 指定套件鏡像）。此實驗需 `runtime/omniparser-venv/bin/python`（Python 3.12、ultralytics、pillow）及 `runtime/omniparser-icon.pt`（微軟 OmniParser-v2.0 的 icon_detect/model.pt）。依賴與權重留在忽略的 runtime 目錄，不提交。該 YOLOv8 權重按官方模型卡採 AGPL-3.0；上游來源：https://huggingface.co/microsoft/OmniParser-v2.0 。目前使用 CPU，實際延遲需在目標 Mac 測量。

图示的文字字段为空，当前应使用确认后的 center 通过坐标点击，不把图示 ID 当作文字目标。`parser_backend` 标明结果后端，`icon_detection_seconds` 记录图示阶段耗时，原 `recognition_seconds` 保留文字 OCR 耗时。此机保存截图的单次测试在暖机后约 0.41～0.46 秒完成图示侦测；不是跨设备性能保证。模型冷启动可能较慢，后台首次完成观察前返回 503。
