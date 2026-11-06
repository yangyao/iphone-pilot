import io
import json
import unittest
from unittest.mock import MagicMock, patch
from observe import HybridOCR, ObservationError
from tools.pilot_client import compact


class HybridTests(unittest.TestCase):
    def make_worker(self, response):
        process = MagicMock()
        process.poll.return_value = None
        process.stdin = io.BytesIO()
        process.stdout = io.BytesIO(json.dumps(response).encode() + b'\n')
        text = MagicMock(return_value=dict(image_sha256='same-frame', items=[dict(text='发布')]))
        with patch('observe.subprocess.Popen', return_value=process):
            worker = HybridOCR(text, '/python', '/model')
        return worker, text, process

    def test_merge_preserves_text_and_icon_metadata(self):
        icon = dict(type='icon', text='', center=[30, 100], description=None,
                    source='omniparser-icon-detect', confidence=.5, clickable=None)
        worker, text, process = self.make_worker(dict(items=[icon]))
        with patch('observe.selectors.DefaultSelector') as selector:
            selector.return_value.__enter__.return_value.select.return_value = [True]
            result = worker(b'same image bytes')
        text.assert_called_once_with(b'same image bytes')
        self.assertEqual(result['image_sha256'], 'same-frame')
        self.assertEqual(result['items'][1], icon)
        self.assertEqual(compact(result)['items'][1]['source'], icon['source'])
        self.assertIn('icon_detection_seconds', result)
        worker.close()

    def test_timeout_kills_worker_instead_of_reading_mismatched_next_reply(self):
        worker, _, process = self.make_worker(dict(items=[]))
        with patch('observe.selectors.DefaultSelector') as selector:
            selector.return_value.__enter__.return_value.select.return_value = []
            with self.assertRaisesRegex(ObservationError, 'timed out'):
                worker(b'image')
        process.kill.assert_called_once()
        process.wait.assert_called_once()
