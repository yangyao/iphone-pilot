#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ROOT/runtime/bin"
swiftc -O "$ROOT/tools/screen_ocr.swift" -o "$ROOT/runtime/bin/screen-ocr"
echo "$ROOT/runtime/bin/screen-ocr"
