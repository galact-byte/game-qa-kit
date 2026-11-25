"""Wolf 启动出图的真实窗口、共享内存及自有 Job 回收回归。"""
import ctypes
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == 'nt', '需要 Windows/MSVC x86')
class WolfStartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        parent = Path('E:/DevData/gameqa/gameqa-wolf/tmp')
        parent.mkdir(parents=True, exist_ok=True)
        cls.tmp = tempfile.TemporaryDirectory(dir=parent)
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.root = Path(cls.tmp.name)
        source = (ROOT / 'plugins/wolf/Bridge.cpp').read_text(encoding='utf-8')
        start = source.index('            std::string capture_id;')
        end = source.index('            Sleep(10);', start)
        (cls.root / 'BridgeCycle.inc').write_text(source[start:end], encoding='utf-8')
        cls.binary = cls.root / 'StartupFixture.exe'
        args = ['cl', '/nologo', '/EHsc', '/W4', '/WX', '/std:c++17', '/utf-8',
                '/DWIN32_LEAN_AND_MEAN', '/DNOMINMAX', '/I' + str(ROOT / 'plugins/wolf'),
                '/I' + str(cls.root), str(ROOT / 'tests/wolf/StartupFixture.cpp'),
                '/Fe:' + str(cls.binary), '/link', 'user32.lib', 'gdi32.lib', 'gdiplus.lib',
                'bcrypt.lib', 'advapi32.lib']
        script = cls.root / 'compile.cmd'
        script.write_text('@echo off\ncall "E:/vsCode/VSBuildTools/VC/Auxiliary/Build/vcvars32.bat" >nul\n'
                          'if errorlevel 1 exit /b 1\n' + subprocess.list2cmdline(args) + '\n', encoding='utf-8')
        subprocess.run(['cmd', '/d', '/c', str(script)], cwd=cls.root, check=True, timeout=45)

    def exercise(self, mode='normal', enabled=True):
        from gameqakit.control import BridgeClient, write_connection
        from gameqakit.desktop import DesktopProcess, k, W
        from plugins.wolf.startup import initialize
        bridge = self.root / uuid.uuid4().hex
        bridge.mkdir()
        session = uuid.uuid4().hex
        with DesktopProcess([str(self.binary), str(bridge), session, mode,
                             'enabled' if enabled else 'disabled'], cwd=self.root) as process:
            write_connection(bridge, {'protocol': 1, 'session_id': session, 'pid': process.pid,
                             'exe_path': str(self.binary), 'command_format': 'ini',
                             'control_transport': 'wolf-shm-v1'})
            process.run()
            client = BridgeClient(bridge, pid=process.pid, exe_path=str(self.binary))
            until = time.monotonic() + 5
            while time.monotonic() < until:
                state = client.status()
                if state.get('connected') and state.get('at_frame_boundary'):
                    break
                time.sleep(.02)
            self.assertTrue(state.get('connected'), state)
            self.assertEqual(state['frame'], 0)
            if not enabled:
                time.sleep(.1)
                self.assertEqual(client.status()['frame'], 0)
                self.assertFalse(list((bridge / 'screenshots').glob('*.png')))
                return
            started = time.monotonic()
            if mode == 'normal':
                result = initialize(process, client)
                self.assertTrue(result['ok'])
                self.assertEqual(result['frame'], 2)
                self.assertTrue(Path(result['screenshot']).is_file())
                observed = client.status()
                observe_until = time.monotonic() + 1
                while not observed.get('observation_seq') and time.monotonic() < observe_until:
                    time.sleep(.01)
                    observed = client.status()
                self.assertGreater(observed.get('observation_seq', 0), 0)
                self.assertEqual(observed.get('observation_screenshot'), 'screenshots/observation.png')
                self.assertEqual((bridge / 'screenshots/observation.png').read_bytes(),
                                 Path(result['screenshot']).read_bytes())
                time.sleep(.1)
                state = client.status()
                self.assertEqual(state['frame'], 2)
                self.assertTrue(state['paused'])
                self.assertEqual(state['held'], [])
                self.assertEqual(state['held_polls'], 0)
            else:
                exits = []
                def observe_exit():
                    code = k.WaitForSingleObject(process.process, 4000)
                    exits.append((code, time.monotonic() - started))
                observer = threading.Thread(target=observe_exit, daemon=True)
                observer.start()
                try:
                    with self.assertRaises((RuntimeError, TimeoutError)):
                        initialize(process, client)
                finally:
                    observer.join(4)
                # The deadline bounds target lifetime; a failed IPC pause may finish later.
                self.assertEqual(len(exits), 1)
                self.assertEqual(exits[0][0], 0)
                self.assertLess(exits[0][1], 4.0)
                self.assertEqual(k.WaitForSingleObject(process.process, 1000), 0)
                self.assertFalse(process.alive())
                code = W.DWORD()
                self.assertTrue(k.GetExitCodeProcess(process.process, ctypes.byref(code)))
                self.assertNotEqual(code.value, 259)

    def test_legacy_entry_remains_paused_at_frame_zero(self):
        self.exercise(enabled=False)

    def test_first_valid_image_stops_at_complete_boundary_without_keys(self):
        self.exercise()

    def test_blank_budget_reaps_owned_process(self):
        self.exercise('blank')

    def test_blocked_capture_is_reaped_by_host_deadline(self):
        self.exercise('blocked-capture')

    def test_blocked_game_loop_stops_without_extra_frames(self):
        self.exercise('blocked-loop')

    def test_guard_rejection_fails_and_reaps_owned_process(self):
        self.exercise('guard')

    def test_game_exit_fails_without_retry(self):
        self.exercise('exit')


