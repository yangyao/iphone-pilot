import importlib.util
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import unittest
import threading
from urllib.error import HTTPError

spec = importlib.util.spec_from_file_location(
    'hybrid_control', Path(__file__).resolve().parents[1] / 'hardware/tools/hybrid_control.py')
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)


class CoordinateTests(unittest.TestCase):
    def test_pixel_mapping(self):
        self.assertEqual(control.normalized(0, 498), 0)
        self.assertEqual(control.normalized(249, 498), 16384)
        self.assertLessEqual(control.normalized(497, 498), 32767)

    def test_invalid_coordinates(self):
        for value, extent in [(-1, 498), (498, 498), (0, 0), (1, -1),
                              (math.nan, 498), (math.inf, 498)]:
            with self.subTest(value=value, extent=extent):
                with self.assertRaises(ValueError):
                    control.normalized(value, extent)


class BoardClientTests(unittest.TestCase):
    def test_authenticated_transport_and_no_retry(self):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append((self.path, self.headers.get('Authorization')))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({'usb': 'ok', 'busy': 0}).encode())

            def do_POST(self):
                data = self.rfile.read(int(self.headers['Content-Length'])).decode()
                requests.append((self.path, data))
                self.send_response(200 if data == 'abstap 0 0' else 409)
                self.end_headers()
                self.wfile.write(b'ok' if data == 'abstap 0 0' else b'err absolute HID send failed')

            def log_message(self, *_):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = control.BoardClient('127.0.0.1', 'a' * 48, port=server.server_port)
            self.assertEqual(client.status()['usb'], 'ok')
            self.assertEqual(requests[0], ('/status', 'Bearer ' + 'a' * 48))
            self.assertEqual(client.command('abstap 0 0'), 'ok')
            with self.assertRaises(HTTPError) as caught:
                client.command('abstap 100 100')
            caught.exception.close()
            self.assertEqual(requests.count(('/command', 'abstap 100 100')), 1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
