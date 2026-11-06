"""Private durable action claims. A claimed command is never replayed automatically."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3


class OperationJournal:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT, 0o600)
        os.close(descriptor)
        with self.database() as db:
            db.execute('CREATE TABLE IF NOT EXISTS operations '
                       '(request_id TEXT PRIMARY KEY, digest TEXT NOT NULL, result TEXT NOT NULL)')

    @contextmanager
    def database(self):
        db = sqlite3.connect(self.path)
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, request_id, digest):
        with self.database() as db:
            row = db.execute('SELECT digest,result FROM operations WHERE request_id=?', (request_id,)).fetchone()
        if row is None:
            return None
        if row[0] != digest:
            raise ValueError('request_id already used for a different action')
        return json.loads(row[1])

    def claim(self, request_id, digest, result):
        try:
            with self.database() as db:
                db.execute('INSERT INTO operations VALUES (?, ?, ?)',
                           (request_id, digest, json.dumps(result, ensure_ascii=False)))
        except sqlite3.IntegrityError:
            return self.get(request_id, digest)
        return None

    def save(self, request_id, digest, result):
        with self.database() as db:
            db.execute('UPDATE operations SET result=? WHERE request_id=? AND digest=?',
                       (json.dumps(result, ensure_ascii=False), request_id, digest))
