#!/usr/bin/env python3
"""Cache a persistent MJPEG source for agents. Python standard library only."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Frames:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.sequence = 0
        self.updated = None
        self.frame = None
        self.running = False
        self.error = None

    def publish(self, data):
        with self.lock:
            temp = self.directory / 'latest.tmp'
            temp.write_bytes(data)
            os.replace(temp, self.directory / 'latest.jpg')
            self.frame = data
            self.sequence += 1
            self.updated = time.time()
            self.error = None

    def status(self):
        with self.lock:
            return dict(sequence=self.sequence, received_at=self.updated,
                        age_seconds=None if self.updated is None else time.time() - self.updated,
                        source_running=self.running, error=self.error,
                        file=str((self.directory / 'latest.jpg').resolve()))


def jpeg_frames(stream):
    """Ignore CLI JSON preamble; retain complete JPEGs across arbitrary reads."""
    buffer = bytearray()
    while True:
        chunk = stream.read1(65536)
        if not chunk:
            return
        buffer.extend(chunk)
        while True:
            start = buffer.find(b'\xff\xd8')
            if start < 0:
                buffer[:] = buffer[-1:]
                break
            if start:
                del buffer[:start]
            end = buffer.find(b'\xff\xd9', 2)
            if end < 0:
                if len(buffer) > 32 * 1024 * 1024:
                    raise ValueError('JPEG exceeds 32 MiB')
                break
            yield bytes(buffer[:end + 2])
            del buffer[:end + 2]


def serve(args):
    frames = Frames(args.directory)
    stop = threading.Event()
    process = [None]

    def source():
        while not stop.is_set():
            try:
                with open(frames.directory / 'source.log', 'ab') as log:
                    child = subprocess.Popen(args.source, stdout=subprocess.PIPE, stderr=log)
                    process[0] = child
                    frames.running = True
                    for frame in jpeg_frames(child.stdout):
                        frames.publish(frame)
                    code = child.wait()
                    frames.error = f'source exited: {code}; see source.log'
            except Exception as exc:
                frames.error = str(exc)
            finally:
                frames.running = False
            stop.wait(2)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/status':
                body, mime, code = json.dumps(frames.status()).encode(), 'application/json', 200
            elif self.path == '/frame.jpg':
                with frames.lock:
                    body = frames.frame
                    sequence, received_at = frames.sequence, frames.updated
                    running = frames.running
                mime, code = 'image/jpeg', 200 if body else 503
                body = body or b'No frame received yet'
            else:
                body, mime, code = b'Use /status or /frame.jpg', 'text/plain', 404
            self.send_response(code)
            self.send_header('Content-Type', mime)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            if self.path == '/frame.jpg' and code == 200:
                self.send_header('X-Frame-Sequence', str(sequence))
                self.send_header('X-Frame-Received-At', str(received_at))
                self.send_header('X-Source-Running', str(running).lower())
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)

    def shutdown(*_):
        stop.set()
        if process[0] and process[0].poll() is None:
            process[0].terminate()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    threading.Thread(target=source, daemon=True).start()
    print(f'http://127.0.0.1:{args.port}/status', flush=True)
    try:
        server.serve_forever()
    finally:
        shutdown()
        server.server_close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', default='./runtime')
    parser.add_argument('--port', type=int, default=18765)
    parser.add_argument('source', nargs=argparse.REMAINDER,
                        help='command producing concatenated JPEGs (after --)')
    options = parser.parse_args()
    if options.source[:1] == ['--']:
        options.source.pop(0)
    if not options.source:
        options.source = ['npx', '-y', 'esp32agent', 'screencapture', '--format', 'mjpeg']
    serve(options)
