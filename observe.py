#!/usr/bin/env python3
"""Persistent local OCR observations and optional one-call AirType insertion."""
import argparse
import base64
from collections import OrderedDict
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import selectors
import subprocess
import threading
import time
from urllib.parse import parse_qs, urlsplit
from urllib.request import ProxyHandler, build_opener
from tools.observation_store import ObservationStore
from tools.airtype_input import AirTypeInput, InputError, keyboard_connection
from tools.action_runtime import ActionError, PhoneActions
from hardware.tools.hybrid_control import BoardClient

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


class HybridOCR:
    def __init__(self, text_worker, python, model):
        self.text_worker = text_worker
        self.python, self.model = python, model
        self.process = subprocess.Popen([python, str(Path(__file__).parent / 'tools/icon_worker.py'), model],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE)

    def __call__(self, image):
        if self.process.poll() is not None:
            self.process = subprocess.Popen([self.python, str(Path(__file__).parent / 'tools/icon_worker.py'), self.model],
                                            stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        result = self.text_worker(image)
        icon_start = time.monotonic()
        self.process.stdin.write(base64.b64encode(image) + b'\n')
        self.process.stdin.flush()
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdout, selectors.EVENT_READ)
            if not selector.select(120):
                self.process.kill()
                self.process.wait()
                raise ObservationError('OmniParser timed out')
        icons = json.loads(self.process.stdout.readline())
        if 'error' in icons:
            raise ObservationError('OmniParser: ' + icons['error'])
        result['items'].extend(icons['items'])
        result['icon_detection_seconds'] = round(time.monotonic() - icon_start, 4)
        result['parser_backend'] = 'omniparser-hybrid'
        return result

    def close(self):
        self.process.terminate()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        self.text_worker.close()


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
        self.latest_observation_id = None

    def observe(self, include_image=False, observation_id=None):
        with self.lock:
            cached = False
            if observation_id:
                if observation_id not in self.cache:
                    raise ObservationError('Observation expired; request a fresh observation', 404)
                image, metadata, result = self.cache[observation_id]
                cached = True
                read_mode = 'pinned'
            else:
                try:
                    image, metadata = self.fetch()
                except Exception as exc:
                    # A frame gap should not make the screen unreadable. Return the
                    # newest known frame as explicitly stale; action/input paths
                    # continue to reject stale observations.
                    observation_id = self.latest_observation_id
                    if observation_id in self.cache:
                        image, metadata, result = self.cache[observation_id]
                    else:
                        raise ObservationError('No readable screen frame is available; retry shortly') from exc
                    cached = True
                    read_mode = 'cached_fallback'
                else:
                    observation_id = hashlib.sha256(image).hexdigest()
                    read_mode = 'live' if not self.stale(metadata) else 'cached_fallback'
                    if observation_id in self.cache:
                        _, _, result = self.cache[observation_id]
                        cached = True
                    else:
                        result = self.ocr(image)
                        if result.get('image_sha256') != observation_id:
                            raise ObservationError('OCR image hash mismatch')
                        result = dict(result)
                        result['items'] = [dict(item, id=i) for i, item in enumerate(result['items'])]
                    self.latest_observation_id = observation_id
                    self.cache[observation_id] = image, dict(metadata), result
            self.cache.move_to_end(observation_id)
            while len(self.cache) > self.cache_size:
                self.cache.popitem(last=False)
            output = dict(result, observation_id=observation_id, cached=cached,
                          source=dict(metadata),
                          stale=(read_mode == 'cached_fallback' or self.stale(metadata)))
            output['source']['read_mode'] = read_mode
            age_seconds = (None if metadata.get('received_at') is None
                           else time.time() - metadata['received_at'])
            output['source']['age_seconds'] = age_seconds
            output['frame_read_mode'] = read_mode
            output['frame_age_seconds'] = age_seconds
            try:
                keyboard, pairing_code = keyboard_connection(output)
                endpoint = urlsplit(keyboard.base)
                output['airtype'] = dict(detected=True, endpoint=keyboard.base,
                                         host=endpoint.hostname, port=endpoint.port,
                                         pairing_code=pairing_code, connection_verified=False)
            except InputError as exc:
                output['airtype'] = dict(detected=False, pairing_code=None, reason=str(exc))
            if output['stale']:
                output['note'] = 'Showing the most recently captured frame; it may be old. Do not use its coordinates for actions.'
            if include_image:
                output['image'] = dict(mime_type='image/jpeg',
                                       data=base64.b64encode(image).decode())
            return output

    def capture_status(self):
        """Report the receiver's latest frame immediately, without waiting for freshness."""
        try:
            image, metadata = self.fetch()
        except Exception:
            return dict(fresh_frame_available=False, cached_frame_available=False)
        return dict(fresh_frame_available=(image is not None and not self.stale(metadata)),
                    cached_frame_available=image is not None)

    def stale(self, metadata):
        received = metadata.get('received_at')
        return not metadata.get('source_running') or received is None or time.time() - received > self.max_age


