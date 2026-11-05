---
name: iphone-mac-control
description: Operate a real iPhone from Mac using this repository's UxPlay frame cache and ESP32 absolute USB HID over authenticated LAN commands. Use for phone app tasks with an already configured board and mirroring session.
---

# iPhone control from Mac

Resolve the repository root from this skill's location (`../..`). Read `docs/setup.md` for initial setup and command syntax. Do not reflash firmware or change network settings as a routine phone action.

## Observe and act

Use the running receiver at `http://127.0.0.1:18765/status`; read its reported `file` using an image viewer. If no receiver exists, run `python3 receiver.py --directory runtime -- python3 uxplay_source.py` once, not a screenshot subprocess for every observation. The phone must mirror to **iPhone Agent Mac**, not the board. Identify the board IP from the user's setup, never hardcode another user's address.

Use `python3 hardware/tools/hybrid_control.py --host BOARD_IP status` to confirm the board is reachable. For clicks use `tap X Y --width W --height H`; for drags use `swipe X1 Y1 X2 Y2 --width W --height H --ms 400`. Coordinates are actual screenshot pixels, not 0–1000 prompt units. Do not run relative calibration or the old `esp32agent --aim` path for these absolute commands.

Observe after each navigation before using coordinates on the new page. Batch only actions whose targets and effects are already known; do not blind-tap through dialogs or across unseen pages. Transport `ok` is not evidence of success. Verify submission using visible success text or the resulting record, and avoid duplicate submissions after ambiguous results.

The receiver's sequence and arrival time show frame reception, not semantic freshness or measured capture latency. If actions show no effect, compare frames and check USB/status/source.log. Replan after repeated misses; stop after two failed stream recovery attempts and report the actual state. Do not repeatedly restart mirroring without progress.

## Home and input

Consumer Home did not work on the test phone. Return Home with a bottom-edge upward swipe scaled to the actual screenshot. `command 'key cmd+space'` can open Spotlight; verify before typing.

`command 'type ...'` sends ASCII key events. For Chinese, select **简体拼音 / 全键盘** (long-press the keyboard globe to select), send a short pinyin phrase, then inspect and select the correct candidate. A nine-key layout can produce wrong input. Trailing spaces in `type` are trimmed; send `command 'key space'` separately or tap the candidate. `cmd+a` may not select all while a pinyin composition is active: commit or clear that composition first. Review final Chinese text before publishing.

Do the task the user authorized. For product questions, ask for missing first-hand facts when needed; do not infer authenticity or specialized efficacy from a general positive purchase experience. Read `docs/validation.md` when compatibility or performance matters.
