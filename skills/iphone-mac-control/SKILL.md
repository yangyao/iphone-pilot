---
name: iphone-mac-control
description: Operate a real iPhone from Mac using UxPlay frames, ESP32 absolute USB HID and AirType LAN Chinese text input. Use for phone app tasks with an already configured board and mirroring session.
---

# iPhone control from Mac

Resolve the repository root from this skill's location (`../..`). Read `docs/setup.md` for initial setup and command syntax. Do not reflash firmware or change network settings as a routine phone action.

## Fixed tools first

Use the installed MCP tools when they are actually present in the client's tool list. Otherwise use the repository's fixed executable `./iphone`: `observe`, `tap 12 --id p1 --wait 发布`, `tap --xy 378 296 --id s1`, `swipe 250 850 250 350 --id d1`, `type --id t1 --text-file runtime/draft.txt`, `image`. Resolve its absolute repository path if working elsewhere. Read [agent entrypoints](../../docs/agent-entrypoints.md) for registration, per-agent sessions and retries.

Do not generate ad hoc Python/urllib/heredoc HTTP scripts for routine observe/tap/input operations. The fixed CLI remembers the observed frame, builds requests, handles output and writes images. MCP/CLI return compact OCR by default; use full=true / global --full only when boxes are needed. A registered server is not proof the current session loaded its tools. If tools are not visible, use the fixed CLI now rather than repeatedly probing or inventing another transport.

Read each result before choosing the next action. Reuse an action's returned observation directly. CLI requests require a stable --id; after an ambiguous failure, retry with the SAME id and arguments. The CLI pins retries to the original payload even after a new observe. A new id means a new physical action, not a workaround for uncertainty.

### Await the complete result in one tool call

The CLI already waits synchronously for the API. When invoking it through `exec_command`, retain the default 10000 ms foreground wait or explicitly set `yield_time_ms: 10000`. Use 30000 for a known slower operation. Do not set 1000 ms, append `&`, or use nohup for ordinary phone actions. With `functions.exec`, retain its normal outer wait instead of adding a one-second yield pragma. Native MCP calls should likewise be awaited directly.

For example, invoke `exec_command` with `{"cmd":"iphone tap 12 --id task1-p1 --wait 发布","yield_time_ms":10000}`. A 2–3 second action returns its result in that same call; it does not wait the entire ten seconds. This command-tool foreground wait is separate from CLI `--timeout`, which controls how long the backend checks the phone's resulting state.

Only if the tool reports an actually running process after the foreground wait, poll that original session with a suitable wait (normally 10000 ms). Do not add a poll after a completed result and do not re-execute a phone mutation to fetch its output. For a still-running `functions.exec` cell, use its corresponding wait tool rather than starting another command.

## Observe and act

Prefer the persistent observation API/MCP described in [observation API](../../docs/observation-api.md). Call `iphone_observe()` for text and pixel centers; if MCP is unavailable, run `./iphone observe`. Start `bash tools/build-ocr.sh` once, then `python3 observe.py` if the service is absent. Default to text observations; request `include_image=true` with the previous `observation_id` (CLI: `./iphone image`) for ambiguous icons, stars, missing targets or unexpected results. This returns the same frame, not a different page. Do not pipe HTTP image base64 into the model as text.

Check `stale` and source age before acting. IDs and item numbers belong to one observation; obtain a fresh observation after page changes. OCR centers locate text, not guaranteed clickable controls: a Home Screen app label may require tapping its icon instead. OCR may misread characters; compare input against the known original and inspect again before editing suspected typos.

Observations also include `airtype`: when the current keyboard panel is readable, it contains endpoint/host/port and a six-digit `pairing_code` string. This is extracted from the same OCR, not another screenshot. It is not a verified connection or a saved Token. Missing/ambiguous/stale codes are null; never substitute a historical code. Prefer `iphone_type` to orchestrate connection and insertion rather than manually assembling calls.

The keyboard's tiny footer may require the built-in local OCR crop; valid IP and .local URLs can coexist. Do not guess punctuation/digits in an unreadable address or lower code confidence to force input. If discovery fails, inspect the current image and use the documented manual client fallback. For wait_for_text, use text actually displayed in the phone's language: this AirType setup shows 配对码 / 网页输入, not necessarily “Web”.

The underlying receiver is at `http://127.0.0.1:18765/status`; its `file` is a fallback for image viewing if OCR is unavailable. If no receiver exists, run `python3 receiver.py --directory runtime -- python3 uxplay_source.py` once, not a screenshot subprocess for every observation. The phone must mirror to **iPhone Agent Mac**, not the board. Identify the board IP from the user's setup, never hardcode another user's address.

