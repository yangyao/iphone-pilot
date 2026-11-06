"""Observe-bound absolute actions, bounded state waits, deltas and timing."""
import hashlib
import json
import math
from pathlib import Path
import re
import threading
import time

from hardware.tools.hybrid_control import normalized as hid_coordinate
from tools.airtype_input import normalized
from tools.operation_journal import OperationJournal


class ActionError(Exception):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def content_items(observation):
    # The clock/battery row alone must not confirm navigation.
    return [item for item in observation['items']
            if item['center'][1] >= observation['height'] * .06 and normalized(item['text'])]


def delta(before, after):
    old, new = content_items(before), content_items(after)
    remaining = list(new)
    removed = []
    tolerance = max(3, min(after['width'], after['height']) * .01)
    for item in old:
        match = next((index for index, candidate in enumerate(remaining)
                      if normalized(candidate['text']) == normalized(item['text']) and
                      math.dist(candidate['center'], item['center']) <= tolerance), None)
        if match is None:
            removed.append(item)
        else:
            remaining.pop(match)
    return dict(added=remaining, removed=removed)


def same_layout(before, after, anchor=None):
    """Compare actionable OCR around a coordinate, ignoring transient top chrome."""
    if (before['width'], before['height']) != (after['width'], after['height']):
        return False
    width, height = after['width'], after['height']
    anchors = [] if anchor is None else anchor
    if anchor is not None and (len(anchor) == 2 and
                               all(isinstance(value, (int, float)) for value in anchor)):
        anchors = [anchor]
    if not anchors:
        # Preserve conservative behavior where no intended point is known.
        return not delta(before, after)['added'] and not delta(before, after)['removed']
    radius = max(48, min(width, height) * .14)
    def nearby(observation, point):
        return sorted((normalized(item['text']), round(item['center'][0] / width, 2),
                       round(item['center'][1] / height, 2))
                      for item in content_items(observation)
                      if math.dist(item['center'], point) <= radius)
    return all(nearby(before, point) == nearby(after, point) for point in anchors)


def stable_content(before, after):
    """Ignore explicit countdown values only when checking post-action stability."""
    def cleaned(observation):
        result = dict(observation)
        result['items'] = [dict(item, text=re.sub(
            r'\d+\s*(?=天|小时|分钟|秒)', '#', item['text']))
            for item in content_items(observation)]
        return result
    return same_layout(cleaned(before), cleaned(after))


def target_found(observation, expected):
    text = normalized('\n'.join(item['text'] for item in content_items(observation)))
    return any(normalized(option) in text for option in expected.split('|') if normalized(option))


def loading_content(observation):
    return any(normalized(item['text']) in
               ('加载中', '加载中...', '正在加载', '正在加载...', '请稍候', '请稍候...')
               for item in content_items(observation))


def probe_point(observation, excluded=()):
    """Choose an OCR-empty edge location for a non-click pointer liveness probe."""
    width, height = observation['width'], observation['height']
    margin = max(28, min(width, height) * .035)
    candidates = [
        (.92, .56), (.08, .56), (.92, .78), (.08, .78),
        (.50, .90), (.50, .13), (.92, .36), (.08, .36),
    ]
    boxes = []
    for item in content_items(observation):
        box = item.get('box')
        if isinstance(box, (list, tuple)) and len(box) == 4:
            x, y, w, h = box
            boxes.append((x - margin, y - margin, x + w + margin, y + h + margin))
        elif item.get('center'):
            x, y = item['center']
            boxes.append((x - margin * 1.5, y - margin, x + margin * 1.5, y + margin))

    candidates = [(round(rx * width), round(ry * height)) for rx, ry in candidates]
    safe = []
    for x, y in candidates:
        if any(left <= x <= right and top <= y <= bottom
               for left, top, right, bottom in boxes):
            continue
        if any(math.dist((x, y), point) < margin * 2 for point in excluded):
            continue
        clearance = min((math.dist((x, y), (min(max(x, left), right), min(max(y, top), bottom)))
                        for left, top, right, bottom in boxes), default=float('inf'))
        safe.append((clearance, x, y))
    if not safe:
        return None
    _, x, y = max(safe)
    return x, y


