import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

urlopen = build_opener(ProxyHandler({})).open

from observe import Observer, ObservationError, make_server


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.image = b'example frame bytes'
        self.metadata = dict(received_at=time.time(), sequence=1, source_running=True, metadata_exact=True)
        self.calls = 0
        def ocr(image):
            self.calls += 1
            return dict(image_sha256=hashlib.sha256(image).hexdigest(), width=498, height=1080,
                        items=[dict(text='发布', center=[250, 1000], clickable=None)])
        self.observer = Observer(lambda: (self.image, dict(self.metadata)), ocr, cache_size=2)

    def test_default_text_and_pinned_image_agree(self):
        first = self.observer.observe()
        self.assertNotIn('image', first)
        self.image = b'new page'
        pinned = self.observer.observe(True, first['observation_id'])
        self.assertEqual(base64.b64decode(pinned['image']['data']), b'example frame bytes')
        self.assertEqual(pinned['items'][0]['id'], 0)

    def test_same_frame_concurrent_requests_only_ocr_once(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.observer.observe(), range(8)))
        self.assertEqual(self.calls, 1)
        self.assertEqual(len({r['observation_id'] for r in results}), 1)

    def test_airtype_hint_uses_same_ocr_without_extra_recognition(self):
        from test_airtype_input import screen
        fixture = screen(code='012345')
        def ocr(image):
            self.calls += 1
            return dict(image_sha256=hashlib.sha256(image).hexdigest(), width=498,
                        height=fixture['height'], items=fixture['items'])
        self.observer.ocr = ocr
        observation = self.observer.observe()
        self.assertEqual(observation['airtype'], dict(
            detected=True, endpoint='http://192.168.2.140:8307', host='192.168.2.140',
            port=8307, pairing_code='012345', connection_verified=False))
        self.assertEqual(self.calls, 1)
        self.observer.observe(True, observation['observation_id'])
        self.assertEqual(self.calls, 1)
        self.observer.max_age = -1
        stale = self.observer.observe(observation_id=observation['observation_id'])
        self.assertFalse(stale['airtype']['detected'])
        self.assertIsNone(stale['airtype']['pairing_code'])

    def test_no_keyboard_does_not_break_observation(self):
        result = self.observer.observe()
        self.assertFalse(result['airtype']['detected'])
        self.assertIsNone(result['airtype']['pairing_code'])

    def test_stale_latest_rejected_and_old_pin_marked(self):
        first = self.observer.observe()
        self.observer.max_age = -1
        with self.assertRaises(ObservationError):
            self.observer.observe()
        self.assertTrue(self.observer.observe(True, first['observation_id'])['stale'])

    def test_eviction_and_hash_validation(self):
        first = self.observer.observe()
        for value in (b'page two', b'page three'):
            self.image = value
            self.observer.observe()
        with self.assertRaises(ObservationError) as caught:
            self.observer.observe(observation_id=first['observation_id'])
        self.assertEqual(caught.exception.status, 404)
        self.image = b'page four'
        self.observer.ocr = lambda _: {'image_sha256': 'wrong'}
        with self.assertRaisesRegex(ObservationError, 'hash mismatch'):
            self.observer.observe()

    def test_http_contract(self):
        server = make_server(self.observer, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f'http://127.0.0.1:{server.server_port}'
        try:
            with urlopen(url + '/observe') as response:
                result = json.load(response)
            self.assertEqual(result['items'][0]['text'], '发布')
            with self.assertRaises(HTTPError) as caught:
                urlopen(url + '/observe?include_image=maybe')
            self.assertEqual(caught.exception.code, 400)
            caught.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_http_input_rejects_browser_and_invalid_json_without_sending(self):
        class FakeInput:
            calls = []

            def insert(self, *args):
                self.calls.append(args)
                return {'accepted': True, 'submitted': False}

        inputter = FakeInput()
        server = make_server(self.observer, 0, inputter)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f'http://127.0.0.1:{server.server_port}/input'
        try:
            payload = json.dumps(dict(text='中文🙂', request_id='http-1')).encode()
            for headers, body, code in (
                ({'Content-Type': 'text/plain'}, payload, 403),
                ({'Content-Type': 'application/json', 'Origin': 'https://example.com'}, payload, 403),
                ({'Content-Type': 'application/json'}, b'not-json', 400),
            ):
                with self.assertRaises(HTTPError) as caught:
                    urlopen(Request(url, data=body, headers=headers))
                self.assertEqual(caught.exception.code, code)
                caught.exception.close()
            self.assertEqual(inputter.calls, [])
            with urlopen(Request(url, data=payload, headers={'Content-Type': 'application/json'})) as response:
                self.assertTrue(json.load(response)['accepted'])
            self.assertEqual(inputter.calls, [('中文🙂', 'http-1', False)])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
