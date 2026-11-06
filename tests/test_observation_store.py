import hashlib
from pathlib import Path
import tempfile
import unittest
from tools.observation_store import ObservationStore


class StoreTests(unittest.TestCase):
    def test_restart_dedup_and_retention(self):
        with tempfile.TemporaryDirectory() as folder:
            store = ObservationStore(folder, retain=2)
            def put(image, stamp):
                key = hashlib.sha256(image).hexdigest()
                store.put(image, dict(received_at=stamp, sequence=stamp, source_running=True), dict(image_sha256=key, items=[]))
                return key
            first = put(b'one', 1)
            path = store.get(first)[3]
            put(b'one', 2)
            self.assertEqual(store.get(first)[3], path)
            self.assertEqual(store.get(first)[1]['received_at'], 2)
            store.close()
            store = ObservationStore(folder, retain=2)
            self.assertEqual(store.get(first, True)[5], b'one')
            put(b'two', 3)
            last = put(b'three', 4)
            self.assertEqual(store.get()[0], last)
            self.assertIsNone(store.get(first))
            self.assertFalse(Path(path).exists())
            store.close()
