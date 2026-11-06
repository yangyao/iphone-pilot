# 在当前 App 输入整段中文

首选 **AirType → 局域网原文传输 → 键盘扩展插入**。ESP32 继续负责点击和滑动，中文正文不再经过 HID 拼音转换，也不依赖苹果通用剪贴板或第三方云同步。

这是一个额外安装的第三方键盘，不是固件自带的中文能力。未安装 AirType 时，原有 HID ASCII／系统拼音输入仍可作为备选。

## 一次设置

1. 安装 [AirType - Type, over the air](https://apps.apple.com/cn/app/airtype-type-over-the-air/id922932291)，开发者 invivo，App ID `922932291`。不要混淆为同名语音输入产品。
2. 在 iPhone“设置 → 通用 → 键盘 → 键盘”添加 AirType，允许完全访问。
3. 在 AirType 主 App 开启本地网络连接，允许系统“本地网络”权限。按其引导在测试页确认输入。
4. 手机和 Mac 连接同一可信局域网。输入时切换到 AirType，保持其 **网页输入 / Web Access** 面板可见。

商店说明：无需云账号，文字不上传到其自有云服务；局域网使用明文 HTTP。需要完全访问和本地网络权限，密码框、某些电话输入框以及拒绝第三方键盘的 App 不适用。这里描述的是开发者声明及本机测试，不是完整网络审计。

## 一次工具调用完成输入

已配置 [观察 API / MCP](observation-api.md) 时，Agent 先点击目标输入框并展开 AirType **Web Access**，然后调用：

```text
iphone_type(text="这是一段完整的中文正文。", request_id="jd-answer-001")
```

工具从最新 OCR 读取键盘面板上的地址、端口与当前六位码，优先复用有效会话；必要时自动配对，再检查一次连接信息未变化，只发送一次原文，最后尝试用 OCR 检查新文字。Agent 不必手工抄配对码，也不必依次调用 pair/status/type。

`request_id` 是本次输入的唯一编号，可由 Agent 自行生成。超时或重试时必须保留相同编号和相同正文；服务使用忽略目录里的 SQLite 日志记住已尝试的输入，重启后也不自动重发。换一条新输入才换编号。不要为绕过“结果不确定”而生成新编号重发。

多段文章仅在已确认多行编辑器时增加 `allow_newlines=true`。单次支持最多 20,000 个字符，但实际可输入长度仍受目标 App 限制，超长文章尚未实机验证。服务只插入正文，不点击发布或发送。

返回值中的 `accepted:true` 只是键盘 HTTP 确认。`verification.status:text_detected_after_input` 表示 OCR 在后续画面读到了原文（忽略空白和 Unicode 宽度差异），且输入前没有读到相同文字；它不证明焦点正确，也不代表已经发布。长文没有完整显示、OCR 误字或画面没及时更新时会返回 `not_confirmed`，即使文字可能已成功输入，此时查看输入框，不要盲目重发。`status:uncertain` 表示发送结果不明，也必须先检查。

若键盘未展开、画面过期、地址或配对码不清、出现多个候选码，工具停止，不猜数字。不支持自动从任意页面找输入框、切换第三方键盘或关闭配对要求；这些仍由 Agent 根据页面完成。读取到的 IP 不可达时也不会扫描网络，需要核对手机 Wi-Fi 地址。

## HTTP 接口

观察服务默认也提供 `POST http://127.0.0.1:18766/input`，JSON 参数为 `text`、`request_id`、可选 `allow_newlines`。仅接受本地客户端的 `application/json` 请求，拒绝带 Origin 的浏览器请求。可用 `python3 observe.py --disable-input` 运行只读观察服务。

```python
import json
from urllib.request import ProxyHandler, Request, build_opener

payload = {"text": "完整中文正文。", "request_id": "draft-001"}
request = Request("http://127.0.0.1:18766/input",
                  data=json.dumps(payload, ensure_ascii=False).encode(),
                  headers={"Content-Type": "application/json"})
with build_opener(ProxyHandler({})).open(request, timeout=150) as response:
    print(json.load(response))
```

调用不包含手机地址和配对码，这两项从当前画面读取；Token 仍保存在私有的本地会话文件，不返回给模型。防重发日志只记录请求摘要、命令编号与结果，不保存正文。此日志是防止意外重复输入的措施，不是 AirType 服务端“恰好一次”交付保证；如果服务在落盘后、发送前崩溃，也会保守返回不确定而不补发。

## 手动配对与输入（回退方式）

从键盘面板读取当前地址、端口和六位码。默认端口通常是 8307，但以手机显示为准；不要把别人的 IP 或会话码写进 Skill。

```sh
python3 tools/airtype.py --host PHONE_IP pair --code CURRENT_CODE
python3 tools/airtype.py --host PHONE_IP status
python3 tools/airtype.py --host PHONE_IP type --text '你好，世界！中文与ABC123。'
```

长文本放在本地 UTF-8 文件，避免 shell 对引号、反引号和 `$()` 的解释：

```sh
python3 tools/airtype.py --host PHONE_IP type --text-file runtime/draft.txt
```

在已经确认的多行编辑器中，若文件包含换行，再加 `--allow-newlines`。不要为了绕过检查，在搜索框或聊天发送栏盲目开启。

客户端只插入文字，不调用 newline、删除或提交。插入后仍需核对全文；发布、发送等由 Agent 按任务授权另行点击。配对 Token 保存在忽略的 `runtime/airtype-session.json`，文件以仅当前用户可读写的权限创建，不输出 Token。不同手机可用 `--session runtime/other-phone.json` 分开保存。

## 切换与恢复

- 本次实测切换 App／重新展开键盘时会出现新配对码。不要假定旧码或已保存 Token 永久有效。先尝试 `status`，失效时读取当前码重新配对。
- 手机键盘显示的地址可能不是 Mac 可达的 Wi-Fi 地址。若有多个网络接口，确认手机当前 Wi-Fi IP，再测试该 IP 的相同端口；不要修改 Mac 网络来迁就错误地址。
- `status` 里的 connected、HTTP `ok`、配对成功，都不单独证明文字写进了目标框。必须从实时截图核对。
- 超时可能发生在文字已插入之后。客户端不会自动重发；先看输入框再决定补输。每次插入发送 UUID，但没有验证服务端对相同 UUID 的去重保证，不依赖它自动重试。
- 连接依赖键盘面板可见。隐藏、切换键盘或锁屏后，不要继续向旧会话盲发。恢复面板、读当前码、确认焦点后重连。
- 接口为从 AirType 1.1 网页客户端观察得到，未来版本可能变化。若接口不兼容，先用它自带网页对照测试，勿连续重放输入。

## 给 Agent 的执行顺序

确认实时画面 → 点击目标输入框 → 选择 AirType Web Access → `iphone_type` 一次输入并初步检查 → 必要时看原图核对 → 按任务授权提交 → 核验提交结果。自动读取连接信息失败时，才使用手动客户端。

若输入失败，保留原文，不立即转成英文或发布测试内容。必要时退回系统拼音短词输入并逐次确认候选。

## 实测范围

已验证 AirType 测试页、苹果备忘录两次连续输入（含换行）、京东买家问答整段中文输入并发布成功。文字包括中文、标点、ASCII 和数字。尚未系统验证超长文章、复杂 emoji、多机型、长时间重连或各种 App。

新的一次调用封装已通过模拟键盘的配对、复用会话、重新鉴权、Unicode 输入、OCR 检查、超时防重发、重启恢复和 MCP 协议测试；尚待在展开的真实 AirType 键盘上完成端到端验收。上述既有实机结果属于原始 AirType 客户端，不应当作新封装已经实机通过。

Remoboard 曾在自身测试页成功，随后在京东和备忘录出现“已配对但没有写入”的现象，根因未确定，因此当前不作为默认后端。不能据此推断所有远程键盘都不可靠。
