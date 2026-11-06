#!/bin/sh
# Restart the UxPlay receiver and the observe/action API.
# Usage: tools/serve.sh [BOARD_IP]
#   BOARD_IP defaults to runtime/board.json (see: ./iphone board discover --save)
# Environment:
#   UXPLAY_BIN  path to the uxplay binary (default: sibling iphone-agent-skill build)
#   SECRETS     board secrets.h path (default: sibling esp32-hybrid firmware)
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
UXPLAY_BIN=${UXPLAY_BIN:-/Users/jiao/www/play/esp32/iphone-agent-skill/uxplay-src/build/uxplay}
SECRETS=${SECRETS:-/Users/jiao/www/play/esp32/esp32-hybrid/hardware/hid_bridge/secrets.h}
BOARD=${1:-$(python3 -c "import json; print(json.load(open('runtime/board.json'))['host'])" 2>/dev/null || true)}
[ -n "$BOARD" ] || { echo "board IP unknown; run: ./iphone board discover --save (or pass it as argument 1)"; exit 1; }
[ -x "$UXPLAY_BIN" ] || { echo "uxplay binary missing at $UXPLAY_BIN (set UXPLAY_BIN)"; exit 1; }
[ -f "$SECRETS" ] || { echo "secrets.h missing at $SECRETS (set SECRETS)"; exit 1; }

pkill -f 'receiver.py --directory' 2>/dev/null || true
pkill -f 'observe.py --board-host' 2>/dev/null || true
sleep 1

nohup env UXPLAY_BIN="$UXPLAY_BIN" python3 receiver.py --directory runtime -- python3 uxplay_source.py \
    > runtime/receiver.log 2>&1 &
sleep 3
nohup python3 observe.py --board-host "$BOARD" --board-config "$SECRETS" \
    > runtime/observe.log 2>&1 &
sleep 3
curl -s -m 5 http://127.0.0.1:18765/status; echo
curl -s -m 5 http://127.0.0.1:18766/health; echo