class PhoneActions:
    def __init__(self, observer, board, directory, lock=None, poll_interval=.15,
                 monotonic=time.monotonic, wall_time=time.time, sleep=time.sleep,
                 probe_timeout_ms=1200):
        self.observer, self.board = observer, board
        self.lock = lock if lock is not None else threading.Lock()
        self.journal = OperationJournal(Path(directory) / 'phone-actions.sqlite')
        self.poll_interval = poll_interval
        self.monotonic, self.wall_time, self.sleep = monotonic, wall_time, sleep
        self.probe_timeout_ms = probe_timeout_ms

    def act(self, *, action, observation_id, request_id, target_id=None,
            x=None, y=None, end_x=None, end_y=None, duration_ms=400,
            wait_for_text=None, timeout_ms=5000, changes_only=False, include_image=False):
        start = self.monotonic()
        self.validate(action, observation_id, request_id, target_id, x, y, end_x, end_y,
                      duration_ms, wait_for_text, timeout_ms, changes_only, include_image)
        # Output format can change on a retry; physical action and wait condition cannot.
        intent = dict(action=action, observation_id=observation_id, target_id=target_id,
                      x=x, y=y, end_x=end_x, end_y=end_y, duration_ms=duration_ms,
                      wait_for_text=wait_for_text, timeout_ms=timeout_ms)
        digest = hashlib.sha256(json.dumps(intent, sort_keys=True).encode()).hexdigest()
        if not self.lock.acquire(blocking=False):
            raise ActionError('Phone is busy; wait, then retry with the SAME request_id')
        try:
            previous = self.journal.get(request_id, digest)
            if previous is not None:
                return self.present(previous, True, changes_only, include_image)
            pinned = self.observer.observe(observation_id=observation_id)
            # A cached frame can be old, but must still agree with a freshly received screen.
            board_start = self.monotonic()
            try:
                status = self.board.status()
            except Exception:
                raise ActionError('Board status unavailable; no action sent', 503) from None
            if status.get('usb') != 'ok' or status.get('busy') or status.get('owner') == 'app':
                raise ActionError('Board USB not ready or another controller is active; no action sent')
            board_ms = (self.monotonic() - board_start) * 1000
            try:
                current = self.observer.observe()
            except Exception:
                raise ActionError('Screen temporarily unavailable; no action sent', 503) from None
            probe = dict(performed=False, frame_confirmed=False)
            if current['stale']:
                probe_start = self.monotonic()
                exclude = []
                if target_id is not None:
                    item = next((item for item in pinned['items'] if item['id'] == target_id), None)
                    if item:
                        exclude.append(item['center'])
                elif x is not None and y is not None:
                    exclude.append((x, y))
                if action == 'swipe' and end_x is not None and end_y is not None:
                    exclude.append((end_x, end_y))
                point = probe_point(current, exclude)
                if point is None:
                    raise ActionError('Screen is stale and no OCR-empty pointer probe location is available; tap/swipe not sent', 503)
                px = hid_coordinate(point[0], current['width'])
                py = hid_coordinate(point[1], current['height'])
                baseline_id = current['observation_id']
                baseline_sequence = current.get('source', {}).get('sequence')
                probe_started_at = self.wall_time()
                try:
                    self.board.command(f'absmove {px} {py}')
                except Exception:
                    raise ActionError('Pointer probe outcome is uncertain; tap/swipe not sent. Inspect the screen before retrying', 503) from None
                deadline = self.monotonic() + self.probe_timeout_ms / 1000
                confirmed = None
                probe_polls = 0
                while self.monotonic() < deadline:
                    try:
                        candidate = self.observer.observe()
                    except Exception:
                        candidate = None
                    probe_polls += 1
                    if candidate is not None:
                        received = candidate.get('source', {}).get('received_at')
                        sequence = candidate.get('source', {}).get('sequence')
                        sequence_advanced = (baseline_sequence is None or sequence is None or
                                             sequence > baseline_sequence)
                        if (not candidate['stale'] and received is not None and
                                received > probe_started_at and sequence_advanced and
                                candidate['observation_id'] != baseline_id):
                            confirmed = candidate
                            break
                    self.sleep(max(0, min(self.poll_interval, deadline - self.monotonic())))
                if confirmed is None:
                    raise ActionError('Pointer moved without a verifiable new Mac frame; tap/swipe not sent', 503)
                current = confirmed
                probe = dict(performed=True, frame_confirmed=True,
                             point=[point[0], point[1]], polls=probe_polls,
                             duration_ms=round((self.monotonic() - probe_start) * 1000, 1))
            exact = current['observation_id'] == observation_id
            # Text targets are independently revalidated by exact text and position
            # below. Comparing the whole OCR page first makes harmless footer noise
            # (for example AirType URLs) block a still-visible target. Coordinates
            # without a text anchor still require the conservative layout check.
            action_anchor = None
            if x is not None and y is not None:
                action_anchor = [(x, y)]
                if action == 'swipe' and end_x is not None and end_y is not None:
                    action_anchor.append((end_x, end_y))
            if target_id is None and not exact and (not content_items(pinned) or
                                                     not same_layout(pinned, current, action_anchor)):
                raise ActionError('Screen no longer matches this action; no action sent. Observe again before deciding.')
            if target_id is not None:
                item = next((item for item in pinned['items'] if item['id'] == target_id), None)
                if item is None:
                    raise ActionError('target_id does not belong to that observation', 400)
                tolerance = max(3, min(current['width'], current['height']) * .01)
                matches = [candidate for candidate in current['items']
                           if normalized(candidate['text']) == normalized(item['text']) and
                           math.dist(candidate['center'], item['center']) <= tolerance]
                if len(matches) != 1:
                    raise ActionError('Target changed or is ambiguous; no action sent. Observe again before deciding.')
                x, y = matches[0]['center']
            width, height = current['width'], current['height']
            try:
                points = [hid_coordinate(x, width), hid_coordinate(y, height)]
                if action == 'swipe':
                    points.extend((hid_coordinate(end_x, width), hid_coordinate(end_y, height), duration_ms))
            except (ValueError, TypeError):
                raise ActionError('Coordinates must be inside the observed image', 400) from None
            line = ('abstap' if action == 'tap' else 'absdrag') + ' ' + ' '.join(map(str, points))
            result = dict(request_id=request_id, action=action, accepted=None, replayed=False,
                          status='uncertain', before_observation_id=current['observation_id'],
                          after_observation_id=None, changes=None,
                          probe=probe,
                          wait={'status': 'not_started', 'expected_text': wait_for_text},
                          timings_ms={'precheck': round((self.monotonic() - start) * 1000, 1),
                                      'board_status': round(board_ms, 1)})
            previous = self.journal.claim(request_id, digest, result)
            if previous is not None:
                return self.present(previous, True, changes_only, include_image)
            command_start = self.monotonic()
            try:
                self.board.command(line)
            except Exception:
                result['message'] = 'Action outcome uncertain. Inspect the phone; this request_id will not resend.'
            else:
                result.update(accepted=True, status='accepted')
            result['timings_ms']['command'] = round((self.monotonic() - command_start) * 1000, 1)
            finished_at = self.wall_time()
            self.journal.save(request_id, digest, result)
            if result['accepted']:
                wait_start = self.monotonic()
                try:
                    outcome = self.wait(current, finished_at, wait_for_text, timeout_ms)
                    result.update(outcome)
                except Exception:
                    result['wait'] = dict(status='observation_unavailable', expected_text=wait_for_text)
                result['timings_ms']['wait'] = round((self.monotonic() - wait_start) * 1000, 1)
            result['timings_ms']['total'] = round((self.monotonic() - start) * 1000, 1)
            self.journal.save(request_id, digest, result)
            return self.present(result, False, changes_only, include_image)
        except ValueError as exc:
            raise ActionError(str(exc)) from None
        finally:
            self.lock.release()

    def wait(self, before, finished_at, expected, timeout_ms):
        deadline = self.monotonic() + timeout_ms / 1000
        last, stable, previous_received = None, None, finished_at
        started = self.monotonic()
        stable_since = None
        stable_ms = 300 if expected is not None else 600
        observation_ms, polls = 0., 0
        wait_status = 'timeout'
        error = None
        while self.monotonic() < deadline:
            poll_start = self.monotonic()
            try:
                candidate = self.observer.observe()
            except Exception:
                error = 'observation_unavailable'
                self.sleep(max(0, min(self.poll_interval, deadline - self.monotonic())))
                continue
            observation_ms += (self.monotonic() - poll_start) * 1000
            polls += 1
            received = candidate.get('source', {}).get('received_at')
            if not candidate['stale'] and received is not None and received > previous_received:
                error = None
                last = candidate
                previous_received = received
                changed = not same_layout(before, candidate)
                found = expected is None or target_found(candidate, expected)
                if changed and found and content_items(candidate) and not loading_content(candidate):
                    now = self.monotonic()
                    if stable is None or not stable_content(stable, candidate):
                        stable_since = now
                    stable = candidate
                    if ((now - stable_since) * 1000 >= stable_ms and
                            (expected is not None or now - started >= .9)):
                        wait_status = 'condition_met' if expected is not None else 'changed'
                        break
                else:
                    stable, stable_since = None, None
            # A new JPEG or a changed clock is not a changed page.
            self.sleep(max(0, min(self.poll_interval, deadline - self.monotonic())))
        if error:
            wait_status = error
        elif last is None:
            wait_status = 'no_new_frame'
        result = dict(wait=dict(status=wait_status, expected_text=expected, polls=polls,
                                observation_ms=round(observation_ms, 1),
                                stable=wait_status in ('condition_met', 'changed'),
                                target_confirmed=wait_status == 'condition_met',
                                stable_required_ms=stable_ms),
                      after_observation_id=None, changes=None)
        if last is not None:
            result['after_observation_id'] = last['observation_id']
            result['changes'] = delta(before, last)
        return result

    def present(self, saved, replayed, changes_only, include_image):
        result = dict(saved, replayed=replayed)
        observation_id = result.get('after_observation_id')
        if observation_id:
            try:
                observation = self.observer.observe(include_image=include_image, observation_id=observation_id)
                if changes_only:
                    observation.pop('items', None)
                result['observation'] = observation
            except Exception:
                result['observation'] = None
                result['observation_note'] = 'Result frame expired; call iphone_observe for the current screen'
        return result

    @staticmethod
    def validate(action, observation_id, request_id, target_id, x, y, end_x, end_y,
                 duration_ms, wait_for_text, timeout_ms, changes_only, include_image):
        if action not in ('tap', 'swipe'):
            raise ActionError('action must be tap or swipe', 400)
        if not isinstance(observation_id, str) or not re.fullmatch(r'[0-9a-f]{64}', observation_id):
            raise ActionError('Use the observation_id returned by iphone_observe', 400)
        if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', request_id):
            raise ActionError('request_id must be a unique stable identifier (1..128 ASCII characters)', 400)
        if type(timeout_ms) is not int or not 200 <= timeout_ms <= 15000:
            raise ActionError('timeout_ms must be 200..15000', 400)
        if type(duration_ms) is not int or not 60 <= duration_ms <= 2000:
            raise ActionError('duration_ms must be 60..2000', 400)
        if wait_for_text is not None and (not isinstance(wait_for_text, str) or
                                         not normalized(wait_for_text) or len(wait_for_text) > 500):
            raise ActionError('wait_for_text must be nonempty text, at most 500 characters', 400)
        if type(changes_only) is not bool or type(include_image) is not bool:
            raise ActionError('changes_only and include_image must be booleans', 400)
        if target_id is not None:
            if action != 'tap' or type(target_id) is not int or target_id < 0 or any(
                    value is not None for value in (x, y, end_x, end_y)):
                raise ActionError('target_id is for tap only; do not mix it with coordinates', 400)
        else:
            points = (x, y, end_x, end_y) if action == 'swipe' else (x, y)
            if any(type(value) not in (int, float) or not math.isfinite(value) for value in points):
                raise ActionError('Provide finite pixel coordinates or a tap target_id', 400)
            if action == 'tap' and (end_x is not None or end_y is not None):
                raise ActionError('tap does not accept end coordinates', 400)
