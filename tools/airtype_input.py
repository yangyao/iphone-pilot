"""One-call AirType insertion using fresh OCR and a durable no-replay journal."""
import hashlib
import ipaddress
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import unicodedata
import uuid
from urllib.error import HTTPError

from tools.airtype import AirType, save_session


class InputError(Exception):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def normalized(text):
    return ''.join(unicodedata.normalize('NFKC', text).split())


def keyboard_connection(observation):
    """Do not guess digits or select between conflicting keyboard addresses/codes."""
    if observation.get('stale'):
        raise InputError('Fresh screen required before input')
    height = observation['height']
    items = observation['items']
    endpoints = []
    for item in items:
        text = normalized(item['text'])
        for match in re.finditer(r'(?:http://)?((?:\d{1,3}\.){3}\d{1,3}|[\w.-]+\.local):(\d{2,5})(?!\d)', text):
            if item.get('confidence', 0) < .7:
                continue
            try:
                client = AirType(match[1], int(match[2]))
            except ValueError:
                continue
            endpoints.append((client, item['center'][1]))
    unique = {client.base: (client, y) for client, y in endpoints}
    if len(unique) > 1:
        # The real keyboard advertises an IP and a .local alias simultaneously.
        # Accept one numeric address with a same-port adjacent alias, never two IPs.
        from urllib.parse import urlsplit
        numeric = []
        for entry in unique.values():
            try:
                ipaddress.ip_address(urlsplit(entry[0].base).hostname)
            except ValueError:
                continue
            numeric.append(entry)
        ports = {urlsplit(client.base).port for client, _ in unique.values()}
        rows = [y for _, y in unique.values()]
        if len(numeric) == 1 and len(ports) == 1 and max(rows) - min(rows) <= height * .05:
            endpoints = numeric
            unique = {numeric[0][0].base: numeric[0]}
    if len(unique) != 1:
        raise InputError('Show one readable AirType Web Access address on the focused keyboard')
    client, y = next(iter(unique.values()))
    nearby = [item for item in items if abs(item['center'][1] - y) <= height * .3]
    panel = '\n'.join(item['text'] for item in nearby).casefold()
    if not any(label in panel for label in ('airtype', 'web access', 'webaccess',
                                            '配对', '配對', 'pair', '网页输入', '網頁輸入')):
        raise InputError('Cannot identify the AirType keyboard panel; inspect the screen')
    codes = set()
    for item in nearby:
        if item.get('confidence', 0) < .7:
            continue
        # Keep whitespace grouping, but never repair O/0 or other uncertain glyphs.
        text = unicodedata.normalize('NFKC', item['text'])
        for match in re.finditer(r'(?<![\d.])(\d{3}\s?\d{3})(?![\d.])', text):
            codes.add(''.join(match[1].split()))
    if len(codes) > 1:
        raise InputError('Multiple possible pairing codes; inspect the keyboard instead of guessing')
    return client, next(iter(codes), None)


