#!/usr/bin/env python3
"""Persistent local OCR observations and optional one-call AirType insertion."""
import argparse
import base64
from collections import OrderedDict
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import selectors
import subprocess
import threading
import time
from urllib.parse import parse_qs, urlsplit
from urllib.request import ProxyHandler, build_opener
from tools.airtype_input import AirTypeInput, InputError

urlopen = build_opener(ProxyHandler({})).open


class ObservationError(Exception):
    def __init__(self, message, status=503):
        super().__init__(message)
        self.status = status


class OCRWorker:
    def __init__(self, binary, timeout=30):
        self.binary, self.timeout, self.process = str(binary), timeout, None

    def close(self):
        if self.process:
            self.process.kill() if self.process.poll() is None else None
            self.process.wait()
            self.process.stdin.close()
            self.process.stdout.close()
            self.process = None

    def __call__(self, image):
        try:
            if not self.process or self.process.poll() is not None:
                self.close()
                self.process = subprocess.Popen([self.binary, '--worker'],
                                                stdin=subprocess.PIPE, stdout=subprocess.PIPE)
            self.process.stdin.write(base64.b64encode(image) + b'\n')
            self.process.stdin.flush()
            with selectors.DefaultSelector() as selector:
                selector.register(self.process.stdout, selectors.EVENT_READ)
                if not selector.select(self.timeout):
                    raise TimeoutError('OCR worker timed out')
            result = json.loads(self.process.stdout.readline())
            if 'error' in result:
                raise ValueError(result['error'])
            return result
        except Exception as exc:
            self.close()
            raise ObservationError(f'OCR failed: {exc}') from exc


def fetch_frame(source):
    with urlopen(source + '/frame.jpg', timeout=5) as response:
        image, headers = response.read(), response.headers
    if headers.get('X-Frame-Received-At'):
        metadata = dict(received_at=float(headers['X-Frame-Received-At']),
                        sequence=int(headers['X-Frame-Sequence']),
                        source_running=headers.get('X-Source-Running') == 'true',
                        metadata_exact=True)
    else:
        # Compatibility with a receiver already running before frame headers existed.
        with urlopen(source + '/status', timeout=5) as response:
            status = json.load(response)
        metadata = {key: status.get(key) for key in
                    ('received_at', 'sequence', 'source_running')}
        metadata['metadata_exact'] = False
    return image, metadata


class Observer:
    def __init__(self, fetch, ocr, max_age=5, cache_size=8):
        self.fetch, self.ocr = fetch, ocr
        self.max_age, self.cache_size = max_age, cache_size
        self.cache, self.lock = OrderedDict(), threading.Lock()

    def observe(self, include_image=False, observation_id=None):
        with self.lock:
            cached = False
            if observation_id:
                if observation_id not in self.cache:
                    raise ObservationError('Observation expired; request a fresh observation', 404)
                image, metadata, result = self.cache[observation_id]
                cached = True
            else:
                image, metadata = self.fetch()
                if self.stale(metadata):
                    raise ObservationError('No fresh frame: unlock iPhone and reconnect screen mirroring')
                observation_id = hashlib.sha256(image).hexdigest()
                if observation_id in self.cache:
                    _, _, result = self.cache[observation_id]
                    cached = True
                else:
                    result = self.ocr(image)
                    if result.get('image_sha256') != observation_id:
                        raise ObservationError('OCR image hash mismatch')
                    result = dict(result)
                    result['items'] = [dict(item, id=i) for i, item in enumerate(result['items'])]
                self.cache[observation_id] = image, dict(metadata), result
            self.cache.move_to_end(observation_id)
            while len(self.cache) > self.cache_size:
                self.cache.popitem(last=False)
            output = dict(result, observation_id=observation_id, cached=cached,
                          source=dict(metadata), stale=self.stale(metadata))
            output['source']['age_seconds'] = (None if metadata.get('received_at') is None
                                               else time.time() - metadata['received_at'])
            if output['stale'] and not include_image:
                output['note'] = 'Stale observation; do not use coordinates for actions.'
            if include_image:
                output['image'] = dict(mime_type='image/jpeg',
                                       data=base64.b64encode(image).decode())
            return output

    def stale(self, metadata):
        received = metadata.get('received_at')
        return not metadata.get('source_running') or received is None or time.time() - received > self.max_age


def make_server(observer, port=18766, inputter=None):
    class Handler(BaseHTTPRequestHandler):
        def respond(self, output, code):
            body = json.dumps(output, ensure_ascii=False, separators=(',', ':')).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            try:
                if self.path != '/input' or inputter is None:
                    raise InputError('Input endpoint unavailable', 404)
                if self.headers.get('Origin') or self.headers.get_content_type() != 'application/json':
                    raise InputError('Use a local API client with application/json, not a browser page', 403)
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 128 * 1024:
                    raise InputError('JSON request must contain 1..131072 bytes', 400)
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict) or set(data) - {'text', 'request_id', 'allow_newlines'}:
                    raise InputError('Use text, request_id and optional allow_newlines', 400)
                output = inputter.insert(data.get('text'), data.get('request_id'), data.get('allow_newlines', False))
                self.respond(output, 200)
            except (InputError, ObservationError) as exc:
                self.respond({'error': str(exc)}, exc.status)
            except (ValueError, TypeError):
                self.respond({'error': 'Invalid JSON input request'}, 400)
            except Exception:
                self.respond({'error': 'Input unavailable; inspect phone before retrying with SAME request_id'}, 503)

        def do_GET(self):
            try:
                parsed = urlsplit(self.path)
                if parsed.path == '/health':
                    output, code = {'service': 'iphone-pilot-observe', 'ok': True}, 200
                elif parsed.path == '/observe':
                    query = parse_qs(parsed.query)
                    flag = query.get('include_image', ['false'])[0]
                    if flag not in ('true', 'false'):
                        raise ObservationError('include_image must be true or false', 400)
                    output = observer.observe(flag == 'true', query.get('observation_id', [None])[0])
                    code = 200
                else:
                    raise ObservationError('Use /observe or /health', 404)
            except ObservationError as exc:
                output, code = {'error': str(exc)}, exc.status
            except Exception:
                output, code = {'error': 'Observation unavailable; check receiver and OCR worker'}, 503
            self.respond(output, code)

        def log_message(self, *_):
            pass
    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=18766)
    parser.add_argument('--source', default='http://127.0.0.1:18765')
    parser.add_argument('--max-age', type=float, default=5)
    parser.add_argument('--ocr-binary', default=str(Path(__file__).parent / 'runtime/bin/screen-ocr'))
    parser.add_argument('--disable-input', action='store_true', help='serve observations only')
    parser.add_argument('--input-directory', default=str(Path(__file__).parent / 'runtime'))
    args = parser.parse_args()
    if urlsplit(args.source).hostname not in ('127.0.0.1', 'localhost', '::1'):
        parser.error('--source must be a loopback receiver')
    worker = OCRWorker(args.ocr_binary)
    observer = Observer(lambda: fetch_frame(args.source.rstrip('/')), worker, args.max_age)
    inputter = None if args.disable_input else AirTypeInput(observer, args.input_directory)
    server = make_server(observer, args.port, inputter)
    print(f'Observation API: http://127.0.0.1:{server.server_port}/observe', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        worker.close()
