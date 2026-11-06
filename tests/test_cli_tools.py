"""Unit tests for the CLI-side helpers added to tools/iphone.py."""
import importlib.util
import json
from pathlib import Path
import struct
import unittest

import sys
TOOLS = Path(__file__).resolve().parents[1] / 'tools'
sys.path.insert(0, str(TOOLS))
spec = importlib.util.spec_from_file_location('iphone_cli', TOOLS / 'iphone.py')
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


def observation(items=(), airtype=None):
    return {'items': list(items), 'airtype': airtype or {'detected': False}}


def item(text, x, y):
    return {'text': text, 'center': [x, y]}


def make_bmp(width, height, pixel_at, bpp=24):
    row_size = ((bpp * width + 31) // 32) * 4
    offset = 14 + 40
    body = bytearray()
    rows = [bytes(pixel_at(x, y)) for y in range(height) for x in (0,)]
    for y in range(height - 1, -1, -1):  # bottom-up like sips output
        row = bytearray()
        for x in range(width):
            r, g, b = pixel_at(x, y)
            row += bytes((b, g, r)) + (b'\0' if bpp == 32 else b'')
        body += row + b'\0' * (row_size - len(row))
    header = b'BM' + struct.pack('<IHHI', offset + len(body), 0, 0, offset)
    dib = struct.pack('<IiiHHIIiiII', 40, width, height, 1, bpp, 0, len(body), 0, 0, 0, 0)
    return header + dib + bytes(body)


class KeyboardDetection(unittest.TestCase):
    def test_airtype_report_counts_as_keyboard(self):
        self.assertTrue(cli.keyboard_present(observation(airtype={'detected': True})))

    def test_keyboard_token_detected(self):
        self.assertTrue(cli.keyboard_present(observation([item('空格', 222, 951)])))

    def test_plain_page_is_not_keyboard(self):
        self.assertFalse(cli.keyboard_present(observation([item('淘宝', 250, 100)])))


class PanelClose(unittest.TestCase):
    def test_finds_cross_in_panel_area(self):
        items = [item('✕', 455, 951), item('网页输入', 124, 855)]
        self.assertEqual(cli.panel_close_point(observation(items)), [455, 951])

    def test_ignores_cross_outside_panel_area(self):
        items = [item('✕', 455, 183)]  # top of page, not the input panel
        self.assertIsNone(cli.panel_close_point(observation(items)))


class KeyboardTop(unittest.TestCase):
    def test_returns_min_top(self):
        items = [item('空格', 222, 951), item('拼字检查', 248, 692)]
        self.assertEqual(cli.keyboard_top(observation(items)), 692)

    def test_none_without_keyboard(self):
        self.assertIsNone(cli.keyboard_top(observation([item('淘宝', 250, 100)])))


class CollapsePoint(unittest.TestCase):
    def test_finds_chevron_directly_above_keyboard(self):
        items = [item('发布', 250, 1034), item('∨', 463, 684), item('空格', 222, 951)]
        self.assertEqual(cli.collapse_point(observation(items)), [463, 684])

    def test_ignores_chevron_far_above_keyboard(self):
        items = [item('V', 463, 180), item('空格', 222, 951)]
        self.assertIsNone(cli.collapse_point(observation(items)))

    def test_ignores_left_side_tokens(self):
        items = [item('く', 124, 684), item('空格', 222, 951)]
        self.assertIsNone(cli.collapse_point(observation(items)))

    def test_no_keyboard_no_point(self):
        self.assertIsNone(cli.collapse_point(observation([item('∨', 463, 684)])))


class TapCoordinateParsing(unittest.TestCase):
    def make_args(self, action='tap', xy=None, coords=(), ms=400):
        return type('Args', (), {'action': action, 'xy': xy, 'coords': list(coords), 'ms': ms})()

    def test_positional_pair_is_coordinates(self):
        self.assertEqual(cli.coordinates_for(self.make_args(coords=[421, 655]), 'tap'),
                         dict(x=421.0, y=655.0))

    def test_single_positional_is_target_id(self):
        self.assertEqual(cli.coordinates_for(self.make_args(coords=[12]), 'tap'),
                         dict(target_id=12))

    def test_flag_pair_wins(self):
        self.assertEqual(cli.coordinates_for(self.make_args(xy=[10, 20], coords=[1, 2]), 'tap'),
                         dict(x=10.0, y=20.0))

    def test_swipe_needs_four_values(self):
        self.assertEqual(cli.coordinates_for(self.make_args('swipe', coords=[1, 2, 3, 4]), 'swipe'),
                         dict(x=1.0, y=2.0, end_x=3.0, end_y=4.0, duration_ms=400))
        with self.assertRaises(cli.PilotError):
            cli.coordinates_for(self.make_args('swipe', coords=[1, 2]), 'swipe')


class BmpSampling(unittest.TestCase):
    def test_parses_and_scales_24bpp(self):
        bmp = make_bmp(4, 4, lambda x, y: (250, 120, 10) if (x, y) == (2, 1) else (0, 0, 0))
        width, height, pixels = cli.parse_bmp_pixels(bmp)
        self.assertEqual((width, height), (4, 4))
        self.assertEqual(pixels[1][2], (250, 120, 10))

    def test_sample_color_reports_average(self):
        bmp = make_bmp(8, 8, lambda x, y: (250, 120, 10))
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as stream:
            stream.write(b'\xff\xd8stub')  # contents irrelevant; sips is stubbed below
            path = Path(stream.name)

        calls = []

        def fake_run(command, **kwargs):
            calls.append(command)
            bmp_path = Path(command[-1])
            bmp_path.write_bytes(make_bmp(8, 8, lambda x, y: (250, 120, 10)))

        original = cli.subprocess.run
        cli.subprocess.run = fake_run
        try:
            result = cli.sample_color(path.read_bytes(), [(4, 4)], 8, 8)
        finally:
            cli.subprocess.run = original
            path.unlink(missing_ok=True)
        self.assertEqual(result[0]['rgb'], [250, 120, 10])
        self.assertEqual(result[0]['hex'], '#fa780a')
        self.assertEqual(calls[0][:3], ['sips', '-s', 'format', 'bmp'][:3])

    def test_sample_color_scales_from_ocr_space(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as stream:
            stream.write(b'\xff\xd8stub')
            path = Path(stream.name)

        def fake_run(command, **kwargs):
            Path(command[-1]).write_bytes(make_bmp(16, 16, lambda x, y: (9, 9, 9)))

        original = cli.subprocess.run
        cli.subprocess.run = fake_run
        try:
            result = cli.sample_color(path.read_bytes(), [(8, 8)], 8, 8, radius=1)
        finally:
            cli.subprocess.run = original
            path.unlink(missing_ok=True)
        self.assertEqual(result[0]['rgb'], [9, 9, 9])

    def test_sample_color_supports_multiple_points(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as stream:
            stream.write(b'\xff\xd8stub')
            path = Path(stream.name)

        def fake_run(command, **kwargs):
            Path(command[-1]).write_bytes(
                make_bmp(8, 8, lambda x, y: (250, 120, 10) if x < 4 else (0, 0, 0)))

        original = cli.subprocess.run
        cli.subprocess.run = fake_run
        try:
            result = cli.sample_color(path.read_bytes(), [(1, 4), (6, 4)], 8, 8, radius=0)
        finally:
            cli.subprocess.run = original
            path.unlink(missing_ok=True)
        self.assertEqual([sample['rgb'] for sample in result], [[250, 120, 10], [0, 0, 0]])


class FilledAnnotation(unittest.TestCase):
    def test_marks_close_colors_filled(self):
        samples = [{'rgb': [227, 118, 21]}, {'rgb': [142, 89, 42]}, {'rgb': [235, 235, 235]}]
        annotated = cli.with_filled(samples, (227, 118, 21), 60)
        self.assertEqual([sample['filled'] for sample in annotated], [True, False, False])
        self.assertEqual(annotated[0]['distance'], 0.0)

    def test_tolerance_boundary(self):
        samples = [{'rgb': [227, 118, 81]}]
        self.assertTrue(cli.with_filled(samples, (227, 118, 21), 60)[0]['filled'])
        self.assertFalse(cli.with_filled(samples, (227, 118, 21), 59)[0]['filled'])

    def test_multiple_references_match_any(self):
        samples = [{'rgb': [193, 36, 44]}]
        annotated = cli.with_filled(samples, [(227, 118, 21), (193, 36, 44)], 60)
        self.assertTrue(annotated[0]['filled'])
        self.assertEqual(annotated[0]['distance'], 0.0)


class InsertionMatch(unittest.TestCase):
    def test_prefix_across_wrapped_lines(self):
        items = [item('山刊', 36, 360), item('宗，面条爽滑有弹性', 270, 382)]
        self.assertTrue(cli.insertion_matched(
            items, '出前一丁方便面收到了，5袋装分量十足，麻油味香浓正宗，面条爽滑有弹性'))

    def test_tail_matches_when_head_ocr_garbled(self):
        items = [item('山刊，刀文国划】，衣衣力里一天，那', 270, 360),
                 item('值得回购，五星好评！', 107, 455)]
        self.assertTrue(cli.insertion_matched(
            items, '出前一丁方便面收到了，物流速度快，客服态度好，整体购物体验满意，值得回购，五星好评！'))

    def test_false_when_neither_head_nor_tail(self):
        items = [item('完全无关的内容', 100, 400)]
        self.assertFalse(cli.insertion_matched(
            items, '出前一丁方便面收到了，值得回购，五星好评！'))

    def test_short_text_uses_full_match(self):
        items = [item('你好世界', 100, 400)]
        self.assertTrue(cli.insertion_matched(items, '你好世界'))
        self.assertFalse(cli.insertion_matched(items, '不存在的文本'))


    def test_layout_unchanged(self):
        before = [item('综合评分', 47, 372), item('★', 280, 370), item('提交评价', 250, 777)]
        same = [item('综合评分', 47, 372), item('★', 280, 370), item('提交评价', 250, 777)]
        changed = [item('感谢你的评价', 266, 99), item('我的订单', 250, 98)]
        self.assertTrue(cli.layout_unchanged(before, same))
        self.assertFalse(cli.layout_unchanged(before, changed))
        self.assertFalse(cli.layout_unchanged([], same))

    def test_dynamic_page_resend_once(self):
        # 动态页面（两帧预览不一致）：第一次盲发补击允许，第二次必须停止
        state = {'observation_id': 'obsA', 'requests': {}}

        class Args:
            action = 'tap'; request_id = 'r1'; observation_id = 'obsA'
            timeout = 100; wait = '很好'; retry = 3; include_image = False
            session = Path('/tmp/test-session-cli.json'); xy = [280.0, 370.0]; coords = []
        args = Args()
        Path('/tmp/test-session-cli.json').unlink(missing_ok=True)
        responses = {'/act': [
            dict(observation=dict(observation_id='post1', items=[item('★', 280, 370)]),
                 wait=dict(status='timeout')),
            dict(observation=dict(observation_id='post2', items=[item('很好', 323, 371)]),
                 wait=dict(status='condition_met')),
        ]}

        class FakeAPI:
            base = 'http://127.0.0.1:18766'
            def observe(self, observation_id=None, include_image=False):
                if observation_id == 'obsA':
                    return dict(observation_id='obsA', items=[item('★', 280, 370)])
                return dict(observation_id='preX', items=[item('★', 280, 370)])
            def request(self, route, payload):
                if route == '/act':
                    return responses['/act'].pop(0)
                return {}
        api = FakeAPI()
        result = cli.run(args, api)
        journal = json.loads(args.session.read_text())['requests']
        fired = [rid for rid in journal if journal[rid]['route'] == '/act']
        self.assertLessEqual(len(fired), 3)

    def test_static_page_never_blind_resend(self):
        # 静态页面滚动后（布局变化）：绝不补发
        state = {'observation_id': 'obsA', 'requests': {}}

        class Args:
            action = 'tap'; request_id = 'r2'; observation_id = 'obsA'
            timeout = 100; wait = '很好'; retry = 3; include_image = False
            session = Path('/tmp/test-session-cli.json'); xy = [280.0, 370.0]; coords = []
        args = Args()
        Path('/tmp/test-session-cli.json').unlink(missing_ok=True)
        acts = [dict(observation=dict(observation_id='post1', items=[item('提交评价', 250, 500)]),
                     wait=dict(status='timeout'))]

        class FakeAPI:
            base = 'http://127.0.0.1:18766'
            def observe(self, observation_id=None, include_image=False):
                # 静态页：无 id 的最新帧与 pre-tap 帧一致（页面没有自己变动），
                # 但 tap 后布局变化（滚动导致提交按钮移位）
                if observation_id == 'post1':
                    return dict(observation_id='post1', items=[item('提交评价', 250, 500)])
                return dict(observation_id=observation_id or 'preX', items=[item('★', 280, 370)])
            def request(self, route, payload):
                if route == '/act':
                    return acts.pop(0)
                return {}
        api = FakeAPI()
        result = cli.run(args, api)
        journal = json.loads(args.session.read_text())['requests']
        fired = [rid for rid in journal if journal[rid]['route'] == '/act']
        self.assertEqual(len(fired), 1)  # 只有点击本身，没有盲发


class BoardDiscovery(unittest.TestCase):
    def test_local_subnets_filters_fake_and_loopback(self):
        def fake_run(command, **kwargs):
            class Result:
                stdout = ('lo0: inet 127.0.0.1\n'
                          'en0: inet 192.168.2.10\n'
                          'utun5: inet 198.18.0.1\n'
                          'awdl0: inet 169.254.55.9\n')
            return Result()
        original = cli.subprocess.run
        cli.subprocess.run = fake_run
        try:
            self.assertEqual(cli.local_subnets(), ['192.168.2'])
        finally:
            cli.subprocess.run = original

    def test_reports_listening_hosts_sorted(self):
        opened = []

        class FakeConn:
            def __init__(self, ip):
                self.ip = ip

            def close(self):
                opened.append(self.ip)

        def fake_create_connection(address, timeout=None):
            ip, port = address
            if int(ip.rsplit('.', 1)[1]) in (42, 7, 201):
                return FakeConn(ip)
            raise OSError('refused')

        original = cli.socket.create_connection
        cli.socket.create_connection = fake_create_connection
        try:
            found = cli.discover_board(timeout=0.1)
        finally:
            cli.socket.create_connection = original
        self.assertEqual(sorted(ip.rsplit('.', 1)[1] for ip in found),
                         ['201', '201', '42', '42', '7', '7'])
        self.assertEqual(len({ip.rsplit('.', 1)[0] for ip in found}), 2)  # both scanned /24s
        self.assertEqual(len(opened), 6)

    def test_discover_handles_total_refusal(self):
        original = cli.socket.create_connection
        cli.socket.create_connection = lambda address, timeout=None: (_ for _ in ()).throw(OSError())
        try:
            self.assertEqual(cli.discover_board(timeout=0.05), [])
        finally:
            cli.socket.create_connection = original


class TapsParsing(unittest.TestCase):
    def test_parses_multiple_pairs(self):
        arguments = cli.parser().parse_args(['taps', '--id', 's1', '--xy', '379', '350',
                                             '379', '403', '379', '456', '--expect', '描述相符'])
        self.assertEqual(arguments.xy, [379.0, 350.0, 379.0, 403.0, 379.0, 456.0])

    def test_taps_parse_requires_expect(self):
        arguments = cli.parser().parse_args(['taps', '--id', 's1', '--xy', '379', '350',
                                             '379', '403', '379', '456', '--expect', '描述相符'])
        self.assertEqual(arguments.expect, '描述相符')

    def test_pixel_compare_and_tolerance(self):
        arguments = cli.parser().parse_args(['pixel', '--xy', '379', '456',
                                             '--compare', '227', '118', '21', '--tolerance', '40'])
        self.assertEqual(arguments.compare, [227.0, 118.0, 21.0])
        self.assertEqual(arguments.tolerance, 40.0)


class Parser(unittest.TestCase):
    def test_tap_positional_pair(self):
        arguments = cli.parser().parse_args(['tap', '421', '655', '--id', 'p1'])
        self.assertEqual(arguments.coords, ['421', '655'])
        self.assertIsNone(arguments.xy)

    def test_tap_flag_pair(self):
        arguments = cli.parser().parse_args(['tap', '--xy', '421', '655', '--id', 'p1'])
        self.assertEqual(arguments.xy, [421.0, 655.0])

    def test_tap_target_id(self):
        arguments = cli.parser().parse_args(['tap', '12', '--id', 'p1'])
        self.assertEqual(arguments.coords, ['12'])

    def test_swipe_both_forms(self):
        positional = cli.parser().parse_args(['swipe', '250', '1050', '250', '400', '--id', 'd1'])
        flagged = cli.parser().parse_args(['swipe', '--xy', '250', '1050', '250', '400', '--id', 'd1'])
        self.assertEqual([float(v) for v in positional.coords], flagged.xy)

    def test_swipe_rejects_short_coordinates(self):
        arguments = cli.parser().parse_args(['swipe', '250', '1050', '--id', 'd1'])
        self.assertEqual(arguments.coords, ['250', '1050'])  # parse keeps tokens; run() rejects

    def test_scroll_parse(self):
        arguments = cli.parser().parse_args(['scroll', '--id', 's1', '--until', '立即评价',
                                             '--max', '3', '--dir', 'down'])
        self.assertEqual(arguments.until, '立即评价')
        self.assertEqual((arguments.max, arguments.dir), (3, 'down'))

    def test_new_commands_parse(self):
        self.assertEqual(cli.parser().parse_args(['wait', '发布', '--gone', '--timeout', '3000']).text, '发布')
        self.assertEqual(cli.parser().parse_args(['dismiss', '--id', 'k1']).request_id, 'k1')
        self.assertEqual(cli.parser().parse_args(['pixel', '--xy', '379', '456']).xy, [379.0, 456.0])
        multi = cli.parser().parse_args(['pixel', '--xy', '379', '350', '379', '403', '379', '456'])
        self.assertEqual(multi.xy, [379.0, 350.0, 379.0, 403.0, 379.0, 456.0])
        board = cli.parser().parse_args(['board', 'discover', '--save'])
        self.assertEqual((board.board_action, board.save), ('discover', True))
        tap_retry = cli.parser().parse_args(['tap', '12', '--id', 'p1', '--retry', '5'])
        self.assertEqual(tap_retry.retry, 5)


if __name__ == '__main__':
    unittest.main()
