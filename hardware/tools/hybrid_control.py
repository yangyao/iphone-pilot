#!/usr/bin/env python3
"""Authenticated LAN client for the local hybrid firmware. Coordinates use your image dimensions."""
import argparse
import ipaddress
import json
from pathlib import Path
import re
import urllib.error
import urllib.request


def normalized(value, extent):
    if extent <= 0 or not 0 <= value < extent:
        raise ValueError('coordinate must lie inside the image')
    return round(value * 32767 / extent)


class BoardClient:
    """Reusable HTTP transport; commands are never automatically retried."""
    def __init__(self, host, token, port=8766, timeout=6):
        try:
            address = ipaddress.ip_address(host)
            if not address.is_private or address.is_unspecified or address.is_multicast:
                raise ValueError('use a local board address')
        except ValueError:
            if not re.fullmatch(r'[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)*\.local', host):
                raise ValueError('board host must be a private IP or .local name') from None
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError('invalid board port')
        if not re.fullmatch(r'[0-9a-f]+', token):
            raise ValueError('invalid local board token')
        host = f'[{host}]' if ':' in host else host
        self.base, self.timeout = f'http://{host}:{port}', timeout
        self.headers = {'Authorization': 'Bearer ' + token, 'Content-Type': 'text/plain; charset=utf-8'}
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    @classmethod
    def from_config(cls, host, config):
        match = re.search(r'^#define HYBRID_HTTP_TOKEN "([0-9a-f]+)"$', Path(config).read_text(), re.M)
        if not match:
            raise ValueError('missing local HTTP token')
        return cls(host, match[1])

    def status(self):
        request = urllib.request.Request(self.base + '/status', headers=self.headers)
        with self.opener.open(request, timeout=self.timeout) as response:
            return json.load(response)

    def command(self, line):
        if '\n' in line or '\r' in line or len(line.encode()) > 180:
            raise ValueError('command must be one line, at most 180 bytes')
        request = urllib.request.Request(self.base + '/command', data=line.encode(), headers=self.headers)
        with self.opener.open(request, timeout=self.timeout) as response:
            result = response.read().decode().strip()
        if result != 'ok':
            raise ValueError('board did not acknowledge command')
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True, help='board IP, without scheme/port')
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('status')
    raw = sub.add_parser('command')
    raw.add_argument('line', help='e.g. homescreen, key cmd+h, type nihao, home')
    for action in ('tap', 'move', 'swipe'):
        p = sub.add_parser(action)
        p.add_argument('coordinates', type=float, nargs=4 if action == 'swipe' else 2)
        p.add_argument('--width', type=float, required=True)
        p.add_argument('--height', type=float, required=True)
        if action == 'swipe':
            p.add_argument('--ms', type=int, default=400)
    args = parser.parse_args()
    config = Path(__file__).resolve().parents[1] / 'hid_bridge/secrets.h'
    match = re.search(r'^#define HYBRID_HTTP_TOKEN "([0-9a-f]+)"$', config.read_text(), re.M)
    if not match:
        raise ValueError('missing local HTTP token')
    headers = {'Authorization': 'Bearer ' + match[1], 'Content-Type': 'text/plain; charset=utf-8'}
    data = None
    if args.action != 'status':
        if args.action == 'command':
            line = args.line
        else:
            points = [normalized(v, args.width if i % 2 == 0 else args.height)
                      for i, v in enumerate(args.coordinates)]
            verb = {'tap': 'abstap', 'move': 'absmove', 'swipe': 'absdrag'}[args.action]
            if args.action == 'swipe':
                if not 60 <= args.ms <= 2000:
                    raise ValueError('duration must be 60..2000 ms')
                points.append(args.ms)
            line = verb + ' ' + ' '.join(map(str, points))
        if '\n' in line or '\r' in line or len(line.encode()) > 180:
            raise ValueError('command must be one line, at most 180 bytes')
        data = line.encode()
    url = f'http://{args.host}:8766/' + ('status' if data is None else 'command')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(urllib.request.Request(url, data=data, headers=headers), timeout=6) as response:
            print(response.read().decode())
    except urllib.error.HTTPError as error:
        raise SystemExit(f'HTTP {error.code}: {error.read().decode()}')


if __name__ == '__main__':
    main()
