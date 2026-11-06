# Agent phone operations

For phone tasks, read `skills/iphone-mac-control/SKILL.md`.

- Prefer `iphone_observe`, `iphone_act`, `iphone_type` if loaded as MCP tools.
- Otherwise call the fixed `./iphone` executable. Do not write one-off urllib/Python HTTP scripts for ordinary phone actions.
- `./iphone observe` returns text/centers. `./iphone tap 12 --id p1 --wait 发布` acts using the last observed frame. `./iphone image` writes the same-frame image and returns its path.
- Reuse returned observations; do not add a redundant observe after every successful action.
- Preserve request IDs/arguments on retry; inspect uncertain outcomes before starting another action. Never repeat a publication simply because an HTTP response was lost.
- Do not run real phone actions for source-code tests unless the user requested a live phone task. Use simulated fixtures for checks.
