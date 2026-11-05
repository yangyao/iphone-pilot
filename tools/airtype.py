#!/usr/bin/env python3
"""Local AirType client. Successful HTTP execution still requires visual verification."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import tempfile
import urllib.error
import urllib.request
import uuid


class AirType:
    def __init__(self, host, port=8307, token='', timeout=4):
        try:
            address = ipaddress.ip_address(host)
            if not address.is_private or address.is_unspecified or address.is_multicast:
                raise ValueError('use a local device address')
        except ValueError:
            if not re.fullmatch(r'[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)*\.local', host):
                raise ValueError('host must be a private IP address or .local name') from None
        if not 1 <= port <= 65535:
            raise ValueError('port must be 1..65535')
        self.base = f'http://[{host}]:{port}' if ':' in host else f'http://{host}:{port}'
        self.token, self.timeout = token, timeout
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, path, data=None):
        request = urllib.request.Request(
            self.base + path,
            data=None if data is None else json.dumps(data, ensure_ascii=False).encode('utf-8'),
            headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.token})
        # Never automatically retry input: a timeout may happen after insertion.
        with self.opener.open(request, timeout=self.timeout) as response:
            return json.load(response)

    def pair(self, code):
        if not re.fullmatch(r'\d{6}', code):
            raise ValueError('use the current six-digit code visible on the phone')
        result = self.request('/pair', {'code': code})
        token = result.get('token')
        if not isinstance(token, str) or not token:
            raise ValueError('pair response did not contain a token')
        self.token = token

    def insert(self, text, command_id):
        if not text or '\x00' in text:
            raise ValueError('text must be nonempty and contain no NUL')
        result = self.request('/command', {'id': command_id, 'action': 'insert', 'text': text})
        if result.get('ok') is not True:
            raise ValueError('input was not acknowledged; inspect the phone before retrying')
        return {'accepted': True, 'visually_verified': False, 'command_id': command_id}


def save_session(path, client):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.airtype-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump({'endpoint': client.base, 'token': client.token}, stream)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True, help='phone LAN IP or .local name, without scheme')
    parser.add_argument('--port', type=int, default=8307)
    parser.add_argument('--session', type=Path,
                        default=Path(__file__).resolve().parents[1] / 'runtime/airtype-session.json')
    sub = parser.add_subparsers(dest='action', required=True)
    pair = sub.add_parser('pair')
    pair.add_argument('--code', required=True)
    sub.add_parser('status')
    insert = sub.add_parser('type')
    source = insert.add_mutually_exclusive_group(required=True)
    source.add_argument('--text')
    source.add_argument('--text-file', type=Path, help='UTF-8 file; preserves punctuation and newlines')
    insert.add_argument('--allow-newlines', action='store_true', help='only for a confirmed multiline editor')
    args = parser.parse_args()
    command_id = str(uuid.uuid4()) if args.action == 'type' else None
    try:
        client = AirType(args.host, args.port)
        if args.action == 'pair':
            client.pair(args.code)
            save_session(args.session, client)
            print(json.dumps({'paired': True, 'session': str(args.session)}))
            return
        session = json.loads(args.session.read_text())
        if session.get('endpoint') != client.base or not session.get('token'):
            raise ValueError('session does not match this endpoint; pair again')
        client.token = session['token']
        if args.action == 'status':
            print(json.dumps(client.request('/status')))
        else:
            text = args.text if args.text is not None else args.text_file.read_text(encoding='utf-8')
            if ('\n' in text or '\r' in text) and not args.allow_newlines:
                raise ValueError('line breaks require --allow-newlines and a confirmed multiline field')
            print(json.dumps(client.insert(text, command_id)))
    except urllib.error.HTTPError as error:
        reason = 'read the current keyboard code and pair again' if error.code in (401, 403) else 'inspect phone state'
        parser.exit(1, f'HTTP {error.code}: {reason}. No automatic retry. command_id={command_id}\n')
    except (OSError, ValueError) as error:
        parser.exit(1, f'{error}. No automatic retry; inspect phone before resending. command_id={command_id}\n')


if __name__ == '__main__':
    main()
