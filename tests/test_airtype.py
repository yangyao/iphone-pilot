import importlib.util
import json
from pathlib import Path
import stat
import tempfile
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

spec = importlib.util.spec_from_file_location('airtype', Path(__file__).resolve().parents[1] / 'tools/airtype.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        requests = self.requests

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append((self.path, data, self.headers.get('Authorization')))
                if self.path == '/pair':
                    status, result = (200, {'token': 'test-session'}) if data['code'] == '123456' else (403, {})
                elif self.headers.get('Authorization') != 'Bearer test-session':
                    status, result = 403, {}
                elif data.get('text') == 'simulate uncertain failure':
                    status, result = 500, {}
                else:
                    status, result = 200, {'ok': True}
                self.send_response(status)
                self.end_headers()
                self.wfile.write(json.dumps(result).encode())

            def log_message(self, *_):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = module.AirType('127.0.0.1', self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_unicode_round_trip_and_private_session(self):
        self.client.pair('123456')
        text = '中文、繁體與emoji🙂\n第二行 ABC123。'
        result = self.client.insert(text, 'unique-id')
        self.assertEqual(self.requests[-1], ('/command', {'id': 'unique-id', 'action': 'insert', 'text': text}, 'Bearer test-session'))
        self.assertTrue(result['accepted'])
        self.assertFalse(result['visually_verified'])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'session.json'
            module.save_session(path, self.client)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(json.loads(path.read_text())['token'], 'test-session')

    def test_no_automatic_retry_after_error(self):
        self.client.pair('123456')
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.client.insert('simulate uncertain failure', 'uncertain-id')
        error.exception.close()
        self.assertEqual(sum(path == '/command' for path, _, _ in self.requests), 1)

    def test_rejected_pair_does_not_replace_token(self):
        self.client.token = 'previous'
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.client.pair('000000')
        error.exception.close()
        self.assertEqual(self.client.token, 'previous')

    def test_reject_invalid_host_and_code(self):
        for host in ['example.com/path', '8.8.8.8', '0.0.0.0', '224.0.0.1']:
            with self.assertRaises(ValueError):
                module.AirType(host)
        with self.assertRaises(ValueError):
            self.client.pair('not-a-code')
