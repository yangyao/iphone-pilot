#!/usr/bin/env python3
"""Short fixed commands for iPhone Pilot; no generated HTTP scripts needed."""
import argparse
import base64
from contextlib import contextmanager
import concurrent.futures
import fcntl
import hashlib
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import tempfile
import time
import urllib.request

try:
    from .pilot_client import PilotAPI, PilotError, compact
except ImportError:
    from pilot_client import PilotAPI, PilotError, compact

REPO_ROOT = Path(__file__).resolve().parents[1]
SECRETS_PATH = os.environ.get('SECRETS') or (
    Path('/Users/jiao/www/play/esp32/esp32-hybrid/hardware/hid_bridge/secrets.h'))
RECEIVER_URL = os.environ.get('IPHONE_RECEIVER_URL', 'http://127.0.0.1:18765')


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.iphone-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(data, stream, ensure_ascii=False)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save_picture(picture, observation_id, output):
    if not isinstance(picture, dict) or picture.get('mime_type') != 'image/jpeg':
        raise PilotError('Expected a JPEG image; no file written')
    try:
        data = base64.b64decode(picture['data'], validate=True)
    except (KeyError, ValueError):
        raise PilotError('Image data is unreadable; no file written') from None
    if hashlib.sha256(data).hexdigest() != observation_id:
        raise PilotError('Image does not match observation; no file written')
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.iphone-image-', dir=output.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
        os.replace(temporary, output)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return str(output.resolve())


def extract_image(result, args):
    """Write an optional response image locally; never print base64 into tool text."""
    container = result
    if isinstance(result.get('observation'), dict):
        container = result['observation']
    picture = container.pop('image', None)
    if picture is None:
        return result
    observation_id = container.get('observation_id')
    if not observation_id:
        raise PilotError('Image has no matching observation id; no file written')
    default = args.session.with_name(args.session.stem + '-' + observation_id[:12] + '.jpg')
    output = getattr(args, 'image_output', None) or default
    result['image_file'] = save_picture(picture, observation_id, output)
    return result


