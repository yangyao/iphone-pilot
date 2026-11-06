import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError

from tools.airtype_input import AirTypeInput, InputError, keyboard_connection


def screen(extra=(), code='123456'):
    def item(text, y):
        return dict(text=text, center=[200, y], confidence=.99)
    return dict(stale=False, height=1080, observation_id='frame-id', items=[
        item('AirType Web Access', 800), item('http://192.168.2.140:8307/', 850),
        item('配对码 ' + code, 900), *[item(text, 300) for text in extra]])


class FakeObserver:
    def __init__(self):
        self.current = screen()

    def observe(self):
        return copy.deepcopy(self.current)


class InputTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.observer = FakeObserver()
        self.requests = []
        self.uncertain = False
        self.change_keyboard = False
        self.reject_session = False
        owner = self

        class Client:
            token = ''

            def __init__(self, host, port):
                self.base = f'http://{host}:{port}'

            def request(self, path):
                owner.requests.append(('status', self.token))
                if owner.reject_session:
                    raise HTTPError(self.base, 401, 'expired', {}, None)
                return {'connected': True}

            def pair(self, code):
                owner.requests.append(('pair', code))
                self.token = 'private-test-token'
                if owner.change_keyboard:
                    owner.observer.current = screen(code='654321')

            def insert(self, text, command_id):
                owner.requests.append(('insert', text, command_id))
                # Simulate a timeout AFTER the keyboard accepted the content.
                owner.observer.current = screen(extra=(text,))
                if owner.uncertain:
                    raise TimeoutError('reply lost')
                return {'accepted': True}

        self.client_factory = Client
        self.inputter = self.make_inputter()

    def make_inputter(self):
        return AirTypeInput(self.observer, self.directory.name, self.client_factory, verify_attempts=1)

    def test_one_call_pairs_inserts_and_detects_unicode(self):
        text = '中文、繁體🙂\n第二段。'
        result = self.inputter.insert(text, 'draft-1', allow_newlines=True)
        self.assertEqual([request[0] for request in self.requests], ['pair', 'insert'])
        self.assertTrue(result['accepted'])
        self.assertFalse(result['submitted'])
        self.assertEqual(result['verification']['status'], 'text_detected_after_input')
        self.assertNotIn('private-test-token', json.dumps(result))
        self.assertNotIn(text, Path(self.directory.name, 'airtype-input.sqlite').read_bytes().decode(errors='ignore'))

    def test_success_replay_survives_restart_and_cannot_change_text(self):
        self.inputter.insert('第一条', 'draft-1')
        result = self.make_inputter().insert('第一条', 'draft-1')
        self.assertTrue(result['replayed'])
        self.assertEqual(sum(request[0] == 'insert' for request in self.requests), 1)
        with self.assertRaises(InputError):
            self.inputter.insert('另一条', 'draft-1')

    def test_uncertain_result_never_reinserts_even_after_restart(self):
        self.uncertain = True
        first = self.inputter.insert('可能已输入', 'uncertain-1')
        self.assertEqual(first['status'], 'uncertain')
        self.assertIsNone(first['accepted'])
        second = self.make_inputter().insert('可能已输入', 'uncertain-1')
        self.assertTrue(second['replayed'])
        self.assertEqual(sum(request[0] == 'insert' for request in self.requests), 1)

    def test_pending_journal_entry_after_crash_cannot_resend(self):
        original_claim = self.inputter.claim
        def save_then_crash(*args):
            original_claim(*args)
            raise RuntimeError('simulated process exit after durable pre-insert journal')
        self.inputter.claim = save_then_crash
        with self.assertRaises(RuntimeError):
            self.inputter.insert('防止重发', 'crash-1')
        result = self.make_inputter().insert('防止重发', 'crash-1')
        self.assertEqual(result['status'], 'uncertain')
        self.assertFalse(any(request[0] == 'insert' for request in self.requests))

    def test_two_instances_share_one_atomic_input_claim(self):
        other = self.make_inputter()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda service: service.insert('同一输入', 'shared-1'),
                                    (self.inputter, other)))
        self.assertEqual(sum(request[0] == 'insert' for request in self.requests), 1)
        self.assertEqual(sum(result['replayed'] for result in results), 1)

    def test_reuses_session_and_repairs_auth_failure_before_insertion(self):
        self.inputter.insert('第一条', 'draft-1')
        self.inputter.insert('第二条', 'draft-2')
        self.assertEqual(sum(request[0] == 'pair' for request in self.requests), 1)
        self.reject_session = True
        self.inputter.insert('第三条', 'draft-3')
        self.assertEqual(sum(request[0] == 'pair' for request in self.requests), 2)
        self.assertEqual(sum(request[0] == 'insert' for request in self.requests), 3)

    def test_changed_keyboard_stops_before_insert(self):
        self.change_keyboard = True
        with self.assertRaisesRegex(InputError, 'changed'):
            self.inputter.insert('不要发', 'changed-1')
        self.assertFalse(any(request[0] == 'insert' for request in self.requests))

    def test_existing_text_is_not_claimed_as_verified(self):
        self.observer.current = screen(extra=('原本就存在',))
        result = self.inputter.insert('原本就存在', 'existing-1')
        self.assertEqual(result['verification']['status'], 'not_confirmed')

    def test_newlines_stale_screen_and_busy_do_not_insert(self):
        with self.assertRaises(InputError):
            self.inputter.insert('多\n行', 'newlines-1')
        self.observer.current['stale'] = True
        with self.assertRaises(InputError):
            self.inputter.insert('旧画面', 'stale-1')
        self.inputter.lock.acquire()
        try:
            with self.assertRaises(InputError):
                self.inputter.insert('同时输入', 'busy-1')
        finally:
            self.inputter.lock.release()
        self.assertEqual(self.requests, [])

    def test_code_extraction_rejects_ambiguity_and_uncertainty(self):
        client, code = keyboard_connection(screen(code='１２３ ４５６'))
        self.assertEqual(code, '123456')
        self.assertEqual(client.base, 'http://192.168.2.140:8307')
        ambiguous = screen()
        ambiguous['items'].append(dict(text='654321', center=[200, 920], confidence=.99))
        with self.assertRaises(InputError):
            keyboard_connection(ambiguous)
        low_confidence = screen()
        low_confidence['items'][1]['confidence'] = .2
        with self.assertRaises(InputError):
            keyboard_connection(low_confidence)
        uncertain = screen(code='I23456')
        self.assertIsNone(keyboard_connection(uncertain)[1])

    def test_keyboard_ip_and_local_alias_are_one_panel_not_ambiguity(self):
        current = screen()
        current['items'].append(dict(text='http://airtype.local:8307/', center=[200, 870], confidence=1.))
        client, code = keyboard_connection(current)
        self.assertEqual(client.base, 'http://192.168.2.140:8307')
        self.assertEqual(code, '123456')

    def test_competing_ips_or_ports_remain_ambiguous(self):
        for text, y in [('http://192.168.2.141:8307/', 870),
                        ('http://airtype.local:7777/', 870),
                        ('http://airtype.local:8307/', 1000)]:
            current = screen()
            current['items'].append(dict(text=text, center=[200, y], confidence=1.))
            with self.subTest(text=text, y=y), self.assertRaises(InputError):
                keyboard_connection(current)

    def test_cropped_footer_can_supply_a_confident_url_without_repairing_bad_ocr(self):
        current = screen()
        current['items'][1].update(text='http://192.168:2.140:8307/', confidence=.3)
        current['items'].extend([
            dict(text='http://airtype.local:8307/', center=[200, 945], confidence=1., ocr_region='airtype-footer'),
            dict(text='http://192.168.2.140:8307/', center=[200, 962], confidence=1., ocr_region='airtype-footer')])
        client, code = keyboard_connection(current)
        self.assertEqual(client.base, 'http://192.168.2.140:8307')
        self.assertEqual(code, '123456')


if __name__ == '__main__':
    unittest.main()
