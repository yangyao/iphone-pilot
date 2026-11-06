import base64
import copy
import hashlib
import json
from pathlib import Path
import stat
import tempfile
import unittest

from tools.iphone import parser, run, session_lock
from tools.pilot_client import PilotAPI, PilotError, compact


def observation(image=b'before'):
    return dict(observation_id=hashlib.sha256(image).hexdigest(), stale=False, width=498, height=1080,
                source={'age_seconds': .1}, airtype={'detected': False, 'pairing_code': None},
                items=[dict(id=12, text='评价', center=[250.12345, 999.98765], confidence=1.,
                            clickable=None, box=[225, 990, 50, 20])])


class FakeAPI:
    base = 'http://127.0.0.1:18766'

    def __init__(self):
        self.current = observation()
        self.calls = []
        self.fail = False
        self.results = {}

    def observe(self, observation_id=None, include_image=False):
        result = copy.deepcopy(self.current)
        if include_image:
            result['image'] = dict(mime_type='image/jpeg', data=base64.b64encode(b'before').decode())
        return result

    def request(self, route, payload=None):
        self.calls.append((route, copy.deepcopy(payload)))
        if route == '/health':
            return {'ok': True}
        request_id = payload['request_id']
        if request_id in self.results:
            return dict(copy.deepcopy(self.results[request_id]), replayed=True)
        result = dict(accepted=True, status='accepted', request_id=request_id, replayed=False,
                      wait={'status': 'condition_met'}, timings_ms={'total': 20})
        self.current = observation(b'after')
        if route == '/act':
            result['observation'] = copy.deepcopy(self.current)
        self.results[request_id] = result
        if self.fail:
            self.fail = False
            raise PilotError('response lost after acceptance')
        return result


class ShortClientTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.session = Path(self.directory.name) / 'state.json'
        self.api = FakeAPI()

    def invoke(self, *arguments):
        args = parser().parse_args(['--session', str(self.session), *arguments])
        return run(args, self.api)

    def test_short_tap_remembers_observation_and_exact_retry(self):
        first = self.invoke('observe')
        self.invoke('tap', '12', '--id', 'tap-1', '--wait', '发布')
        self.assertEqual(self.api.calls[-1][1]['observation_id'], first['observation_id'])
        self.assertEqual(self.api.calls[-1][1]['target_id'], 12)
        retried = self.invoke('tap', '12', '--id', 'tap-1', '--wait', '发布')
        self.assertTrue(retried['replayed'])
        self.assertEqual(self.api.calls[-1][1], self.api.calls[-2][1])
        self.assertEqual(stat.S_IMODE(self.session.stat().st_mode), 0o600)

    def test_timeout_then_observe_does_not_redirect_retry_to_new_frame(self):
        first = self.invoke('observe')
        self.api.fail = True
        with self.assertRaises(PilotError):
            self.invoke('tap', '12', '--id', 'uncertain-1')
        self.assertNotIn('observation_id', json.loads(self.session.read_text()))
        self.invoke('observe')
        retried = self.invoke('tap', '12', '--id', 'uncertain-1')
        self.assertTrue(retried['replayed'])
        self.assertEqual(self.api.calls[-1][1]['observation_id'], first['observation_id'])

    def test_cannot_reuse_id_for_different_target_or_text(self):
        self.invoke('observe')
        self.invoke('tap', '12', '--id', 'same-1')
        count = len(self.api.calls)
        with self.assertRaises(PilotError):
            self.invoke('tap', '13', '--id', 'same-1')
        self.assertEqual(len(self.api.calls), count)

    def test_pixel_tap_and_swipe_have_fixed_parameter_parsing(self):
        self.invoke('observe')
        self.invoke('tap', '--xy', '378', '296', '--id', 'stars-1')
        self.assertEqual(self.api.calls[-1][1]['x'], 378.)
        self.assertNotIn('target_id', self.api.calls[-1][1])
        self.invoke('swipe', '250', '850', '250', '350', '--id', 'scroll-1', '--ms', '300')
        self.assertEqual(self.api.calls[-1][1]['duration_ms'], 300)
        self.assertEqual(self.api.calls[-1][1]['end_y'], 350.)

    def test_unicode_file_input_preserves_content_without_shell_processing(self):
        text = '中文🙂\n第二段 `原样` $(不执行)'
        path = Path(self.directory.name) / 'draft.txt'
        path.write_text(text)
        self.invoke('type', '--text-file', str(path), '--allow-newlines', '--id', 'input-1')
        self.assertEqual(self.api.calls[-1], ('/input', dict(text=text, request_id='input-1',
                                                             allow_newlines=True, include_image=False)))

    def test_image_matches_frame_and_is_written_privately(self):
        first = self.invoke('observe')
        result = self.invoke('image')
        image_path = Path(result['image_file'])
        self.assertEqual(hashlib.sha256(image_path.read_bytes()).hexdigest(), first['observation_id'])
        self.assertEqual(stat.S_IMODE(image_path.stat().st_mode), 0o600)
        self.assertNotIn('image', result)

    def test_mismatched_image_is_not_written(self):
        self.api.current = observation(b'other frame')
        self.invoke('observe')
        with self.assertRaisesRegex(PilotError, 'does not match'):
            self.invoke('image')
        self.assertFalse(self.session.with_name('state-image.jpg').exists())

    def test_corrupt_state_is_not_overwritten(self):
        self.session.write_text('[]')
        with self.assertRaises(PilotError):
            self.invoke('observe')
        self.assertEqual(self.session.read_text(), '[]')

    def test_compact_output_keeps_safety_and_target_fields(self):
        result = compact(observation())
        self.assertEqual(result['items'][0]['center'], [250.1, 1000.])
        self.assertEqual(result['items'][0]['id'], 12)
        self.assertNotIn('box', result['items'][0])
        self.assertFalse(result['stale'])
        self.assertNotIn('source', result)  # source diagnostics only in full mode
        self.assertIn('airtype', result)

    def test_without_observation_or_while_busy_no_mutation_is_sent(self):
        with self.assertRaises(PilotError):
            self.invoke('tap', '12', '--id', 'no-frame-1')
        with session_lock(self.session):
            with self.assertRaises(PilotError):
                self.invoke('observe')
        self.assertEqual(self.api.calls, [])

    def test_refuses_nonlocal_or_credentialed_api_origins(self):
        for value in ('https://example.com', 'http://8.8.8.8:18766',
                      'http://user:password@127.0.0.1:18766', 'http://127.0.0.1:18766/path'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                PilotAPI(value)


if __name__ == '__main__':
    unittest.main()