Use `python3 hardware/tools/hybrid_control.py --host BOARD_IP status` to confirm the board is reachable. For clicks use `tap X Y --width W --height H`; for drags use `swipe X1 Y1 X2 Y2 --width W --height H --ms 400`. Coordinates are actual screenshot pixels, not 0–1000 prompt units. Do not run relative calibration or the old `esp32agent --aim` path for these absolute commands.

Prefer `iphone_act` for configured deployments; read [action API](../../docs/action-api.md) for setup and argument details. It combines tap/swipe, a bounded stable-screen wait and new OCR in one call. Supply the current observation_id and a unique request_id; tap by target_id OR pixel x/y, swipe by x/y/end_x/end_y. Choose specific expected page text via wait_for_text when useful. It rejects changed OCR layouts before sending and serializes actions with AirType. CLI commands above remain a fallback, not an additional step after a successful API action.

Use its returned fresh observation and IDs directly for the next decision. Do not call iphone_observe again merely because an action happened; request another observation only if the result is missing, stale, incomplete for the next target or otherwise uncertain.

Read `wait.status` separately from `accepted`: condition_met means expected text on a changed stable screen; changed means only stable OCR content change; timeout/no_new_frame/observation_unavailable do not confirm completion. A newly received frame alone is not success. Reuse the SAME request_id and action parameters after timeout; a durable claim prevents re-execution, even after restart. Never issue a new ID to blindly repeat publishing. The result includes observation, added/removed items and timings; changes_only=true omits full items, include_image=true attaches the result frame. Removed IDs belong to the old frame; obtain full current OCR before targeting an unchanged item omitted from a delta. Layout checks cannot detect every non-text overlay or frozen replay.

Observe after each navigation before using coordinates on the new page. Batch only actions whose targets and effects are already known; do not blind-tap through dialogs or across unseen pages. Transport `ok` is not evidence of success. Verify submission using visible success text or the resulting record, and avoid duplicate submissions after ambiguous results.

The receiver's sequence and arrival time show frame reception, not semantic freshness or measured capture latency. If actions show no effect, compare frames and check USB/status/source.log. Replan after repeated misses; stop after two failed stream recovery attempts and report the actual state. Do not repeatedly restart mirroring without progress.

## Home and input

Consumer Home did not work on the test phone. Return Home with a bottom-edge upward swipe scaled to the actual screenshot. `command 'key cmd+space'` can open Spotlight; verify before typing.

Prefer **AirType LAN insertion** for Chinese or other non-ASCII text when installed. Read [Chinese input](../../docs/chinese-input.md) for setup, commands and recovery. Focus the intended field and show AirType's Web Access keyboard, then call `iphone_type(text=..., request_id=...)`. It reads the fresh OCR address/code, reuses or pairs the session, inserts once and checks OCR. Use a unique request_id for each intended insertion; reuse the SAME id and text on timeout/retry. Never switch IDs to bypass an uncertain result: inspect the field first. The durable local journal prevents replay of an attempted command even after service restart. `accepted:true` is not proof of correct focus; `verification.status:text_detected_after_input` is only OCR evidence, and `not_confirmed` does not mean insertion failed.

If MCP is unavailable, use `./iphone type --id t1 --text-file runtime/draft.txt`; HTTP is a backend implementation detail, not a reason to generate a request script. If automatic discovery is ambiguous or unavailable, inspect the current panel and fall back to `tools/airtype.py pair --code CURRENT_CODE` and `type --text-file runtime/draft.txt` (both with `--host PHONE_IP`). No cloud or clipboard transfer is needed. Never use a historical pairing code; reopening the keyboard may change it. Saved sessions live only in ignored runtime files. Input is separate from submission; the wrapper never clicks Send/Publish.

After insertion, compare the visible text with the intended original before publishing. Pairing/status/HTTP success alone is insufficient. On timeout, inspect before resending to avoid duplicates. Do not type test phrases into a real answer or message draft and submit them. Request newline permission in the CLI only for an observed multiline editor. Remoboard failed cross-app acceptance in this environment and is not the default backend.

Fallback when AirType is unavailable: `command 'type ...'` sends ASCII key events. Select **简体拼音 / 全键盘**, send short pinyin phrases, inspect and select the correct candidates. A nine-key layout can produce wrong input. Trailing spaces in `type` are trimmed; send `command 'key space'` separately or tap the candidate. `cmd+a` may not select all during active pinyin composition: commit or clear composition first. Do not silently substitute English for requested Chinese.

Do the task the user authorized. For product questions, ask for missing first-hand facts when needed; do not infer authenticity or specialized efficacy from a general positive purchase experience. Read `docs/validation.md` when compatibility or performance matters.
