# 安装、连接与刷机

## Mac 接收端

需要 Python 3.10+、Git、Homebrew、CMake、pkg-config 与 GStreamer。接收程序和控制客户端只使用 Python 标准库；UxPlay 是独立外部依赖。

```sh
brew install cmake pkgconf gstreamer libplist openssl@3
export PKG_CONFIG_PATH="$(brew --prefix openssl@3)/lib/pkgconfig:${PKG_CONFIG_PATH:-}"
git clone https://github.com/FDH2/UxPlay.git uxplay-src
git -C uxplay-src checkout 7aaad6222c2e136723e6e3d25c3be784260c577d
cmake -S uxplay-src -B uxplay-src/build
cmake --build uxplay-src/build -j 4
```

这里的 clone 是下载构建依赖，不会把本项目变成 Fork。也可自行安装 UxPlay，并通过 PATH 或 `UXPLAY_BIN` 指定可执行文件。

启动持续接收：

```sh
python3 receiver.py --directory runtime -- python3 uxplay_source.py
```

iPhone 在控制中心打开“屏幕镜像”，选择 **iPhone Agent Mac**。三台设备在同一局域网，允许 UxPlay 的本地网络访问与入站连接。不要为了控制而把 Mac 切到板子的热点，否则可能失去互联网。

查看 `http://127.0.0.1:18765/status` 和 `runtime/latest.jpg`；也可读 `/frame.jpg`。这个 HTTP 服务只监听本机，没有预览窗口。Ctrl-C 停止。截图和日志可能包含个人信息，默认不纳入版本控制。

## 固件构建

需要 Arduino CLI；测试版本为 ESP32 Arduino core **3.3.2**、NimBLE-Arduino **2.5.1**。本配置针对支持原生 USB 的 ESP32-S3、8 MB PSRAM、至少 8 MB Flash；普通 ESP32 不可直接替代。测试板实际有 16 MB Flash，使用 8 MB 分区布局。

```sh
brew install arduino-cli
arduino-cli core update-index --additional-urls https://espressif.github.io/arduino-esp32/package_esp32_index.json
arduino-cli core install esp32:esp32@3.3.2 --additional-urls https://espressif.github.io/arduino-esp32/package_esp32_index.json
arduino-cli lib install 'NimBLE-Arduino@2.5.1'
cp hardware/hid_bridge/secrets.example.h hardware/hid_bridge/secrets.h
python3 -c 'import secrets; print(secrets.token_hex(24))'
```

编辑本地 `secrets.h`：填写 Wi-Fi SSID、密码，并把随机十六进制字符串填入 `HYBRID_HTTP_TOKEN`。配置不会自动从环境变量读取。不要提交 `secrets.h`，也不要公开包含这些值的编译产物。

```sh
bash hardware/build.sh
```

该脚本只构建，不自动选择或刷写任何设备。

## 明确选择设备后刷写

把板子接到 Mac 的原生 USB 接口，查看 `arduino-cli board list`。如无法进入下载模式，按住 BOOT、短按 RESET、再放开 BOOT。不要猜测多个设备中的目标。

以下 `PORT` 是占位符，改成自己确认的端口。先读芯片和 Flash 信息确认目标，保存完整备份，再刷写。Flash 容量必须以读取结果为准，示例备份大小只适用于 16 MB 板。

```sh
uv run --with 'esptool==4.8.1' python -m esptool --chip esp32s3 --port PORT --after no_reset flash_id
mkdir -p backups
uv run --with 'esptool==4.8.1' python -m esptool --chip esp32s3 --port PORT --baud 460800 --after no_reset read_flash 0x0 0x1000000 backups/original-16mb.bin
uv run --with 'esptool==4.8.1' python -m esptool --chip esp32s3 --port PORT --baud 460800 --after hard_reset write_flash -z 0x0 hardware/build/hid_bridge.ino.merged.bin
```

`uv` 是可选 Python 工具；也可在独立虚拟环境安装 `esptool==4.8.1`。备份包括 Wi-Fi 凭据与配对信息，不要上传。写入后应看到哈希校验成功。固件不修改 eFuse；兼容板通常可回刷备份，但不能保证任意型号或供电条件都安全。

## 接回手机并检查

将板子 USB 接回 iPhone 的数据接口。开启“设置 → 辅助功能 → 触控 → 辅助触控”，保持手机解锁。Lightning 设备可能需要支持 USB 外设的数据适配器与供电，尚未在本项目验证。

在路由器 DHCP 列表或板子的 USB 串口启动日志找到板子 IP；建议给板子保留 DHCP 地址。新路径无需蓝牙配对，也不要求 iPhone 安装原作者 App。

```sh
python3 hardware/tools/hybrid_control.py --host BOARD_IP status
```

确认 USB 已就绪，Mac 已收到手机画面。先在无敏感操作的页面验证一处点击、拖动与返回手势，再进入实际任务。固件仍保留旧 BLE/App 控制功能，不要同时运行两个控制者；旧客户端也不会自动改用绝对坐标。

## 接口与中文输入

板子监听 HTTP 8766；`GET /status`、`POST /command` 都要求 Bearer Token。客户端默认从本地 `hardware/hid_bridge/secrets.h` 读取 Token。不使用代理。HTTP 是明文，只用于可信局域网，不要映射公网端口。

- `tap` / `move`：两个截图像素坐标，必须同时给 `--width`、`--height`。
- `swipe`：起止四个坐标，`--ms` 范围 60–2000 ms。同一点拖动可测试长按。
- 原始指令 `absmove X Y`、`abstap X Y`、`absdrag X1 Y1 X2 Y2 MS` 使用 0–32767 HID 坐标。
- `command 'type ...'` 是 ASCII 键盘输入，不是 Unicode 粘贴。中文需启用简体拼音全键盘，发送拼音后观察并点击正确候选词。
- 固件会 trim 命令末尾空白，不能依赖 `type` 尾部空格提交拼音；可单独发送 `command 'key space'` 或点击候选词。
- 消费者 Home 键没有在测试手机上生效。可从截图底部向上拖动返回桌面；坐标需按实际截图选择。

接口返回 `ok` 只代表执行请求，不代表页面已变化或内容已发布。每次导航和提交都要核对实际画面。
