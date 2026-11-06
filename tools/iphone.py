#!/usr/bin/env python3
"""Short fixed commands for iPhone Pilot; no generated HTTP scripts needed."""
import argparse
import base64
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile

try:
    from .pilot_client import PilotAPI, PilotError, compact
except ImportError:
    from pilot_client import PilotAPI, PilotError, compact


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


def run(args, api):
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
            if args.action == 'tap':
                if args.target is not None:
                    payload['target_id'] = args.target
                else:
                    payload.update(x=args.xy[0], y=args.xy[1])
            else:
                payload.update(x=args.points[0], y=args.points[1], end_x=args.points[2],
                               end_y=args.points[3], duration_ms=args.ms)
        if previous and (previous['route'] != route or previous['payload'] != payload):
            raise PilotError('Request id already used with different arguments; inspect state before choosing a new id')
        # Persist the ORIGINAL observation and payload before contacting the service.
        # A timeout followed by `observe` must not redirect a retry to a new target.
        requests[args.request_id] = dict(route=route, payload=payload)
        while len(requests) > 100:
            del requests[next(iter(requests))]
        save_json(args.session, state)
        try:
            result = api.request(route, payload)
        except PilotError:
            # Do not leave a pre-operation frame as the implicit target of a NEW action.
            state.pop('observation_id', None)
            save_json(args.session, state)
            raise
        observation = result.get('observation')
        if observation:
            state['observation_id'] = observation['observation_id']
        else:
            state.pop('observation_id', None)
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
    for name in ('tap', 'swipe'):
        action = sub.add_parser(name)
        action.add_argument('--id', dest='request_id', required=True)
        action.add_argument('--from', dest='observation_id')
        action.add_argument('--wait')
        action.add_argument('--timeout', type=int, default=5000)
        action.add_argument('--include-image', action='store_true')
        action.add_argument('--image-output', type=Path)
        if name == 'tap':
            target = action.add_mutually_exclusive_group(required=True)
            target.add_argument('target', type=int, nargs='?')
            target.add_argument('--xy', type=float, nargs=2)
        else:
            action.add_argument('points', type=float, nargs=4, metavar='PIXEL')
            action.add_argument('--ms', type=int, default=400)
    insert = sub.add_parser('type')
    insert.add_argument('--id', dest='request_id', required=True)
    source = insert.add_mutually_exclusive_group(required=True)
    source.add_argument('--text')
    source.add_argument('--text-file', type=Path)
    insert.add_argument('--allow-newlines', action='store_true')
    insert.add_argument('--include-image', action='store_true',
                        help='save the post-input JPEG with the OCR response')
    insert.add_argument('--image-output', type=Path)
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
        if args.action in ('observe', 'image', 'status'):
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
