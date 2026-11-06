import base64
import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import tempfile
import threading
import time
import unittest

from tools.action_runtime import ActionError, PhoneActions
from tools.airtype_input import AirTypeInput, InputError


class Clock:
    def __init__(self):
        self.value = 0.
        self.epoch = time.time()

    def monotonic(self):
        return self.value

    def wall_time(self):
        return self.epoch + self.value

    def sleep(self, duration):
        self.value += duration


def page(name, lines):
    return dict(observation_id=hashlib.sha256(name.encode()).hexdigest(), width=498, height=1080,
                stale=False, items=[dict(id=i, text=text, center=[250, y], confidence=.99,
                                         clickable=None) for i, (text, y) in enumerate(lines)])


BEFORE = page('before', [('我的京东', 200), ('待评价', 500)])
AFTER = page('after', [('待评价商品', 200), ('去评价', 500)])


class Observer:
    def __init__(self, clock):
        self.clock = clock
        self.current = copy.deepcopy(BEFORE)
        self.after_pages = [copy.deepcopy(AFTER)]
        self.acting = False
        self.cache = {BEFORE['observation_id']: copy.deepcopy(BEFORE)}
        self.new_frames = True
        self.fail_after = False

    def observe(self, include_image=False, observation_id=None):
        if observation_id:
            value = copy.deepcopy(self.cache[observation_id])
        else:
            self.clock.sleep(.01)
            if self.acting and self.fail_after:
                raise OSError('receiver disconnected')
            if self.acting:
                self.current = self.after_pages[0]
                if len(self.after_pages) > 1:
                    self.after_pages.pop(0)
            value = copy.deepcopy(self.current)
            value['source'] = dict(received_at=self.clock.wall_time() if self.new_frames else self.clock.epoch,
                                   metadata_exact=True, sequence=1)
            self.cache[value['observation_id']] = copy.deepcopy(value)
        if include_image:
            value['image'] = dict(mime_type='image/jpeg', data=base64.b64encode(b'fake JPEG').decode())
        return value


class Board:
    def __init__(self, observer, clock):
        self.observer, self.clock = observer, clock
        self.commands = []
        self.state = dict(usb='ok', busy=0, owner='none')
        self.fail_command = False
        self.on_status = None

    def status(self):
        if self.on_status:
            self.on_status()
        return self.state

    def command(self, line):
        self.commands.append(line)
        self.clock.sleep(.05)
        self.observer.acting = True
        if self.fail_command:
            raise TimeoutError('reply lost after HID action')
        return 'ok'


class ActionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.clock = Clock()
        self.observer = Observer(self.clock)
        self.board = Board(self.observer, self.clock)
        self.lock = threading.Lock()
        self.actor = self.make_actor()
        self.arguments = dict(action='tap', observation_id=BEFORE['observation_id'],
                              request_id='tap-1', target_id=1, wait_for_text='待评价商品', timeout_ms=1000)

    def make_actor(self):
        return PhoneActions(self.observer, self.board, self.directory.name, lock=self.lock,
                            monotonic=self.clock.monotonic, wall_time=self.clock.wall_time,
                            sleep=self.clock.sleep)

    def test_tap_wait_and_result_in_one_call(self):
        result = self.actor.act(**self.arguments)
        self.assertEqual(self.board.commands, ['abstap 16449 15170'])
        self.assertTrue(result['accepted'])
        self.assertEqual(result['wait']['status'], 'condition_met')
        self.assertEqual(result['wait']['polls'], 2)
        self.assertEqual(result['observation']['observation_id'], AFTER['observation_id'])
        self.assertEqual([item['text'] for item in result['changes']['added']], ['待评价商品', '去评价'])
        self.assertGreater(result['timings_ms']['total'], 0)
        self.assertIn('observation_ms', result['wait'])

    def test_swipe_returns_compact_text_and_optional_image(self):
        result = self.actor.act(action='swipe', observation_id=BEFORE['observation_id'],
                                request_id='swipe-1', x=200, y=800, end_x=200, end_y=300,
                                changes_only=True, include_image=True)
        self.assertTrue(self.board.commands[0].startswith('absdrag '))
        self.assertTrue(self.board.commands[0].endswith(' 400'))
        self.assertEqual(result['wait']['status'], 'changed')
        self.assertNotIn('items', result['observation'])
        self.assertEqual(base64.b64decode(result['observation']['image']['data']), b'fake JPEG')

    def test_refuses_changed_page_and_changed_page_during_board_check(self):
        self.board.on_status = lambda: setattr(self.observer, 'current', copy.deepcopy(AFTER))
        with self.assertRaisesRegex(ActionError, 'Target changed or is ambiguous'):
            self.actor.act(**self.arguments)
        self.assertEqual(self.board.commands, [])

    def test_allows_jpeg_change_and_small_ocr_jitter_on_same_layout(self):
        current = copy.deepcopy(BEFORE)
        current['observation_id'] = hashlib.sha256(b'JPEG encoding changed').hexdigest()
        current['items'][1]['center'][0] += 2
        self.observer.current = current
        result = self.actor.act(**self.arguments)
        self.assertTrue(result['accepted'])

    def test_moved_target_and_invalid_id_do_not_click(self):
        self.observer.current = copy.deepcopy(BEFORE)
        self.observer.current['observation_id'] = hashlib.sha256(b'scrolled').hexdigest()
        self.observer.current['items'][1]['center'][1] -= 100
        with self.assertRaises(ActionError):
            self.actor.act(**self.arguments)
        self.observer.current = copy.deepcopy(BEFORE)
        with self.assertRaises(ActionError):
            self.actor.act(**dict(self.arguments, target_id=999))
        self.assertEqual(self.board.commands, [])

    def test_stale_live_screen_and_asleep_usb_do_not_click(self):
        self.observer.current['stale'] = True
        with self.assertRaises(ActionError):
            self.actor.act(**self.arguments)
        self.assertEqual(len(self.board.commands), 1)
        self.assertTrue(self.board.commands[0].startswith('absmove '))
        self.observer.current['stale'] = False
        self.board.state['usb'] = 'asleep'
        with self.assertRaises(ActionError):
            self.actor.act(**self.arguments)
        self.assertEqual(len(self.board.commands), 1)

    def test_acknowledgment_and_new_jpeg_are_not_page_success(self):
        self.observer.after_pages = [copy.deepcopy(BEFORE)]
        result = self.actor.act(**self.arguments)
        self.assertTrue(result['accepted'])
        self.assertEqual(result['wait']['status'], 'timeout')
        self.assertEqual(result['changes'], dict(added=[], removed=[]))
        self.assertEqual(len(self.board.commands), 1)

    def test_no_new_frames_and_receiver_failure_are_reported(self):
        self.observer.new_frames = False
        result = self.actor.act(**self.arguments)
        self.assertEqual(result['wait']['status'], 'no_new_frame')
        self.assertIsNone(result['after_observation_id'])
        self.observer.fail_after = True
        # Querying an existing request returns its outcome without touching the board.
        again = self.actor.act(**self.arguments)
        self.assertTrue(again['replayed'])
        self.assertEqual(len(self.board.commands), 1)

    def test_receiver_error_after_ack_does_not_lose_action_result(self):
        self.observer.fail_after = True
        result = self.actor.act(**self.arguments)
        self.assertTrue(result['accepted'])
        self.assertEqual(result['wait']['status'], 'observation_unavailable')

    def test_replay_survives_restart_and_rejects_changed_intent(self):
        self.actor.act(**self.arguments)
        result = self.make_actor().act(**dict(self.arguments, changes_only=True))
        self.assertTrue(result['replayed'])
        self.assertEqual(len(self.board.commands), 1)
        with self.assertRaisesRegex(ActionError, 'different action'):
            self.actor.act(**dict(self.arguments, target_id=0))

    def test_uncertain_command_is_not_replayed(self):
        self.board.fail_command = True
        first = self.actor.act(**self.arguments)
        second = self.make_actor().act(**self.arguments)
        self.assertEqual(first['status'], 'uncertain')
        self.assertIsNone(first['accepted'])
        self.assertTrue(second['replayed'])
        self.assertEqual(len(self.board.commands), 1)

    def test_pending_claim_after_crash_is_not_replayed(self):
        claim = self.actor.journal.claim
        def crash(*args):
            claim(*args)
            raise RuntimeError('process exit before command')
        self.actor.journal.claim = crash
        with self.assertRaises(RuntimeError):
            self.actor.act(**self.arguments)
        result = self.make_actor().act(**self.arguments)
        self.assertEqual(result['status'], 'uncertain')
        self.assertTrue(result['replayed'])
        self.assertEqual(self.board.commands, [])

    def test_durable_claim_is_atomic_across_runtime_instances(self):
        from tools.operation_journal import OperationJournal
        first = self.actor.journal
        second = OperationJournal(first.path)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda journal: journal.claim('shared-1', 'same-digest', {'status': 'uncertain'}),
                                    (first, second)))
        self.assertEqual(sum(result is None for result in results), 1)
        self.assertEqual(sum(result == {'status': 'uncertain'} for result in results), 1)

    def test_action_and_input_share_interaction_lock(self):
        inputter = AirTypeInput(self.observer, self.directory.name, lock=self.lock)
        self.assertIs(self.actor.lock, inputter.lock)
        self.lock.acquire()
        try:
            with self.assertRaisesRegex(ActionError, 'busy'):
                self.actor.act(**self.arguments)
            with self.assertRaises(InputError):
                inputter.insert('不能同时输入', 'input-1')
        finally:
            self.lock.release()

    def test_clock_only_change_does_not_confirm_navigation(self):
        clock_changed = copy.deepcopy(BEFORE)
        clock_changed['items'].append(dict(id=2, text='09:42', center=[40, 35]))
        clock_changed['observation_id'] = hashlib.sha256(b'clock').hexdigest()
        self.observer.after_pages = [clock_changed]
        result = self.actor.act(**dict(self.arguments, wait_for_text=None))
        self.assertEqual(result['wait']['status'], 'timeout')

    def test_transient_target_text_does_not_count_as_stable(self):
        transient = page('transient', [('待评价商品', 250)])
        self.observer.after_pages = [transient, copy.deepcopy(BEFORE)]
        result = self.actor.act(**self.arguments)
        self.assertEqual(result['wait']['status'], 'timeout')

    def test_empty_loading_frames_are_not_returned_as_stable_content(self):
        empty = page('blank loading frame', [])
        self.observer.after_pages = [empty, empty, copy.deepcopy(AFTER)]
        result = self.actor.act(**dict(self.arguments, wait_for_text=None))
        self.assertEqual(result['wait']['status'], 'changed')
        self.assertEqual(result['after_observation_id'], AFTER['observation_id'])

    def test_invalid_actions_and_coordinates_do_not_send(self):
        for invalid in [dict(action='command'), dict(target_id=True), dict(timeout_ms=99999),
                        dict(target_id=None, x=-1, y=10), dict(x=2), dict(wait_for_text='  '),
                        dict(target_id=None, x=float('nan'), y=5), dict(duration_ms=1)]:
            with self.subTest(invalid=invalid):
                with self.assertRaises(ActionError):
                    self.actor.act(**dict(self.arguments, **invalid))
        self.assertEqual(self.board.commands, [])


if __name__ == '__main__':
    unittest.main()
