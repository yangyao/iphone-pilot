# Sources and licenses

This repository was initialized independently, without importing upstream Git history. It contains adapted third-party source; it is not a claim of wholly original authorship.

## ESP32 iPhone Agent

- Source: https://github.com/onehumanbeing/esp32-iphone-agent
- Base: release-1.0, commit `78ed66e1838e7f5650e6f8caba6a8e8b03cd3f6d`.
- Copyright (c) 2026 Henry Yuan. Original MIT notice: [licenses/esp32-iphone-agent-MIT.txt](licenses/esp32-iphone-agent-MIT.txt).
- Adapted files: `hardware/hid_bridge/`, except newly added `absolute_pointer.h`. Source comments and nested licenses are preserved.
- Changes made for this project on 2026-11-05: absolute HID report ID 7 before the relative mouse collection; `absmove`, `abstap`, `absdrag`; authenticated local HTTP port 8766; version suffix `hybrid2`. Python client, continuous capture wrapper and tests accompany these changes.
- Firmware retains upstream BLE, board AirPlay receiver, Wi-Fi video, ownership arbitration and OTA. These retained components are not all regression-tested; Mac direct mirroring bypasses the board video path.

## UxPlay, RPiPlay and playfair

The inherited `hardware/hid_bridge/src/airplay/` is a GPL-3.0 port of UxPlay, itself derived from RPiPlay and playfair. File headers preserve author notices, including Juho Vähä-Herttua, Jaslo Ziska, F. Duncanh, dsafa22 and EstebanKubata. See that directory's `LICENSE-GPL.md` and `playfair/LICENSE.md`. The combined firmware is GPL-3.0.

Mac reception uses a separately installed UxPlay executable (https://github.com/FDH2/UxPlay). Local acceptance used commit `7aaad6222c2e136723e6e3d25c3be784260c577d`. Its source/binaries are not bundled here. The Python wrapper independently launches it and caches JPEG frames.

## iPhone Use

https://github.com/xhoantran/iphone-use was studied as a reference for agent-driven real iPhone interaction. No Swift code or app binary from that project is included. It is not a runtime dependency.

## Build dependencies

AirType (invivo, App Store ID `922932291`) is an optional separately installed third-party iOS keyboard for direct LAN text insertion. No AirType app, binary or source is redistributed. `tools/airtype.py` is an independent standard-library client based on the HTTP requests observed in its served web interface, tested with version 1.1. This project is not affiliated with its developer. See `docs/chinese-input.md` for permissions and limitations.

Arduino ESP32 core (tested 3.3.2), NimBLE-Arduino (tested 2.5.1), GStreamer and other UxPlay dependencies retain their own licenses. Install them from their upstream distributions. This source repository does not bundle their SDKs or binaries.

New project contributions: Copyright (c) 2026 yangyao, GPL-3.0. Existing notices and separately granted MIT permissions remain intact.

## Optional OmniParser icon detection

The experimental hybrid backend downloads Microsoft OmniParser-v2.0's YOLOv8 `icon_detect/model.pt` and uses Ultralytics for local inference. The official model card identifies this detector as AGPL-3.0; the model is not bundled in this repository. The setup script installs dependencies into ignored runtime storage. Apple Vision continues to provide text OCR; no icon caption model is enabled in this version.

Source and model license: https://huggingface.co/microsoft/OmniParser-v2.0 and https://github.com/microsoft/OmniParser .