class StoredObserver(Observer):
    """Collect in the background; query only completed SQLite records."""
    def __init__(self, fetch, ocr, directory, max_age=5, interval=0.5, retain=1000):
        super().__init__(fetch, ocr, max_age)
        self.store = ObservationStore(directory, retain)
        self.stop = threading.Event()
        self.interval = interval
        self.collector = threading.Thread(target=self.collect, daemon=True)
        self.collector.start()

    def collect(self):
        while not self.stop.is_set():
            try:
                result = super().observe(include_image=True)
                if result['frame_read_mode'] == 'live':
                    raw = dict(result)
                    picture = raw.pop('image')
                    self.store.put(base64.b64decode(picture['data']), result['source'], raw)
            except Exception:
                pass  # Keep completed observations readable during receiver/OCR gaps.
            self.stop.wait(self.interval)

    def observe(self, include_image=False, observation_id=None):
        row = self.store.get(observation_id, include_image)
        if row is None:
            raise ObservationError('Observation expired' if observation_id else 'No completed observation available; retry shortly', 404 if observation_id else 503)
        key, metadata, result, path, completed, picture = row
        result.update(observation_id=key, cached=True, image_path=path,
                      image_url='/image?observation_id=' + key,
                      received_at=metadata.get('received_at'), ocr_completed_at=completed)
        result['source'] = metadata
        result['frame_age_seconds'] = time.time() - metadata['received_at'] if metadata.get('received_at') else None
        result['stale'] = self.stale(metadata)
        result['frame_read_mode'] = 'cached_fallback' if result['stale'] else ('pinned' if observation_id else 'database')
        if result['stale']:
            result['airtype'] = dict(detected=False, pairing_code=None, reason='Fresh screen required before input')
        if include_image:
            result['image'] = dict(mime_type='image/jpeg', data=base64.b64encode(picture).decode())
        return result

    def close(self):
        self.stop.set()
        self.collector.join()
        self.store.close()


