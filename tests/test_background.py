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
class BackgroundStartupDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.b = module()
        from gameqakit import background_agent
        self.native = background_agent
        self.manager = self.b.Manager()
        self.addCleanup(self.manager.close)

    def failed_start(self):
        with mock.patch.object(self.native, 'check_window', return_value=(1, 1)):
            self.manager.start(1, 4294967295, elevate=False)
        end = time.monotonic() + 10
        while time.monotonic() < end:
            state = self.manager.status()
            if not state['running']:
                return state
            time.sleep(.03)
        self.fail('初始化失败的代理未退出')

    def test_failed_initialization_retains_error_after_stop(self):
        state = self.failed_start()
        self.assertFalse(state['ready'])
        self.assertNotEqual(state.get('exit_code', 0), 0)
        self.assertIn('OSError', state.get('error', ''))
        self.assertEqual(state.get('pid'), 4294967295)
        self.assertEqual(state, self.manager.status())
        self.manager.stop()
        self.assertEqual(state, self.manager.status())

    def test_status_reports_exit_during_request(self):
        state = self.failed_start()
        self.manager.last_exit = None
        with mock.patch.object(self.native, 'alive', side_effect=[True, False]), \
                mock.patch.object(self.manager, '_request', side_effect=TimeoutError('pipe closed')):
            raced = self.manager.status()
        self.assertEqual(raced, state)

    def test_exit_code_read_failure_is_retained(self):
        self.failed_start()
        self.manager.last_exit = None
        with mock.patch.object(self.native, 'exit_code', side_effect=OSError('exit-code-unavailable'), create=True):
            state = self.manager.status()
        self.assertFalse(state['running'])
        self.assertIn('exit-code-unavailable', state.get('error', ''))
        self.assertNotIn('exit_code', state)
        self.manager.stop()
        self.assertEqual(state, self.manager.status())

    def test_exit_without_stderr_reports_reason(self):
        import subprocess
        import sys
        child = subprocess.Popen([sys.executable, '-B', '-c', 'raise SystemExit(7)'])
        self.addCleanup(child.wait)
        handle = self.native.process(child.pid)
        child.wait(timeout=5)
        self.manager.handle = handle
        self.manager.agent_pid = child.pid
        self.manager.target = (1, 123)
        state = self.manager.status()
        self.assertEqual(state.get('exit_code'), 7)
        self.assertTrue(state.get('error'))
        self.assertEqual(state.get('agent_pid'), child.pid)

    def test_sid_rejection_survives_real_agent_exit(self):
        import subprocess
        import sys
        popen = subprocess.Popen
        script = ('import sys;from unittest import mock;'
                  'from gameqakit import background_agent as n;'
                  'sys.argv=["background_agent",*sys.argv[1:]];'
                  'p=mock.patch.object(n,"user_sid",side_effect=["S-1-5-21-1","S-1-5-21-2"]);'
                  'p.start();n.main()')
        with mock.patch.object(self.native, 'check_window', return_value=(1, 1)), \
                mock.patch.object(self.native.subprocess, 'Popen', side_effect=lambda argv, **kw:
                                  popen([sys.executable, '-B', '-X', 'utf8', '-c', script,
                                         *argv[argv.index('--pipe'):]], **kw)):
            self.manager.start(1, os.getpid(), elevate=False)
        end = time.monotonic() + 10
        while self.native.alive(self.manager.handle) and time.monotonic() < end:
            time.sleep(.03)
        state = self.manager.status()
        self.assertFalse(state['running'])
        self.assertEqual(state.get('exit_code'), 1)
        self.assertIn('只允许同用户的启动进程和目标游戏', state.get('error', ''))

    def test_agent_errors_use_utf8_even_with_legacy_stderr(self):
        import subprocess
        import sys
        popen = subprocess.Popen
        script = ('import sys;from unittest import mock;'
                  'from gameqakit import background_agent as n;'
                  'sys.stderr.reconfigure(encoding="cp936");'
                  'sys.argv=["background_agent",*sys.argv[1:]];'
                  'p=mock.patch.object(n,"user_sid",side_effect=["S-1-5-21-1","S-1-5-21-2"]);'
                  'p.start();n.main()')
        with mock.patch.object(self.native, 'check_window', return_value=(1, 1)), \
                mock.patch.object(self.native.subprocess, 'Popen', side_effect=lambda argv, **kw:
                                  popen([sys.executable, '-B', '-c', script,
                                         *argv[argv.index('--pipe'):]], **kw)):
            self.manager.start(1, os.getpid(), elevate=False)
        end = time.monotonic() + 10
        while self.native.alive(self.manager.handle) and time.monotonic() < end:
            time.sleep(.03)
        state = self.manager.status()
        self.assertFalse(state['running'])
        self.assertEqual(state.get('exit_code'), 1)
        self.assertIn('只允许同用户的启动进程和目标游戏', state.get('error', ''))
        self.assertNotIn('\ufffd', state['error'])

    def test_large_stderr_is_drained_and_tail_bounded(self):
        import subprocess
        import sys
        popen = subprocess.Popen
        script = 'import sys;sys.stderr.write("x" * 131072 + "\\nRuntimeError: tail-marker\\n");sys.exit(9)'
        with mock.patch.object(self.native.subprocess, 'Popen',
                               side_effect=lambda argv, **kw: popen([sys.executable, '-B', '-c', script], **kw)):
            state = self.failed_start()
        self.assertEqual(state.get('exit_code'), 9)
        self.assertIn('RuntimeError: tail-marker', state.get('error', ''))
        self.assertLessEqual(len(state['error'].encode('utf-8')), 17000)

    def test_fresh_manager_is_idle(self):
        self.assertEqual(self.manager.status(), {'running': False, 'ready': False})

    def test_sid_mismatch_rejects_before_window_access(self):
        with mock.patch.object(self.native, 'user_sid', side_effect=['S-1-5-21-1', 'S-1-5-21-2']), \
                mock.patch.object(self.native, 'check_window', side_effect=AssertionError('不应访问窗口')):
            with self.assertRaisesRegex(RuntimeError, '同用户'):
                self.native.Agent(1, os.getpid(), os.getpid())


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

    def test_restart_clears_previous_startup_failure(self):
        from gameqakit import background_agent as native
        self.manager.stop()
        with mock.patch.object(native, 'check_window', return_value=(1, 1)):
            self.manager.start(1, 4294967295, elevate=False)
        self.until(lambda: not self.manager.status()['running'])
        self.assertTrue(self.manager.status().get('error'))
        self.manager.start(self.target['hwnd'], self.target['pid'], elevate=False)
        self.until(lambda: self.manager.status().get('ready'))
        self.assertNotIn('error', self.manager.status())
        self.manager.stop()
        state = self.manager.status()
        self.assertEqual(state.get('exit_code'), 0)
        self.assertNotIn('error', state)

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
        def cursor_position():
            point = W.POINT()
            if u.GetCursorPos(ctypes.byref(point)):
                return point.x, point.y
            error = ctypes.get_last_error()
            if error == 5:  # 独立诊断桌面可能无权读取输入桌面的光标。
                return None
            raise ctypes.WinError(error)

        before_cursor = cursor_position()
        before_window = u.GetForegroundWindow()
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
        after_cursor = cursor_position()
        self.assertEqual(u.GetForegroundWindow(), before_window)
        with self.subTest(check='system_cursor'):
            if before_cursor is None and after_cursor is None:
                self.skipTest('GetCursorPos 两次均拒绝访问，系统光标位置未验证')
            self.assertEqual(after_cursor, before_cursor)
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
        code = ('import sys,json,time\nfrom gameqakit.background import Manager\n'
                'from pathlib import Path\nm=Manager()\n'
                'started=m.start(int(sys.argv[1]),int(sys.argv[2]),elevate=False)\n'
                'deadline=time.monotonic()+5\n'
                'while not (state:=m.status()).get("ready"):\n'
                ' assert state.get("running") and time.monotonic()<deadline,state\n'
                ' time.sleep(.02)\n'
                'Path(sys.argv[3]).write_text(json.dumps(started))\n')
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