@contextmanager
def session_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(str(path) + '.lock', os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise PilotError('CLI session is busy; wait and reuse the SAME request id') from None
        yield
    finally:
        os.close(descriptor)


KEYBOARD_TOKENS = ('空格', '换行', '拼字检查', '配对码', '等候电脑连接', '网页输入', '已连接', '收起')
CHEVRON_TOKENS = ('∨', 'V', 'v', 'く', '<', '﹀', '︾', '⌄')


def keyboard_present(observation):
    """Best-effort keyboard detection from OCR text and the AirType report."""
    if observation.get('airtype', {}).get('detected'):
        return True
    texts = [item.get('text', '') for item in observation.get('items', [])]
    return any(any(token in text for token in KEYBOARD_TOKENS) for text in texts)


def keyboard_top(observation):
    tops = [item['center'][1] for item in observation.get('items', [])
            if any(token in item.get('text', '') for token in KEYBOARD_TOKENS)]
    return min(tops) if tops else None


def collapse_point(observation):
    """Find a collapse chevron just above the keyboard, right side of the screen."""
    items = [item for item in observation.get('items', [])
             if len(item.get('text', '').strip()) <= 2
             and item.get('text', '').strip() in CHEVRON_TOKENS
             and item.get('center', [0, 0])[0] >= 380]
    if not items:
        return None
    top = keyboard_top(observation)
    if top is None:
        return None
    above = [item for item in items if top - 350 <= item['center'][1] < top]
    if not above:
        return None
    return max(above, key=lambda item: item['center'][1])['center']


def panel_close_point(observation):
    """Find the ✕ that closes the AirType web-input panel (bottom area, right side)."""
    items = [item for item in observation.get('items', [])
             if item.get('text', '').strip() in ('X', '✕', '×', '╳')
             and item.get('center', [0, 0])[1] >= 700]
    if not items:
        return None
    return max(items, key=lambda item: item['center'][1])['center']


def parse_bmp_pixels(bmp):
    """Decode a 24/32-bit BMP written by sips into (width, height, pixels)."""
    if bmp[:2] != b'BM':
        raise PilotError('sips did not produce a BMP image')
    offset = struct.unpack_from('<I', bmp, 10)[0]
    width, height = struct.unpack_from('<ii', bmp, 18)
    planes, bpp = struct.unpack_from('<HH', bmp, 26)
    if planes != 1 or bpp not in (24, 32):
        raise PilotError(f'Unsupported BMP layout: {bpp} bpp')
    bottom_up = height > 0
    height = abs(height)
    stride = bpp // 8
    row_size = ((bpp * width + 31) // 32) * 4
    pixels = []
    for row in range(height):
        start = offset + (height - 1 - row) * row_size if bottom_up else offset + row * row_size
        line = []
        for column in range(width):
            blue, green, red = bmp[start + stride * column:start + stride * column + 3]
            line.append((red, green, blue))
        pixels.append(line)
    return width, height, pixels


def sample_color(jpeg_bytes, points, source_width, source_height, radius=3):
    """Average a small box around each (x, y); coordinates are in OCR pixel space."""
    with tempfile.NamedTemporaryFile(prefix='.iphone-color-', suffix='.jpg', delete=False) as source:
        source.write(jpeg_bytes)
        source_path = Path(source.name)
    bmp_path = source_path.with_suffix('.bmp')
    try:
        subprocess.run(['sips', '-s', 'format', 'bmp', str(source_path), '--out', str(bmp_path)],
                       check=True, capture_output=True)
        width, height, pixels = parse_bmp_pixels(bmp_path.read_bytes())
    finally:
        source_path.unlink(missing_ok=True)
        bmp_path.unlink(missing_ok=True)
    scale = bool(source_width and source_height and (width != source_width or height != source_height))
    samples = []
    for x, y in points:
        if scale:
            x, y = x * width / source_width, y * height / source_height
        reds, greens, blues = [], [], []
        for py in range(max(0, int(y) - radius), min(height, int(y) + radius + 1)):
            for px in range(max(0, int(x) - radius), min(width, int(x) + radius + 1)):
                red, green, blue = pixels[py][px]
                reds.append(red)
                greens.append(green)
                blues.append(blue)
        if not reds:
            raise PilotError('Sample point falls outside the captured frame')
        rgb = (round(sum(reds) / len(reds)), round(sum(greens) / len(greens)), round(sum(blues) / len(blues)))
        samples.append({'point': [x, y], 'rgb': list(rgb),
                        'hex': '#{:02x}{:02x}{:02x}'.format(*rgb), 'sampled_pixels': len(reds)})
    return samples


def with_filled(samples, references, tolerance):
    """Annotate each sample: filled=True when its color is close to ANY reference RGB."""
    if references and not isinstance(references[0], (list, tuple)):
        references = [references]
    for sample in samples:
        distances = [sum((part - ref) ** 2 for part, ref in zip(sample['rgb'], ref)) ** 0.5
                     for ref in references]
        distance = min(distances)
        sample['distance'] = round(distance, 1)
        sample['filled'] = distance <= tolerance
    return samples


def insertion_matched(items, text):
    """True when the inserted text's head or tail appears in the joined OCR lines."""
    compact = ''.join(''.join(item.get('text', '').split()) for item in items)
    body = ''.join(text.split())
    if len(body) <= 16:
        return body in compact
    return body[:12] in compact or body[-8:] in compact


def layout_unchanged(before_items, after_items):
    """Loose OCR comparison: >=80% shared non-empty texts means the page did not react."""
    before = {item.get('text', '') for item in before_items or [] if item.get('text')}
    after = {item.get('text', '') for item in after_items or [] if item.get('text')}
    if not before or not after:
        return False
    overlap = len(before & after) / max(len(before), len(after))
    return overlap >= 0.8


def coordinates_for(args, action):
    """Normalize tap/swipe coordinates; raise PilotError on impossible counts."""
    coordinates = args.xy if args.xy is not None else args.coords
    if action == 'tap':
        if len(coordinates) == 2:
            return dict(x=float(coordinates[0]), y=float(coordinates[1]))
        if len(coordinates) == 1:
            return dict(target_id=int(coordinates[0]))
        raise PilotError('tap needs either an item ID (tap 12) or X Y coordinates')
    if len(coordinates) != 4:
        raise PilotError('swipe needs X1 Y1 X2 Y2 pixel coordinates')
    return dict(x=float(coordinates[0]), y=float(coordinates[1]),
                end_x=float(coordinates[2]), end_y=float(coordinates[3]),
                duration_ms=args.ms)


def send_swipe(args, state, api, request_id, x1, y1, x2, y2, duration_ms=250):
    """Issue one swipe through /act with durable request journaling."""
    requests = state.setdefault('requests', {})
    observation_id = state.get('observation_id')
    if not observation_id:
        raise PilotError('Run observe before acting')
    payload = dict(action='swipe', observation_id=observation_id, request_id=request_id,
                   timeout_ms=args.timeout, wait_for_text=None, changes_only=False,
                   include_image=False, x=float(x1), y=float(y1),
                   end_x=float(x2), end_y=float(y2), duration_ms=duration_ms)
    requests[request_id] = dict(route='/act', payload=payload)
    while len(requests) > 100:
        del requests[next(iter(requests))]
    result = api.request('/act', payload)
    observation = result.get('observation')
    if observation:
        state['observation_id'] = observation['observation_id']
    else:
        state.pop('observation_id', None)
    return result


def send_tap(args, state, api, request_id, x, y, wait=None):
    """Issue one tap through /act with durable request journaling; returns the result."""
    requests = state.setdefault('requests', {})
    observation_id = state.get('observation_id')
    if not observation_id:
        raise PilotError('Run observe before acting')
    payload = dict(action='tap', observation_id=observation_id, request_id=request_id,
                   timeout_ms=args.timeout, wait_for_text=wait, changes_only=False,
                   include_image=False, x=float(x), y=float(y))
    previous = requests.get(request_id)
    if previous and (previous['route'] != '/act' or previous['payload'] != payload):
        raise PilotError('Request id already used with different arguments; choose a new id')
    requests[request_id] = dict(route='/act', payload=payload)
    while len(requests) > 100:
        del requests[next(iter(requests))]
    result = api.request('/act', payload)
    observation = result.get('observation')
    if observation:
        state['observation_id'] = observation['observation_id']
    else:
        state.pop('observation_id', None)
    state['last_tap'] = [float(x), float(y)]
    return result


BOARD_PORT = 8766


def local_subnets():
    """Candidate /24 prefixes from all interfaces, excluding loopback, link-local and fake-ip ranges."""
    import re
    output = subprocess.run(['ifconfig'], check=True, capture_output=True, text=True).stdout
    addresses = re.findall(r'inet (\d+\.\d+\.\d+\.\d+)', output)
    prefixes = set()
    for address in addresses:
        a, b, c, _ = (int(part) for part in address.split('.'))
        if a == 127 or (a == 169 and b == 254) or (a == 198 and b == 18):
            continue  # loopback, link-local, proxy fake-ip range
        prefixes.add(f'{a}.{b}.{c}')
    return sorted(prefixes)


def discover_board(timeout=0.35):
    """Probe local /24 subnets for devices listening on the HID bridge port."""

    def probe(ip):
        conn = socket.create_connection((ip, BOARD_PORT), timeout=timeout)
        conn.close()
        return ip

    targets = [f'{prefix}.{n}' for prefix in local_subnets() for n in range(2, 255)]
    found = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=128) as pool:
        for future in concurrent.futures.as_completed({pool.submit(probe, t): t for t in targets}):
            try:
                found.append(future.result())
            except OSError:
                pass
    return sorted(found, key=lambda ip: tuple(int(part) for part in ip.split('.')))


def run(args, api):
    if args.action == 'serve':
        try:
            from .service_manager import Services
        except ImportError:
            from service_manager import Services
        return Services(REPO_ROOT).run(args)
    with session_lock(args.session):
        if args.session.exists():
            try:
                state = json.loads(args.session.read_text())
            except (OSError, ValueError):
                raise PilotError('CLI session unreadable; do not overwrite pending requests') from None
        else:
            state = {'requests': {}}
        if not isinstance(state, dict) or not isinstance(state.get('requests'), dict):
            raise PilotError('Invalid CLI session structure; preserve it and choose a separate --session')
        if state.get('api', api.base) != api.base:
            raise PilotError('Session belongs to another API; choose a separate --session file')
        state['api'] = api.base
        if args.action == 'status':
            return api.request('/health')
        if args.action == 'health':
            return api.request('/health')
        if args.action == 'board':
            if args.board_action == 'health':
                return api.request('/health')
            if args.board_action == 'discover':
                found = discover_board(args.timeout / 1000)
                if args.save:
                    path = args.session.parent / 'board.json'
                    path.parent.mkdir(parents=True, exist_ok=True)
                    save_json(path, {'host': found[0] if found else None,
                                     'discovered_at': time.strftime('%Y-%m-%dT%H:%M:%S')})
                    return dict(discovered=found, saved_to=str(path))
                return dict(discovered=found)
            if args.board_action == 'status':
                import sys as _sys
                if str(REPO_ROOT / 'hardware' / 'tools') not in _sys.path:
                    _sys.path.insert(0, str(REPO_ROOT / 'hardware' / 'tools'))
                from hybrid_control import BoardClient
                host = args.host
                if not host:
                    saved = args.session.parent / 'board.json'
                    host = json.loads(saved.read_text()).get('host') if saved.exists() else None
                if not host:
                    raise PilotError('Board host unknown; run ./iphone board discover --save '
                                     'or pass --host IP')
                config = args.config or (SECRETS_PATH if Path(SECRETS_PATH).exists() else None)
                if not config or not Path(config).exists():
                    raise PilotError(f'Board secrets.h not found (set SECRETS or pass --config)')
                return dict(host=host, board=BoardClient.from_config(host, config).status())
            raise PilotError('board action must be health, discover or status')
        if args.action == 'receiver':
            result = api.request('/health')  # liveness check of the observe API first
            with urllib.request.urlopen(f'{args.url}/status', timeout=5) as response:
                receiver = json.load(response)
            return dict(receiver=receiver, observe_api=result)
        if args.action == 'wait':
            candidates = [part for part in args.text.split('|') if part]
            deadline = time.monotonic() + max(0, args.timeout) / 1000
            while True:
                last = api.observe()
                present = any(any(part in item.get('text', '') for part in candidates)
                              for item in last.get('items', []))
                if present != args.gone:
                    state['observation_id'] = last['observation_id']
                    save_json(args.session, state)
                    return dict(found=not args.gone, matched=next(
                        (part for part in candidates
                         if any(part in item.get('text', '') for item in last.get('items', []))), None),
                        waited_ms=round((time.monotonic() - (
                            deadline - max(0, args.timeout) / 1000)) * 1000),
                        observation_id=last['observation_id'],
                        observation=last if args.include_result else None)
                if time.monotonic() >= deadline:
                    state['observation_id'] = last['observation_id']
                    save_json(args.session, state)
                    return dict(found=False, timeout=True, expecting=args.text, gone=args.gone,
                                observation_id=last['observation_id'],
                                observation=last if args.include_result else None)
                time.sleep(0.5)
        if args.action == 'scroll':
            candidates = [part for part in args.until.split('|') if part]
            outcome = dict(found=False, timeout=True, expecting=args.until, taps=[])
            for attempt in range(1, args.max + 1):
                fresh = api.observe()
                state['observation_id'] = fresh['observation_id']
                present = any(any(part in item.get('text', '') for part in candidates)
                              for item in fresh.get('items', []))
                if present != args.gone:
                    matched = next((part for part in candidates
                                    if any(part in item.get('text', '') for item in fresh.get('items', []))), None)
                    save_json(args.session, state)
                    return dict(found=not args.gone, matched=matched, attempts=attempt,
                                observation_id=fresh['observation_id'],
                                observation=fresh if args.include_result else None)
                y2 = args.start_y - args.step if args.dir == 'up' else args.start_y + args.step
                result = None
                for attempt_swipe in range(1, 4):
                    try:
                        result = send_swipe(args, state, api, f'{args.request_id}.{attempt}x{attempt_swipe}',
                                            args.x, args.start_y, args.x, y2, duration_ms=args.ms)
                        break
                    except PilotError as error:
                        if 'no action sent' not in str(error).lower() and 'not sent' not in str(error).lower():
                            raise
                        time.sleep(0.5)
                if result is None:
                    continue
                outcome['taps'].append(dict(attempt=attempt, accepted=result.get('accepted')))
                time.sleep(0.6)
            final = api.observe()
            state['observation_id'] = final['observation_id']
            save_json(args.session, state)
            outcome['observation'] = final if args.include_result else None
            outcome['observation_id'] = final['observation_id']
            return outcome
        if args.action == 'dismiss':
            current = api.observe()
            if not keyboard_present(current):
                return dict(dismissed=False, reason='no keyboard detected', observation=current)
            state['observation_id'] = current['observation_id']
            save_json(args.session, state)
            strategies = []
            after = current

            def revealed(observation):
                return bool(args.reveal) and any(
                    args.reveal in item.get('text', '') for item in observation.get('items', []))

            # 1) app-provided collapse chevron above the keyboard
            point = collapse_point(current)
            if point:
                result = send_tap(args, state, api, args.request_id, point[0], point[1])
                after = result.get('observation') or api.observe()
                if not keyboard_present(after) or revealed(after):
                    return dict(dismissed=True, via='chevron', revealed=revealed(after),
                                observation=after)
                strategies.append(dict(via='chevron', dismissed=False))
            # 2) close button of the AirType web-input panel, then re-check
            cross = panel_close_point(current)
            if cross:
                result = send_tap(args, state, api, f'{args.request_id}.panel', cross[0], cross[1])
                after = result.get('observation') or api.observe()
                if not keyboard_present(after) or revealed(after):
                    return dict(dismissed=True, via='panel-close', revealed=revealed(after),
                                observation=after)
                strategies.append(dict(via='panel-close', dismissed=False))
            # 3) small scroll in the content area: many editors blur the field on scroll
            top = keyboard_top(after or current)
            if top is None:
                top = 700
            for attempt in (1, 2):
                start = max(250, top - 40)
                result = send_swipe(args, state, api, f'{args.request_id}.scroll{attempt}',
                                    250, start, 250, start - 170)
                after = result.get('observation') or api.observe()
                if not keyboard_present(after) or revealed(after):
                    return dict(dismissed=True, via=f'scroll{attempt}', revealed=revealed(after),
                                observation=after)
                strategies.append(dict(via=f'scroll{attempt}', dismissed=False))
            return dict(dismissed=False, strategies=strategies,
                        reason='all collapse strategies failed', observation=after)
        if args.action == 'pixel':
            observation_id = args.observation_id or state.get('observation_id')
            if not observation_id:
                raise PilotError('Run observe first, or pass --from OBSERVATION_ID')
            observation = api.observe(observation_id, include_image=True)
            image = observation.pop('image', None)
            if not image:
                raise PilotError('Observation has no image; cannot sample a color')
            jpeg = base64.b64decode(image['data'], validate=True)
            pairs = list(zip(args.xy[0::2], args.xy[1::2]))
            if len(args.xy) % 2:
                raise PilotError('pixel --xy needs an even number of values (X Y pairs)')
            samples = sample_color(jpeg, pairs, observation.get('width'), observation.get('height'))
            last_tap = state.get('last_tap')
            if last_tap:
                for sample in samples:
                    dx, dy = sample['point'][0] - last_tap[0], sample['point'][1] - last_tap[1]
                    if dx * dx + dy * dy < 900:
                        sample['near_last_tap'] = True
                        sample['warning'] = ('Pointer cursor may rest on this point and darken the color; '
                                             'cross-check a neighboring element')
            result = dict(observation_id=observation_id, samples=samples)
            if args.compare:
                if len(args.compare) % 3:
                    raise PilotError('--compare needs RGB triples (3 or 6 or 9 values)')
                references = [args.compare[i:i + 3] for i in range(0, len(args.compare), 3)]
                with_filled(samples, references, args.tolerance)
            if len(samples) == 1:
                result.update(samples[0])
            return result
        if args.action == 'observe':
            result = api.observe(include_image=args.include_image)
            state['observation_id'] = result['observation_id']
            save_json(args.session, state)
            return result
        if args.action == 'image':
            observation_id = args.observation_id or state.get('observation_id')
            if not observation_id:
                raise PilotError('Run observe first, or pass --from OBSERVATION_ID')
            result = api.observe(observation_id, include_image=True)
            picture = result.pop('image')
            output = args.output or args.session.with_name(args.session.stem + '-image.jpg')
            return dict(result, image_file=save_picture(picture, result['observation_id'], output))

        requests = state.setdefault('requests', {})
        previous = requests.get(args.request_id)
        if args.action == 'taps':
            pairs = list(zip(args.xy[0::2], args.xy[1::2]))
            if len(args.xy) % 2 or not pairs:
                raise PilotError('taps --xy needs an even number of values (X Y pairs)')
            if not (args.observation_id or state.get('observation_id')):
                raise PilotError('Run observe before acting')
            current = api.observe(args.observation_id)
            if not any(args.expect in item.get('text', '') for item in current.get('items', [])):
                state['observation_id'] = current['observation_id']
                save_json(args.session, state)
                raise PilotError(f'Expected text {args.expect!r} not on current frame; '
                                 'compound taps refused to avoid firing on a stale or wrong page')
            state['observation_id'] = current['observation_id']
            save_json(args.session, state)
            outcomes = []
            result = {}
            for index, (x, y) in enumerate(pairs, 1):
                step_wait = args.wait if index == len(pairs) else None
                result = send_tap(args, state, api, f'{args.request_id}.{index}', x, y, wait=step_wait)
                outcomes.append(dict(point=[float(x), float(y)], request_id=f'{args.request_id}.{index}',
                                     accepted=result.get('accepted'),
                                     wait_status=result.get('wait', {}).get('status')))
            observation = result.get('observation')
            if observation:
                state['observation_id'] = observation['observation_id']
            state['last_tap'] = [float(pairs[-1][0]), float(pairs[-1][1])]
            save_json(args.session, state)
            return dict(taps=outcomes, observation=observation)
        if args.action == 'type':
            text = args.text if args.text is not None else args.text_file.read_text(encoding='utf-8')
            route = '/input'
            payload = dict(text=text, request_id=args.request_id, allow_newlines=args.allow_newlines,
                           include_image=args.include_image)
        else:
            route = '/act'
            observation_id = args.observation_id or (
                previous['payload'].get('observation_id') if previous else state.get('observation_id'))
            if not observation_id:
                raise PilotError('Run observe before acting')
            payload = dict(action=args.action, observation_id=observation_id, request_id=args.request_id,
                           timeout_ms=args.timeout, wait_for_text=args.wait,
                           changes_only=False, include_image=args.include_image)
            payload.update(coordinates_for(args, args.action))
        if previous and (previous['route'] != route or previous['payload'] != payload):
            raise PilotError('Request id already used with different arguments; inspect state before choosing a new id')
        pre_dynamic = False
        pre_items = None
        if args.action == 'tap' and getattr(args, 'retry', 0) > 0 and 'x' in payload:
            # Take a second pre-tap frame: two identical frames mean a static page
            # (layout comparison is trustworthy); differing frames mean a dynamic
            # page (camera/live) where layout comparison is meaningless.
            try:
                current = api.observe()
                pre_items = current.get('items')
                if payload.get('observation_id') and payload['observation_id'] != current['observation_id']:
                    earlier = api.observe(payload['observation_id'])
                    pre_dynamic = not layout_unchanged(earlier.get('items'), pre_items)
                    payload['observation_id'] = current['observation_id']
                requests[args.request_id] = dict(route=route, payload=payload)
                save_json(args.session, state)
            except PilotError:
                pass
        # Persist the ORIGINAL observation and payload before contacting the service.
        # A timeout followed by `observe` must not redirect a retry to a new target.
        requests[args.request_id] = dict(route=route, payload=payload)
        while len(requests) > 100:
            del requests[next(iter(requests))]
        save_json(args.session, state)
        try:
            result = api.request(route, payload)
        except PilotError as error:
            if not (args.action == 'tap' and getattr(args, 'retry', 0) > 0
                    and 'no action sent' in str(error).lower()):
                # Do not leave a pre-operation frame as the implicit target of a NEW action.
                state.pop('observation_id', None)
                save_json(args.session, state)
                raise
            # Camera previews and live pages never stabilize; refused taps sent nothing.
            # Re-observe and retry under fresh sub-ids (each attempt is journaled).
            result = None
            for attempt in range(1, args.retry + 1):
                state.pop('observation_id', None)
                fresh = api.observe()
                state['observation_id'] = fresh['observation_id']
                sub_id = f'{args.request_id}~{attempt}'
                sub_payload = dict(payload, observation_id=fresh['observation_id'], request_id=sub_id)
                requests[sub_id] = dict(route='/act', payload=sub_payload)
                while len(requests) > 100:
                    del requests[next(iter(requests))]
                save_json(args.session, state)
                try:
                    result = api.request('/act', sub_payload)
                    break
                except PilotError as retry_error:
                    if 'no action sent' not in str(retry_error).lower() or attempt == args.retry:
                        raise
        observation = result.get('observation')
        if observation:
            state['observation_id'] = observation['observation_id']
        else:
            state.pop('observation_id', None)
        if args.action == 'tap' and getattr(args, 'retry', 0) > 0 \
                and (result.get('wait') or {}).get('status') == 'timeout':
            # The tap was accepted but the expected text never appeared. When the page
            # still looks like the pre-tap frame, the tap likely had no effect at all
            # (live feeds can swallow taps): resend under fresh sub-ids.
            expected = args.wait
            for attempt in range(1, args.retry + 1):
                post = result.get('observation') or api.observe()
                try:
                    pre = api.observe(payload['observation_id'])
                except PilotError:
                    break
                if expected and any(expected in item.get('text', '') for item in post.get('items', [])):
                    break  # effect landed late; nothing to redo
                unchanged = layout_unchanged(pre.get('items'), post.get('items'))
                if not (unchanged or pre_dynamic):
                    break  # static page reacted to the tap; do not re-fire
                if not unchanged and attempt > 1:
                    break  # dynamic page: at most one blind resend
                fresh = api.observe()
                state['observation_id'] = fresh['observation_id']
                sub_id = f'{args.request_id}x{attempt}'
                sub_payload = dict(payload, observation_id=fresh['observation_id'], request_id=sub_id)
                requests[sub_id] = dict(route='/act', payload=sub_payload)
                while len(requests) > 100:
                    del requests[next(iter(requests))]
                save_json(args.session, state)
                try:
                    result = api.request('/act', sub_payload)
                except PilotError:
                    break
                if (result.get('wait') or {}).get('status') != 'timeout':
                    break
        if args.action == 'type' and (result.get('verification') or {}).get('status') == 'not_confirmed':
            # OCR render lag: give the field a moment, then look for the inserted prefix once more.
            # The needle is matched against ALL OCR lines joined together, because the text
            # usually wraps across lines and the first rendered line may start mid-sentence.
            needle = ''.join(text.split())[:12]
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                time.sleep(0.8)
                recheck = api.observe()
                if insertion_matched(recheck.get('items', []), text):
                    result['verification'] = dict(status='confirmed_on_recheck', needle=needle)
                    result['observation'] = recheck
                    state['observation_id'] = recheck['observation_id']
                    break
        if args.action == 'tap' and 'x' in payload:
            state['last_tap'] = [payload['x'], payload['y']]
        save_json(args.session, state)
        return result


def parser():
    root = Path(__file__).resolve().parents[1]
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('--api', default=os.environ.get('IPHONE_OBSERVE_URL', 'http://127.0.0.1:18766'))
    result.add_argument('--session', type=Path, default=root / 'runtime/cli-session.json',
                        help='private per-agent state; use distinct paths for distinct agents')
    result.add_argument('--full', action='store_true', help='include full OCR boxes/metadata')
    sub = result.add_subparsers(dest='action', required=True)
    sub.add_parser('status')
    observe = sub.add_parser('observe')
    observe.add_argument('--include-image', action='store_true')
    observe.add_argument('--image-output', type=Path)
    image = sub.add_parser('image')
    image.add_argument('--from', dest='observation_id')
    image.add_argument('--output', type=Path)
    wait = sub.add_parser('wait', help='poll OCR until TEXT appears (or disappears with --gone); '
                                       'TEXT accepts | separated alternatives')
    wait.add_argument('text', help="e.g. '发布成功|提交成功|感谢你的评价'")
    wait.add_argument('--gone', action='store_true', help='wait until TEXT is absent instead')
    wait.add_argument('--timeout', type=int, default=10000, help='milliseconds')
    wait.add_argument('--include-result', action='store_true', help='attach the final observation')
    dismiss = sub.add_parser('dismiss', help='collapse the keyboard via its chevron and verify it is gone')
    dismiss.add_argument('--id', dest='request_id', required=True)
    dismiss.add_argument('--timeout', type=int, default=5000)
    pixel = sub.add_parser('pixel', help='sample the average color at one or more points in the latest frame')
    pixel.add_argument('--xy', type=float, nargs='+', required=True, metavar='PIXEL',
                       help='one or more X Y pairs, sampled from the same frame')
    pixel.add_argument('--compare', type=float, nargs='+', metavar='RGB',
                       help='one or more reference RGB triples, e.g. 227 118 21 193 36 44; '
                            'each sample gains filled/distance against the closest match')
    pixel.add_argument('--tolerance', type=float, default=60,
                       help='max color distance for filled=True when --compare is used')
    pixel.add_argument('--from', dest='observation_id')
    taps = sub.add_parser('taps', help='tap several points in sequence with one invocation')
    taps.add_argument('--id', dest='request_id', required=True)
    taps.add_argument('--xy', type=float, nargs='+', required=True, metavar='PIXEL',
                      help='one or more X Y pairs, tapped in order')
    taps.add_argument('--expect', required=True,
                      help='text that must exist on the CURRENT frame before any tap is sent; '
                           'guards compound actions against stale-page blind firing')
    taps.add_argument('--from', dest='observation_id')
    taps.add_argument('--wait')
    taps.add_argument('--timeout', type=int, default=5000)
    for name in ('tap', 'swipe'):
        action = sub.add_parser(name)
        action.add_argument('--id', dest='request_id', required=True)
        action.add_argument('--from', dest='observation_id')
        action.add_argument('--wait')
        action.add_argument('--timeout', type=int, default=5000)
        if name == 'tap':
            action.add_argument('--retry', type=int, default=0,
                                help='re-observe and resend this many times when the page keeps '
                                     'changing (refused taps) or when the tap was accepted but the '
                                     '--wait text never appeared: static pages resend only while the '
                                     'frame matches the pre-tap layout, dynamic pages (camera/live) '
                                     'get exactly one blind resend; safe by construction')
        action.add_argument('--include-image', action='store_true')
        action.add_argument('--image-output', type=Path)
        needed = 2 if name == 'tap' else 4
        coordinate = action.add_mutually_exclusive_group(required=True)
        coordinate.add_argument('coords', type=str, nargs='*', metavar='PIXEL',
                                help=f'{name} ID (tap) or {needed} pixel values, matching --xy')
        coordinate.add_argument('--xy', type=float, nargs=needed,
                                help='tap: X Y; swipe: X1 Y1 X2 Y2')
        if name == 'swipe':
            action.add_argument('--ms', type=int, default=400)
    board = sub.add_parser('board', help='HID bridge board health, LAN discovery and raw status')
    board.add_argument('board_action', choices=('health', 'discover', 'status'))
    board.add_argument('--timeout', type=int, default=500,
                       help='per-host probe timeout in milliseconds (discover)')
    board.add_argument('--save', action='store_true',
                       help='write the first discovered host to runtime/board.json')
    board.add_argument('--host', help='board IP for status (default: runtime/board.json)')
    board.add_argument('--config', help=f'board secrets.h path (default: {SECRETS_PATH})')
    dismiss.add_argument('--reveal', help='TEXT that marks success even if the keyboard stays '
                                         '(e.g. a submit button revealed by scrolling)')
    receiver = sub.add_parser('receiver', help='frame receiver status plus observe API health')
    receiver.add_argument('--url', default=RECEIVER_URL, help='receiver base URL')
    scroll = sub.add_parser('scroll', help='swipe repeatedly until TEXT appears (or --gone)')
    scroll.add_argument('--id', dest='request_id', required=True)
    scroll.add_argument('--until', required=True,
                        help="text to scroll to; '|'-separated alternatives, e.g. '立即评价|评价'")
    scroll.add_argument('--gone', action='store_true', help='scroll until TEXT is absent instead')
    scroll.add_argument('--max', type=int, default=5, help='max swipes')
    scroll.add_argument('--x', type=float, default=250, help='swipe column')
    scroll.add_argument('--start-y', type=float, default=600, help='swipe start Y (above the keyboard)')
    scroll.add_argument('--step', type=float, default=170, help='scroll distance per swipe (px)')
    scroll.add_argument('--dir', choices=('up', 'down'), default='up',
                        help="up = reveal lower content (finger moves up); down = the reverse")
    scroll.add_argument('--ms', type=int, default=300)
    scroll.add_argument('--timeout', type=int, default=1500)
    scroll.add_argument('--include-result', action='store_true')
    health = sub.add_parser('health', help='observe API health (actions/input availability)')
    insert = sub.add_parser('type')
    insert.add_argument('--id', dest='request_id', required=True)
    source = insert.add_mutually_exclusive_group(required=True)
    source.add_argument('--text')
    source.add_argument('--text-file', type=Path)
    insert.add_argument('--allow-newlines', action='store_true')
    insert.add_argument('--include-image', action='store_true',
                        help='save the post-input JPEG with the OCR response')
    insert.add_argument('--image-output', type=Path)
    serve = sub.add_parser('serve', help='manage macOS launchd services')
    serve.add_argument('service_action', choices=('start', 'status', 'restart', 'stop', 'logs'))
    scope = serve.add_mutually_exclusive_group()
    scope.add_argument('--api', dest='api_only', action='store_true')
    scope.add_argument('--receiver', dest='receiver_only', action='store_true')
    serve.add_argument('--board-host')
    serve.add_argument('--config', default=str(SECRETS_PATH))
    serve.add_argument('--uxplay')
    serve.add_argument('--parser-backend', choices=('apple', 'omniparser-hybrid'))
    serve.add_argument('--lines', type=int, default=40)
    return result


def main():
    args = parser().parse_args()
    try:
        api = PilotAPI(args.api)
        result = run(args, api)
        if getattr(args, 'include_image', False):
            result = extract_image(result, args)
        print(json.dumps(result if args.full else compact(result), ensure_ascii=False, separators=(',', ':')))
    except (PilotError, OSError, ValueError) as error:
        message = str(error)
        if args.action in ('observe', 'image', 'status', 'wait', 'pixel', 'board', 'scroll', 'serve'):
            retry = 'Retry this read; no phone action was sent'
        elif 'no action sent' in message.lower():
            retry = 'No physical action was sent; observe again and reassess before using a new request id'
        else:
            retry = 'Inspect the phone; use the SAME request id and arguments to retrieve an uncertain outcome'
        print(json.dumps(dict(error=message, http_status=getattr(error, 'status', None),
                              request_id=getattr(args, 'request_id', None), retry=retry), ensure_ascii=False))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
