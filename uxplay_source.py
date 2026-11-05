#!/usr/bin/env python3
"""Run UxPlay with a dedicated JPEG pipe; logs remain on stderr."""
import os
import shutil
from pathlib import Path
import subprocess
import sys

binary = os.environ.get('UXPLAY_BIN') or shutil.which('uxplay')
if not binary:
    local = Path(__file__).parent / 'uxplay-src/build/uxplay'
    if local.is_file():
        binary = str(local)
if not binary:
    raise SystemExit('UxPlay not found: install it or set UXPLAY_BIN to the executable path.')
read_fd, write_fd = os.pipe()
sink = 'videoconvert ! jpegenc quality=80 ! fdsink sync=false fd=' + str(write_fd)
child = subprocess.Popen([str(binary), '-n', 'iPhone Agent Mac', '-nh', '-a',
                          '-vsync', 'no', '-vs', sink],
                         pass_fds=(write_fd,), stdout=sys.stderr)
os.close(write_fd)
try:
    with os.fdopen(read_fd, 'rb') as source:
        while True:
            chunk = source.read1(65536)
            if not chunk:
                break
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()
finally:
    child.terminate()
    child.wait()
