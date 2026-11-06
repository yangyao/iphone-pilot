#!/bin/sh
# Install the optional experimental detector into ignored runtime storage.
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
uv venv --python 3.12 --allow-existing runtime/omniparser-venv
UV_HTTP_TIMEOUT=600 uv pip install --python runtime/omniparser-venv/bin/python \
    'torch==2.6.0' 'torchvision==0.21.0' 'ultralytics==8.3.70' 'pillow==11.1.0' 'numpy==2.1.1'
curl --fail --location --retry 3 \
    https://huggingface.co/microsoft/OmniParser-v2.0/resolve/main/icon_detect/model.pt \
    -o runtime/omniparser-icon.pt.download
mv runtime/omniparser-icon.pt.download runtime/omniparser-icon.pt
