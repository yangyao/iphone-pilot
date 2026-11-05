#!/usr/bin/env bash
# Build only. Flashing always requires an explicitly selected device.
set -euo pipefail
cd "$(dirname "$0")"
[[ -f hid_bridge/secrets.h ]] || { echo 'Copy hid_bridge/secrets.example.h to secrets.h and configure it first.'; exit 1; }
[[ "$PWD" != *' '* ]] || { echo 'Build path must not contain spaces.'; exit 1; }
FQBN='esp32:esp32:esp32s3:USBMode=default,CDCOnBoot=cdc,FlashSize=8M,PSRAM=opi,PartitionScheme=default_8MB'
DEFS="-DSTA_FROM_SECRETS=1 -include $PWD/hid_bridge/nimble_cfg.h"
arduino-cli compile -b "$FQBN" --build-property "compiler.cpp.extra_flags=$DEFS" --build-property "compiler.c.extra_flags=$DEFS" --output-dir build hid_bridge
