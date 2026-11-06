#!/usr/bin/env python3
"""Run UxPlay with a dedicated JPEG pipe; logs remain on stderr."""
import os
import shutil
from pathlib import Path
import subprocess
import sys

def main():
    # One receiver name per login, including across different repository copies.
    import fcntl
    import signal
    import tempfile
    lock_path = Path(tempfile.gettempdir()) / f'iphone-pilot-uxplay-{os.getuid()}.lock'
    lock = lock_path.open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise SystemExit('Another iPhone Pilot UxPlay receiver already owns the lock')
    binary = os.environ.get('UXPLAY_BIN') or shutil.which('uxplay')
    if not binary:
        lock.close()
        raise SystemExit('UxPlay not found: set UXPLAY_BIN through ./iphone serve start --uxplay')
    read_fd, write_fd = os.pipe()
    sink = 'videoconvert ! jpegenc quality=80 ! fdsink sync=false fd=' + str(write_fd)
    child = None

    def stop(*_):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        child = subprocess.Popen([str(binary), '-n', 'iPhone Agent Mac', '-nh', '-a',
                                  '-vsync', 'no', '-vs', sink],
                                 pass_fds=(write_fd,), stdout=sys.stderr)
        os.close(write_fd)
        write_fd = None
        with os.fdopen(read_fd, 'rb') as source:
            while True:
                chunk = source.read1(65536)
                if not chunk:
                    break
                sys.stdout.buffer.write(chunk)
                sys.stdout.buffer.flush()
    finally:
        # Avoid a second signal interrupting cleanup while launchd stops the job.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        if child is not None:
            child.terminate()
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        if write_fd is not None:
            os.close(write_fd)
        lock.close()


if __name__ == '__main__':
    main()
