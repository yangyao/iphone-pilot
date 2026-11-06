"""Bounded SQLite observation history with immutable timestamped JPEG files."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import threading
import time


class ObservationStore:
    def __init__(self, directory, retain=1000):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.retain = max(1, retain)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.directory / 'observations.sqlite', check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS observations (id INTEGER PRIMARY KEY, observation_id TEXT UNIQUE, received_at REAL, ocr_completed_at REAL, metadata TEXT, result TEXT, image_path TEXT)')
        self.db.commit()

    def put(self, image, metadata, result):
        key = result['image_sha256']
        with self.lock:
            row = self.db.execute('SELECT image_path FROM observations WHERE observation_id=?', (key,)).fetchone()
            completed = time.time()
            if row:
                path = Path(row[0])
            else:
                stamp = datetime.fromtimestamp(metadata.get('received_at') or completed, timezone.utc)
                folder = self.directory / 'frames' / stamp.strftime('%Y%m%d')
                folder.mkdir(parents=True, exist_ok=True)
                path = folder / (stamp.strftime('%H%M%S.%f') + f"_s{metadata.get('sequence', 0)}_{key[:12]}.jpg")
                temp = path.with_suffix('.tmp')
                temp.write_bytes(image)
                temp.replace(path)
            with self.db:
                self.db.execute('INSERT INTO observations (observation_id,received_at,ocr_completed_at,metadata,result,image_path) VALUES (?,?,?,?,?,?) ON CONFLICT(observation_id) DO UPDATE SET received_at=excluded.received_at, metadata=excluded.metadata', (key, metadata.get('received_at'), completed, json.dumps(metadata), json.dumps(result), str(path)))
                old = self.db.execute('SELECT id,image_path FROM observations ORDER BY received_at DESC,id DESC LIMIT -1 OFFSET ?', (self.retain,)).fetchall()
                self.db.executemany('DELETE FROM observations WHERE id=?', [(r[0],) for r in old])
            for _, filename in old:
                Path(filename).unlink(missing_ok=True)

    def get(self, key=None, include_image=False):
        with self.lock:
            row = self.db.execute('SELECT observation_id,metadata,result,image_path,ocr_completed_at FROM observations ' + ('WHERE observation_id=?' if key else 'ORDER BY received_at DESC,id DESC LIMIT 1'), (key,) if key else ()).fetchone()
            if row is None:
                return None
            key, metadata, result, path, completed = row
            image = Path(path).read_bytes() if include_image else None
            return key, json.loads(metadata), json.loads(result), path, completed, image

    def close(self):
        self.db.close()
