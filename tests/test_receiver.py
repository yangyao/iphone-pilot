import io
import tempfile
import unittest
from receiver import Frames, jpeg_frames


class Chunks:
    def __init__(self, parts):
        self.parts = iter(parts)

    def read1(self, _):
        return next(self.parts, b'')


class ReceiverTests(unittest.TestCase):
    def test_split_markers_and_preamble(self):
        source = Chunks([b'{"status":"ok"}\n\xff', b'\xd8abc\xff',
                         b'\xd9\xff\xd8def\xff\xd9\xff\xd8incomplete'])
        self.assertEqual(list(jpeg_frames(source)),
                         [b'\xff\xd8abc\xff\xd9', b'\xff\xd8def\xff\xd9'])

    def test_cache_and_status(self):
        with tempfile.TemporaryDirectory() as directory:
            frames = Frames(directory)
            self.assertIsNone(frames.status()['age_seconds'])
            frames.publish(b'first')
            frames.publish(b'second')
            status = frames.status()
            self.assertEqual(status['sequence'], 2)
            with open(status['file'], 'rb') as image:
                self.assertEqual(image.read(), b'second')
            self.assertGreaterEqual(status['age_seconds'], 0)


if __name__ == '__main__':
    unittest.main()