def make_server(observer, port=18766, inputter=None, actor=None):
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
                if self.path not in ('/input', '/act'):
                    raise InputError('Use /input or /act', 404)
                if self.path == '/input' and inputter is None:
                    raise InputError('Input endpoint unavailable', 404)
                if self.path == '/act' and actor is None:
                    raise ActionError('Actions unavailable; start service with --board-host and --board-config', 503)
                if self.headers.get('Origin') or self.headers.get_content_type() != 'application/json':
                    raise InputError('Use a local API client with application/json, not a browser page', 403)
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 128 * 1024:
                    raise InputError('JSON request must contain 1..131072 bytes', 400)
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise InputError('JSON body must be an object', 400)
                if self.path == '/input':
                    if set(data) - {'text', 'request_id', 'allow_newlines', 'include_image'}:
                        raise InputError('Use text, request_id, allow_newlines and include_image', 400)
                    if data.get('include_image', False):
                        output = inputter.insert(data.get('text'), data.get('request_id'),
                                                 data.get('allow_newlines', False), True)
                    else:
                        output = inputter.insert(data.get('text'), data.get('request_id'),
                                                 data.get('allow_newlines', False))
                else:
                    output = actor.act(**data)
                self.respond(output, 200)
            except (InputError, ObservationError, ActionError) as exc:
                self.respond({'error': str(exc)}, exc.status)
            except (ValueError, TypeError):
                self.respond({'error': 'Invalid JSON or request arguments'}, 400)
            except Exception:
                self.respond({'error': 'Operation unavailable; inspect phone before retrying with SAME request_id'}, 503)

        def do_GET(self):
            try:
                parsed = urlsplit(self.path)
                if parsed.path == '/health':
                    output, code = dict(service='iphone-pilot-observe', ok=True,
                                        **observer.capture_status(),
                                        input_enabled=inputter is not None,
                                        actions_enabled=actor is not None), 200
                elif parsed.path == '/image':
                    key = parse_qs(parsed.query).get('observation_id', [None])[0]
                    if not key:
                        raise ObservationError('observation_id required', 400)
                    result = observer.observe(True, key)
                    body = base64.b64decode(result['image']['data'])
                    self.send_response(200)
                    self.send_header('Content-Type', 'image/jpeg')
                    self.send_header('Cache-Control', 'no-store')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
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
    parser.add_argument('--disable-input', action='store_true', help='disable AirType insertion')
    parser.add_argument('--read-only', action='store_true', help='disable both AirType insertion and HID actions')
    parser.add_argument('--input-directory', default=str(Path(__file__).parent / 'runtime'))
    parser.add_argument('--board-host', default=os.environ.get('IPHONE_BOARD_HOST'),
                        help='enable /act using this local board address')
    parser.add_argument('--board-config', default=os.environ.get('IPHONE_BOARD_CONFIG') or
                        str(Path(__file__).parent / 'hardware/hid_bridge/secrets.h'),
                        help='local secrets.h containing the board HTTP token; never returned by API')
    parser.add_argument('--observation-directory', default='runtime/observations')
    parser.add_argument('--observation-interval', type=float, default=0.5)
    parser.add_argument('--retain-observations', type=int, default=1000)
    parser.add_argument('--parser-backend', choices=('apple', 'omniparser-hybrid'), default='apple')
    args = parser.parse_args()
    if args.observation_interval < 0.1:
        parser.error('--observation-interval must be at least 0.1')
    if urlsplit(args.source).hostname not in ('127.0.0.1', 'localhost', '::1'):
        parser.error('--source must be a loopback receiver')
    worker = OCRWorker(args.ocr_binary)
    if args.parser_backend == 'omniparser-hybrid':
        worker = HybridOCR(worker, str(Path(__file__).parent / 'runtime/omniparser-venv/bin/python'),
                           str(Path(__file__).parent / 'runtime/omniparser-icon.pt'))
    observer = StoredObserver(lambda: fetch_frame(args.source.rstrip('/')), worker,
                              (args.observation_directory if args.parser_backend == 'apple' else
                               args.observation_directory + '-omniparser'), args.max_age,
                              args.observation_interval, args.retain_observations)
    interaction_lock = threading.Lock()
    inputter = None if args.disable_input or args.read_only else AirTypeInput(
        observer, args.input_directory, lock=interaction_lock)
    try:
        board = BoardClient.from_config(args.board_host, args.board_config) if args.board_host and not args.read_only else None
    except (OSError, ValueError):
        parser.error('Cannot load board configuration; check --board-host and --board-config')
    actor = PhoneActions(observer, board, args.input_directory, lock=interaction_lock) if board else None
    server = make_server(observer, args.port, inputter, actor)
    print(f'Observation API: http://127.0.0.1:{server.server_port}/observe', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        observer.close()
        worker.close()