class AirTypeInput:
    def __init__(self, observer, directory, client_factory=AirType, verify_attempts=2, lock=None):
        self.observer, self.directory = observer, Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.journal = self.directory / 'airtype-input.sqlite'
        # Create the journal privately before SQLite opens it. No raw text or tokens in it.
        descriptor = os.open(self.journal, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(descriptor)
        self.client_factory, self.verify_attempts = client_factory, verify_attempts
        self.lock = lock if lock is not None else threading.Lock()
        with self.database() as db:
            db.execute('CREATE TABLE IF NOT EXISTS inputs '
                       '(request_id TEXT PRIMARY KEY, digest TEXT NOT NULL, result TEXT NOT NULL)')

    @contextmanager
    def database(self):
        db = sqlite3.connect(self.journal)
        try:
            with db:
                yield db
        finally:
            db.close()

    def insert(self, text, request_id, allow_newlines=False, include_image=False):
        if not isinstance(text, str) or not text or '\x00' in text or len(text) > 20000:
            raise InputError('text must contain 1..20000 characters and no NUL', 400)
        if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', request_id):
            raise InputError('request_id must be a stable unique identifier (1..128 ASCII characters)', 400)
        if not isinstance(allow_newlines, bool):
            raise InputError('allow_newlines must be a boolean', 400)
        if not isinstance(include_image, bool):
            raise InputError('include_image must be a boolean', 400)
        if ('\n' in text or '\r' in text) and not allow_newlines:
            raise InputError('Line breaks require allow_newlines=true and a confirmed multiline editor', 400)
        digest = hashlib.sha256(json.dumps([text, allow_newlines], ensure_ascii=False).encode()).hexdigest()
        if not self.lock.acquire(blocking=False):
            raise InputError('Phone is busy; wait, then reuse the SAME request_id')
        try:
            with self.database() as db:
                previous = db.execute('SELECT digest, result FROM inputs WHERE request_id=?', (request_id,)).fetchone()
            if previous:
                if previous[0] != digest:
                    raise InputError('request_id already used for different input')
                result = dict(json.loads(previous[1]), replayed=True)
                return self.present_observation(result, include_image)

            before = self.observer.observe()
            discovered, code = keyboard_connection(before)
            endpoint = discovered.base
            from urllib.parse import urlsplit
            address = urlsplit(endpoint)
            client = self.client_factory(address.hostname, address.port)
            session_path = self.directory / 'airtype-session.json'
            paired = False
            try:
                session = json.loads(session_path.read_text())
            except (OSError, ValueError):
                session = {}
            if session.get('endpoint') == endpoint and session.get('token'):
                client.token = session['token']
                try:
                    paired = client.request('/status').get('connected') is True
                except HTTPError as exc:
                    status = exc.code
                    exc.close()
                    if status not in (401, 403):
                        raise InputError('AirType status unavailable; no text sent') from None
                except (OSError, ValueError):
                    raise InputError('AirType status unavailable; no text sent') from None
            if not paired:
                if code is None:
                    raise InputError('No readable current pairing code and no valid saved session')
                try:
                    client.pair(code)
                    save_session(session_path, client)
                except (OSError, ValueError):
                    raise InputError('AirType pairing failed; inspect the current keyboard code. No text sent') from None

            # Pairing/status can take time. Never insert into a changed or vanished panel.
            current = self.observer.observe()
            latest, latest_code = keyboard_connection(current)
            if latest.base != endpoint or (code is not None and latest_code != code):
                raise InputError('Keyboard connection changed during pairing; no text sent')

            command_id = str(uuid.uuid5(uuid.NAMESPACE_URL, request_id + ':' + digest))
            result = dict(request_id=request_id, command_id=command_id, accepted=None, submitted=False,
                          status='uncertain', retry_safe=False, replayed=False,
                          verification={'status': 'not_checked'})
            previous = self.claim(request_id, digest, result)  # Commit BEFORE a potentially accepted command.
            if previous is not None:
                return previous
            try:
                client.insert(text, command_id)
            except Exception:
                # A timeout, malformed reply or server error may follow successful insertion.
                result['message'] = 'Input outcome uncertain; inspect the field. This request_id will not resend.'
                try:
                    result['observation'] = self.observer.observe()
                except Exception:
                    result['observation'] = None
                self.save(request_id, digest, result)
                return self.present_observation(result, include_image)
            result.update(accepted=True, status='accepted')
            self.save(request_id, digest, result)
            before_text = normalized('\n'.join(item['text'] for item in current['items']))
            wanted = normalized(text)
            result['verification'] = {'status': 'not_confirmed'}
            result['observation'] = None
            for attempt in range(self.verify_attempts):
                if attempt:
                    time.sleep(.3)
                try:
                    after = self.observer.observe()
                    result['observation'] = after
                    after_text = normalized('\n'.join(item['text'] for item in after['items']))
                    if not after['stale'] and wanted and wanted in after_text and wanted not in before_text:
                        result['verification'] = dict(status='text_detected_after_input',
                                                      observation_id=after['observation_id'])
                        break
                except Exception:
                    break
            self.save(request_id, digest, result)
            return self.present_observation(result, include_image)
        finally:
            self.lock.release()

    def save(self, request_id, digest, result):
        # The journal stores status/IDs only, never screen OCR, screenshots or draft text.
        stored = {key: value for key, value in result.items() if key != 'observation'}
        with self.database() as db:
            db.execute('UPDATE inputs SET result=? WHERE request_id=? AND digest=?',
                       (json.dumps(stored), request_id, digest))

    def claim(self, request_id, digest, result):
        try:
            with self.database() as db:
                db.execute('INSERT INTO inputs VALUES (?, ?, ?)',
                           (request_id, digest, json.dumps(result)))
        except sqlite3.IntegrityError:
            with self.database() as db:
                previous = db.execute('SELECT digest, result FROM inputs WHERE request_id=?', (request_id,)).fetchone()
            if not previous or previous[0] != digest:
                raise InputError('request_id already used for different input') from None
            return dict(json.loads(previous[1]), replayed=True)
        return None

    def present_observation(self, result, include_image):
        observation = result.get('observation')
        if observation is None and result.get('replayed'):
            # Journal intentionally excludes screen text and images. A safe replay
            # may read the current screen, but must never repeat the insertion.
            try:
                observation = self.observer.observe()
                result['observation'] = observation
            except Exception:
                pass
        if observation and include_image:
            try:
                result['observation'] = self.observer.observe(
                    include_image=True, observation_id=observation['observation_id'])
            except Exception:
                # Preserve the OCR result already returned by the verification pass.
                pass
        return result
