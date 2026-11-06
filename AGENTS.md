# Agent phone operations

For phone tasks, read `skills/iphone-mac-control/SKILL.md`.

- Prefer `iphone_observe`, `iphone_act`, `iphone_type` if loaded as MCP tools.
- Otherwise call the fixed `./iphone` executable. Do not write one-off urllib/Python HTTP scripts for ordinary phone actions.
- Run phone CLI calls in the foreground. For `exec_command`, use its default 10000 ms wait or explicitly set `yield_time_ms=10000`; use 30000 for a known slower call. Do not set 1000 ms for ordinary phone commands. They return immediately when complete; this is not a fixed sleep. Poll the original session only if it is actually still running after that wait, and never reissue a mutation just to obtain its result.
- `./iphone observe` returns text/centers. `./iphone tap 12 --id p1 --wait 发布` acts using the last observed frame. `./iphone image` writes the same-frame image and returns its path.
- Reuse returned observations; do not add a redundant observe after every successful action.
- Preserve request IDs/arguments on retry; inspect uncertain outcomes before starting another action. Never repeat a publication simply because an HTTP response was lost.
- Do not run real phone actions for source-code tests unless the user requested a live phone task. Use simulated fixtures for checks.
