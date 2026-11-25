"""后台窗口工具的协议、拒绝路径及真实 Win32 消息集成测试。"""
import importlib
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

BASE = Path(__file__).resolve().parents[1]


def module():
    if not (BASE / 'gameqakit/background.py').is_file():
        raise AssertionError('尚未实现独立后台窗口通路')
    return importlib.import_module('gameqakit.background')


class BackgroundPolicyTests(unittest.TestCase):
    def test_invalid_coordinates_timing_and_unknown_operations_rejected(self):
        b = module()
        for values in ({'kind': 'click', 'cx': -1, 'cy': 1},
                       {'kind': 'click', 'cx': 1280, 'cy': 1},
                       {'kind': 'click', 'cx': True, 'cy': 1},
                       {'kind': 'click', 'cx': 1, 'cy': 1, 'interval_ms': 599},
                       {'kind': 'click', 'cx': 1, 'cy': 1, 'hold_ms': 599},
                       {'kind': 'click', 'cx': 1, 'cy': 1, 'hold_ms': 20000},
                       {'kind': 'drag', 'cx': 1, 'cy': 1, 'end_cx': 5, 'end_cy': -1},
                       {'kind': 'wheel', 'cx': 1, 'cy': 1, 'delta': 0},
                       {'kind': 'shell', 'command': 'ignored'},
                       {'kind': 'click', 'cx': 1, 'cy': 1, 'hwnd': 999}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                b.validate_action(values, 1280, 720)

    def test_click_releases_even_if_intermediate_operation_fails(self):
        b = module()
        calls = []
        def post(message, wparam, x, y):
            calls.append((message, wparam, x, y))
            if len(calls) == 3:
                raise OSError('模拟拖动中的投递失败')
        with self.assertRaises(OSError):
            b.perform_action({'kind': 'drag', 'cx': 3, 'cy': 4, 'end_cx': 40,
                              'end_cy': 50, 'steps': 3}, 100, 100, post, sleep=lambda _: None)
        self.assertEqual(calls[-1][0], 0x202)
        self.assertEqual(calls[-1][1], 0)

    def test_drag_sends_segments_without_global_input_and_wheel_keeps_client_point(self):
        b = module()
        calls, waits = [], []
        result = b.perform_action({'kind': 'drag', 'cx': 1, 'cy': 2, 'end_cx': 31,
                                  'end_cy': 32, 'steps': 3}, 100, 100,
                                  lambda *row: calls.append(row), sleep=waits.append)
        self.assertTrue(result['messages_posted'])
        self.assertIsNone(result['game_responded'])
        self.assertEqual([row[0] for row in calls], [0x200, 0x201, 0x200, 0x200, 0x200, 0x202])
        self.assertEqual(calls[-1][2:], (31, 32))
        self.assertTrue(all(wait >= .6 for wait in waits))

    def test_mcp_background_tools_do_not_require_profile_and_gate_side_effects(self):
        from gameqakit import mcp_server as m
        required = {'background_start', 'background_status', 'background_capture', 'background_input', 'background_stop'}
        self.assertTrue(required.issubset(m.TOOLS), 'MCP 尚未暴露后台窗口工具')
        with mock.patch.object(m, 'P', side_effect=AssertionError('不得加载 profile')):
            with mock.patch.object(m._BACKGROUND, 'start', return_value={'ok': True}) as start:
                m.handle('tools/call', {'name': 'background_start', 'arguments': {'hwnd': 123, 'pid': 456, 'confirm': True}})
                start.assert_called_once()
        for name in ('background_start', 'background_input'):
            with self.assertRaisesRegex(ValueError, 'confirm'):
                m.handle('tools/call', {'name': name, 'arguments': {}})


@unittest.skipUnless(os.name == 'nt', '需要 Windows 进程句柄')
class BackgroundLifecycleTests(unittest.TestCase):
    def test_stop_reaps_child_when_exit_code_precedes_poll_completion(self):
        import subprocess
        import sys
        from gameqakit import background_agent as native
        manager = module().Manager()
        child = subprocess.Popen([sys.executable, '-B', '-c', 'pass'])
        self.addCleanup(child.wait, timeout=5)
        manager.child = child
        manager.handle = native.process(child.pid)
        self.addCleanup(manager.close)
        deadline = time.monotonic() + 5
        while native.alive(manager.handle):
            self.assertLess(time.monotonic(), deadline, '自写子进程未退出')
            time.sleep(.01)
        # 固定退出码已经可读、零超时 poll 尚未观察到退出的竞争窗口。
        with mock.patch.object(child, 'poll', return_value=None):
            result = manager.stop()
        self.assertFalse(result['running'])
        self.assertEqual(child.returncode, 0)
        self.assertIsNone(manager.child)
        self.assertIsNone(manager.handle)

    def test_stop_retains_unreaped_child_and_reports_wait_timeout(self):
        import subprocess
        import sys
        manager = module().Manager()
        child = subprocess.Popen([sys.executable, '-B', '-c', 'import time; time.sleep(30)'])
        self.addCleanup(child.wait, timeout=5)
        self.addCleanup(child.kill)
        manager.child = child
        with self.assertRaises(subprocess.TimeoutExpired):
            manager.stop()
        self.assertIsNone(child.poll())
        self.assertIs(manager.child, child)


@unittest.skipUnless(os.name == 'nt', '需要 Windows 窗口消息')
class BackgroundIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.b = module()
        from gameqakit.desktop import DesktopProcess
        import sys
        self.temp = tempfile.TemporaryDirectory(prefix='gameqa-background-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fixture = DesktopProcess([sys.executable, '-B', str(BASE / 'tests/background_fixture.py'),
                                       str(self.root / 'target.json')], cwd=BASE, isolated_desktop=False)
        self.fixture.__enter__()
        self.addCleanup(self.fixture.close)
        self.fixture.run()
        self.until(lambda: (self.root / 'target.json').exists())
        import json
        self.target = json.loads((self.root / 'target.json').read_text(encoding='utf-8'))
        self.manager = self.b.Manager()
        self.addCleanup(self.manager.close)
        self.manager.start(self.target['hwnd'], self.target['pid'], elevate=False)
        self.until(lambda: self.manager.status().get('ready'))

    def until(self, predicate, timeout=10):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if predicate():
                return
            time.sleep(.03)
        self.fail('后台通路等待超时')

    def test_capture_preserves_full_dpi_aware_client_pixels(self):
        result = self.manager.capture(self.root / 'dpi.png')
        self.assertEqual(result['size'], self.target['size'])
        self.assertEqual(self.manager.status()['size'], self.target['size'])

    def test_capture_click_and_drag_without_foreground_or_system_cursor(self):
        import ctypes
        from ctypes import wintypes as W
        from PIL import Image, ImageChops
        from gameqakit import win32
        u = win32.user32
        point = W.POINT()
        u.GetCursorPos(ctypes.byref(point))
        before = (point.x, point.y, int(u.GetForegroundWindow() or 0))
        first = self.manager.capture(self.root / 'before.png')
        result = self.manager.input({'kind': 'click', 'cx': 50, 'cy': 60, 'command_id': 'one'})
        duplicate = self.manager.input({'kind': 'click', 'cx': 50, 'cy': 60, 'command_id': 'one'})
        self.assertEqual(result, duplicate)
        time.sleep(.1)
        second = self.manager.capture(self.root / 'after.png')
        with Image.open(first['out']) as left, Image.open(second['out']) as right:
            self.assertIsNotNone(ImageChops.difference(left, right).getbbox())
        drag = self.manager.input({'kind': 'drag', 'cx': 10, 'cy': 20, 'end_cx': 90,
                                   'end_cy': 100, 'steps': 2, 'command_id': 'two'})
        self.assertTrue(drag['messages_posted'])
        u.GetCursorPos(ctypes.byref(point))
        self.assertEqual((point.x, point.y, int(u.GetForegroundWindow() or 0)), before)
        self.assertTrue(self.manager.status()['ready'])
        self.manager.stop()
        self.assertFalse(self.manager.status()['running'])

    def test_long_press_wheel_drag_and_dedup_are_consumed_at_requested_points(self):
        import json
        self.manager.input({'kind': 'click', 'cx': 80, 'cy': 90, 'hold_ms': 800, 'command_id': 'hold'})
        self.manager.input({'kind': 'wheel', 'cx': 120, 'cy': 130, 'delta': -120, 'command_id': 'wheel'})
        self.manager.input({'kind': 'drag', 'cx': 10, 'cy': 20, 'end_cx': 90, 'end_cy': 100,
                            'steps': 2, 'command_id': 'drag'})
        self.manager.input({'kind': 'click', 'cx': 80, 'cy': 90, 'hold_ms': 800, 'command_id': 'hold'})
        with self.assertRaisesRegex(RuntimeError, 'command_id'):
            self.manager.input({'kind': 'click', 'cx': 81, 'cy': 90, 'hold_ms': 800, 'command_id': 'hold'})
        path = self.root / 'target.jsonl'
        self.until(lambda: len(path.read_text(encoding='utf-8').splitlines()) >= 10)
        events = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
        presses = [e for e in events if e['message'] == 0x201]
        releases = [e for e in events if e['message'] == 0x202]
        self.assertEqual(len(presses), 2)
        self.assertEqual(len(releases), 2)
        self.assertGreaterEqual(releases[0]['time'] - presses[0]['time'], .75)
        self.assertEqual(releases[-1]['point'], [90, 100])
        wheel = [e for e in events if e['message'] == 0x20A]
        self.assertEqual(wheel[0]['point'], [120, 130])
        self.assertEqual((wheel[0]['wparam'] >> 16) & 0xffff, 65416)
        drag_moves = [e for e in events if e['message'] == 0x200 and e['wparam'] == 1]
        self.assertEqual([e['point'] for e in drag_moves], [[50, 60], [90, 100]])
        self.assertGreaterEqual(releases[-1]['time'] - drag_moves[-1]['time'], .55)

    def test_minimized_target_is_rejected_without_restore(self):
        from gameqakit import win32
        win32.user32.ShowWindow(self.target['hwnd'], 7)
        self.until(lambda: bool(win32.user32.IsIconic(self.target['hwnd'])))
        with self.assertRaisesRegex(RuntimeError, '最小化'):
            self.manager.capture(self.root / 'minimized.png')
        self.assertFalse(self.manager.status()['ready'])
        self.assertTrue(win32.user32.IsIconic(self.target['hwnd']))

    def test_parent_exit_reaps_agent_without_terminating_target(self):
        import subprocess
        import sys
        import json
        from gameqakit import background_agent as native
        code = ('import sys,json,time;from gameqakit.background import Manager;'
                'from pathlib import Path;m=Manager();m.start(int(sys.argv[1]),int(sys.argv[2]),elevate=False);'
                'time.sleep(.3);Path(sys.argv[3]).write_text(json.dumps(m.status()));')
        info = self.root / 'child-status.json'
        child = subprocess.run([sys.executable, '-B', '-c', code, str(self.target['hwnd']),
                                str(self.target['pid']), str(info)], cwd=BASE, capture_output=True, timeout=8)
        self.assertEqual(child.returncode, 0, child.stderr)
        status = json.loads(info.read_text())
        handle = native.process(status['agent_pid'])
        try:
            self.until(lambda: not native.alive(handle))
            self.assertTrue(self.fixture.alive())
        finally:
            native.k.CloseHandle(handle)

    def test_rejects_other_process_using_same_pipe_and_survives(self):
        import subprocess
        import sys
        code = ('from gameqakit.background_agent import request; '
                'import sys; '
                'request(sys.argv[1],int(sys.argv[2]),{"op":"status"})')
        child = subprocess.run([sys.executable, '-B', '-c', code, self.manager.pipe,
                                str(self.manager.agent_pid)], cwd=BASE, capture_output=True, timeout=8)
        self.assertNotEqual(child.returncode, 0)
        self.assertTrue(self.manager.status()['ready'])

    def test_target_exit_fails_closed_and_stops_agent(self):
        self.fixture.close()
        self.until(lambda: not self.manager.status().get('running'))
        with self.assertRaises(RuntimeError):
            self.manager.input({'kind': 'click', 'cx': 1, 'cy': 1, 'command_id': 'dead'})