@unittest.skipUnless(os.name == 'nt', '需要 Windows')
class WolfSmokeWaitTests(unittest.TestCase):
    def setUp(self):
        import importlib.util
        from unittest.mock import Mock
        spec = importlib.util.spec_from_file_location('wolf_smoke_wait_test', ROOT / 'tests/wolf_smoke.py')
        self.smoke = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.smoke)
        self.process = Mock()
        self.process.alive.return_value = True

    def test_extension_policy_is_rejected_without_explicit_initialization(self):
        result = subprocess.run([
            sys.executable, '-B', '-X', 'utf8', str(ROOT / 'tests/wolf_smoke.py'),
            '--source', 'missing-source', '--workspace', 'missing-workspace',
            '--dll', 'missing.dll', '--injector', 'missing.exe', '--disable-extension-points',
        ], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 2)
        self.assertIn('--disable-extension-points 仅能与 --initialize-only 一起使用', result.stderr)

    def test_missing_stage_still_expires(self):
        def missing():
            raise FileNotFoundError('not published')
        started = time.monotonic()
        with self.assertRaisesRegex(TimeoutError, 'condition_not_reached'):
            self.smoke.wait(missing, .05, self.process)
        self.assertLess(time.monotonic() - started, .5)

    def test_permission_failure_is_not_hidden(self):
        def denied():
            raise PermissionError('denied')
        with self.assertRaises(PermissionError):
            self.smoke.wait(denied, 1, self.process)

    def test_stage_published_after_transient_read_timeout(self):
        parent = Path('E:/DevData/gameqa/gameqa-wolf/tmp')
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as temporary:
            stage = Path(temporary) / 'plugin-stage.txt'
            def publish():
                time.sleep(.4)
                stage.write_text('ready', encoding='utf-8')
            publisher = threading.Thread(target=publish)
            publisher.start()
            try:
                self.assertEqual(self.smoke.wait(lambda: self.smoke.read_text(stage), 2, self.process), 'ready')
            finally:
                publisher.join()


if __name__ == '__main__':
    unittest.main()
