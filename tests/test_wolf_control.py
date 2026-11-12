"""Wolf 原生桥的版本／副本边界与真实 C++ 安全核心回归。"""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def prepare_module():
    path = ROOT / 'plugins/wolf/prepare.py'
    if not path.is_file():
        raise AssertionError('Wolf 正式副本准备入口尚未实现')
    spec = importlib.util.spec_from_file_location('wolf_prepare_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WolfControlTests(unittest.TestCase):
    def setUp(self):
        parent = Path('E:/DevData/gameqa-wolf/tmp') if os.name == 'nt' else None
        if parent:
            parent.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'sample'
        self.source.mkdir()
        (self.source / 'Game.exe').write_bytes(b'MZ-not-supported')

    def test_unknown_binary_is_rejected_without_creating_workspace(self):
        module = prepare_module()
        workspace = self.root / 'workspace'
        with self.assertRaises(ValueError):
            module.prepare(self.source, workspace)
        self.assertFalse(workspace.exists())
        self.assertEqual((self.source / 'Game.exe').read_bytes(), b'MZ-not-supported')

    def test_overlap_and_existing_workspace_cannot_overwrite_data(self):
        module = prepare_module()
        existing = self.root / 'existing'
        existing.mkdir()
        (existing / 'progress').write_bytes(b'preserve')
        with patch.object(module, 'validate_image', return_value='test-image'):
            for workspace in (self.source / 'child', self.root):
                with self.subTest(workspace=workspace), self.assertRaises(ValueError):
                    module.prepare(self.source, workspace)
            with self.assertRaises(FileExistsError):
                module.prepare(self.source, existing)
        self.assertEqual((existing / 'progress').read_bytes(), b'preserve')

    def test_preparation_preserves_assets_but_excludes_original_saves(self):
        module = prepare_module()
        (self.source / 'Data').mkdir()
        (self.source / 'Data/asset').write_bytes(b'game-assets')
        for name in ('Save', 'SaveData'):
            (self.source / name).mkdir()
            (self.source / name / 'Save9.dat').write_bytes(b'original-progress')
        workspace = self.root / 'fresh'
        with patch.object(module, 'validate_image', return_value='test-image'):
            record = module.prepare(self.source, workspace)
        runtime = Path(record['runtime'])
        self.assertEqual((runtime / 'Data/asset').read_bytes(), b'game-assets')
        for name in ('Save', 'SaveData'):
            self.assertFalse((runtime / name / 'Save9.dat').exists())
            self.assertEqual((self.source / name / 'Save9.dat').read_bytes(), b'original-progress')
        self.assertTrue((workspace / 'preparation.json').is_file())
        self.assertTrue((workspace / '测试数据记录.md').is_file())
        self.assertFalse(record['copied_user_saves'])

    def test_preparation_excludes_tool_runtime_and_tool_saves(self):
        module = prepare_module()
        for name in ('Save_MTool', 'Tool'):
            (self.source / name).mkdir()
            (self.source / name / 'private.dat').write_bytes(b'original-user-data')
        (self.source / 'MTool_Game.exe').write_bytes(b'tool-modified-runtime')
        (self.source / 'Data.wolf').write_bytes(b'original-resources')
        with patch.object(module, 'validate_image', return_value='test-image'):
            record = module.prepare(self.source, self.root / 'original-only')
        runtime = Path(record['runtime'])
        for name in ('Save_MTool', 'Tool', 'MTool_Game.exe'):
            self.assertFalse((runtime / name).exists(), name)
            self.assertTrue((self.source / name).exists(), name)
        self.assertEqual((runtime / 'Data.wolf').read_bytes(), b'original-resources')

    def test_known_original_image_keeps_its_identity(self):
        fixture = os.environ.get('WOLF_TEST_ORIGINAL_EXE')
        if not fixture:
            self.skipTest('可用 WOLF_TEST_ORIGINAL_EXE 指定本地只读原版 EXE')
        module = prepare_module()
        try:
            actual = module.validate_image(Path(fixture))
        except ValueError as error:
            self.fail('已静态核查原版构建被拒绝：' + str(error))
        self.assertEqual(actual, '2b811e3db991f45113087f979e3f375da7e46769a01f6e554584ed7c100b2f27')

    def test_locale_launch_rejects_unprepared_runtime_before_staging(self):
        module = self.locale_module()
        workspace = self.root / 'unprepared'
        runtime = workspace / 'runtime'
        runtime.mkdir(parents=True)
        (runtime / 'Game.exe').write_bytes(b'unknown')
        bridge = workspace / 'bridge'
        bridge.mkdir()
        with self.assertRaisesRegex(ValueError, 'prepared_runtime_required'):
            module.LocaleProcess(runtime / 'Game.exe', bridge=bridge, session='test',
                                 dll=self.source / 'missing.dll', injector=self.source / 'missing.exe',
                                 locale_dir=self.source)
        self.assertFalse((workspace / 'launcher').exists())

    def test_locale_launch_rejects_unverified_emulator_before_staging(self):
        module = self.locale_module()
        workspace = self.root / 'prepared'
        runtime = workspace / 'runtime'
        runtime.mkdir(parents=True)
        (runtime / 'Game.exe').write_bytes(b'unknown')
        (workspace / 'preparation.json').write_text('{}', encoding='utf-8')
        bridge = workspace / 'bridge'
        bridge.mkdir()
        for name in ('LoaderDll.dll', 'LocaleEmulator.dll'):
            (self.source / name).write_bytes(b'unverified-library')
        with self.assertRaisesRegex(ValueError, 'unsupported_locale_binary'):
            module.LocaleProcess(runtime / 'Game.exe', bridge=bridge, session='test',
                                 dll=self.source / 'missing.dll', injector=self.source / 'missing.exe',
                                 locale_dir=self.source)
        self.assertFalse((workspace / 'launcher').exists())

    @unittest.skipUnless(os.name == 'nt', '需要 Windows 独立桌面')
    def test_locale_launcher_failure_reaps_owned_job(self):
        module = self.locale_module()
        from gameqakit.desktop import DesktopProcess
        process = DesktopProcess([sys.executable, '-c', 'pass'], cwd=self.root,
                                 env={'PYTHONDONTWRITEBYTECODE': '1'})
        instance = module.LocaleProcess.__new__(module.LocaleProcess)
        instance.host = process
        instance.process = None
        instance.pid = None
        instance.bridge = self.root / 'missing-bridge'
        instance.session = 'test'
        with self.assertRaisesRegex(RuntimeError, 'locale_host_exited'):
            instance.__enter__()
        self.assertIsNone(process.job)
        self.assertIsNone(process.desktop)
        self.assertIsNone(process.process)

    def test_locale_host_rejects_wrong_session_and_desktop_before_creating_game(self):
        fixture = os.environ.get('WOLF_TEST_ORIGINAL_EXE')
        host = os.environ.get('WOLF_TEST_LOCALE_HOST')
        if os.name != 'nt' or not fixture or not host:
            self.skipTest('需要本地只读商业 EXE 与已构建的日文启动宿主')
        import shutil
        workspace = self.root / 'host-rejections'
        runtime = workspace / 'runtime'
        runtime.mkdir(parents=True)
        shutil.copy2(fixture, runtime / 'Game.exe')
        (workspace / 'preparation.json').write_text('{}', encoding='utf-8')
        bridge = workspace / 'bridge'
        bridge.mkdir()
        args = [host, str(bridge), str(runtime / 'Game.exe'), str(runtime / 'Game.exe')]
        for overrides, expected in [
                ({'GAMEQAKIT_SESSION_ID': 'invalid-session'}, 'locale_environment_mismatch'),
                ({'GAMEQAKIT_SESSION_ID': 'a' * 32,
                  'GAMEQAKIT_DESKTOP_NAME': 'gameqakit-wrong'}, 'owned_isolated_desktop_required')]:
            with self.subTest(expected=expected):
                result = subprocess.run(args, capture_output=True, encoding='utf-8', timeout=10,
                                        env={**os.environ, 'GAMEQAKIT_BRIDGE_DIR': str(bridge), **overrides})
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual((bridge / 'launch-error.txt').read_text(encoding='utf-8'), expected)
                self.assertFalse((bridge / 'launch.ini').exists())
                self.assertFalse((bridge / 'startup-guard.json').exists())

    @unittest.skipUnless(os.name == 'nt', '需要 Windows 文件共享锁')
    def test_locale_handshake_survives_transient_status_file_lock(self):
        import ctypes
        from ctypes import wintypes as W
        import threading
        import time
        from types import SimpleNamespace
        module = self.locale_module()
        instance = module.LocaleProcess.__new__(module.LocaleProcess)
        instance.bridge = self.root
        instance.session = 'a' * 32
        instance.host = SimpleNamespace(pid=123, alive=lambda: True)
        status = self.root / 'launch.ini'
        status.write_text('[bridge]\nprotocol=1\nsession_id=' + instance.session +
                          '\nowner_pid=123\npid=456\nstage=ready\n', encoding='utf-8')
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, W.LPVOID, W.DWORD, W.DWORD, W.HANDLE]
        kernel.CreateFileW.restype = W.HANDLE
        kernel.CloseHandle.argtypes = [W.HANDLE]
        handle = kernel.CreateFileW(str(status), 0x80000000, 0, None, 3, 0, None)
        self.assertNotEqual(handle, W.HANDLE(-1).value)
        def release():
            time.sleep(.6)
            kernel.CloseHandle(handle)
        thread = threading.Thread(target=release)
        thread.start()
        try:
            self.assertEqual(instance._await_stage('ready'), 456)
        finally:
            thread.join()

    def test_locale_handshake_permanent_lock_preserves_launch_deadline(self):
        from types import SimpleNamespace
        module = self.locale_module()
        instance = module.LocaleProcess.__new__(module.LocaleProcess)
        instance.bridge = self.root
        instance.session = 'a' * 32
        instance.host = SimpleNamespace(pid=123, alive=lambda: True)
        (self.root / 'launch.ini').write_text('locked', encoding='utf-8')
        with patch.object(module, 'read_text', side_effect=PermissionError('locked')), \
                patch.object(module.time, 'monotonic', side_effect=[0, 0, 16]), \
                patch.object(module.time, 'sleep'):
            with self.assertRaisesRegex(TimeoutError, 'locale_launch_timeout:ready'):
                instance._await_stage('ready')

    def test_locale_handshake_rejects_foreign_owner_or_session(self):
        from types import SimpleNamespace
        module = self.locale_module()
        instance = module.LocaleProcess.__new__(module.LocaleProcess)
        instance.bridge = self.root
        instance.session = 'a' * 32
        instance.host = SimpleNamespace(pid=123, alive=lambda: True)
        for session, owner in [('b' * 32, 123), ('a' * 32, 999)]:
            with self.subTest(session=session, owner=owner):
                (self.root / 'launch.ini').write_text(
                    f'[bridge]\nprotocol=1\nsession_id={session}\nowner_pid={owner}\npid=456\nstage=ready\n',
                    encoding='utf-8')
                with self.assertRaisesRegex(RuntimeError, 'locale_launch_binding_mismatch'):
                    instance._await_stage('ready')

    def locale_module(self):
        path = ROOT / 'plugins/wolf/launch.py'
        self.assertTrue(path.is_file(), '日文区域启动入口尚未实现')
        spec = importlib.util.spec_from_file_location('wolf_locale_test', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    @unittest.skipUnless(os.name == 'nt', '需要 Windows 与 MSVC x86')
    def test_status_publication_does_not_block_input_pause_or_lease_checks(self):
        # Compile the actual run-loop body, as well as the real input/guard functions.
        source = (ROOT / 'plugins/wolf/Bridge.cpp').read_text(encoding='utf-8')
        begin = source.index('            std::string capture_id;')
        end = source.index('            Sleep(10);', begin)
        (self.root / 'BridgeCycle.inc').write_text(source[begin:end], encoding='utf-8')
        output = self.root / 'BridgeThreadTests.exe'
        args = ['cl', '/nologo', '/EHsc', '/W4', '/WX', '/std:c++17', '/utf-8',
                '/DWIN32_LEAN_AND_MEAN', '/DNOMINMAX',
                '/I' + str(ROOT / 'plugins/wolf'), '/I' + str(self.root),
                str(ROOT / 'tests/wolf/BridgeThreadTests.cpp'), '/Fe:' + str(output),
                '/link', 'user32.lib', 'gdi32.lib', 'gdiplus.lib', 'bcrypt.lib', 'advapi32.lib']
        script = self.root / 'compile.cmd'
        script.write_text('@echo off\ncall "E:/vsCode/VSBuildTools/VC/Auxiliary/Build/vcvars32.bat" >nul\n'
                          'if errorlevel 1 exit /b 1\n' + subprocess.list2cmdline(args) + '\n', encoding='utf-8')
        subprocess.run(['cmd', '/d', '/c', str(script)], cwd=self.root, check=True, timeout=45)
        subprocess.run([str(output), str(self.root / 'bridge')], check=True, timeout=15)

    @unittest.skipUnless(os.name == 'nt', '需要 Windows 与 MSVC x86')
    def test_capture_preserves_full_client_area_for_window_dpi_awareness(self):
        # A hard-coded unaware capture context crops system-aware windows at 125% DPI.
        from gameqakit.desktop import DesktopProcess
        source = (ROOT / 'plugins/wolf/Bridge.cpp').read_text(encoding='utf-8')
        begin = source.index('BOOL CALLBACK collect(')
        end = source.index('void audit_guard()', begin)
        (self.root / 'BridgeCapture.inc').write_text(source[begin:end], encoding='utf-8')
        output = self.root / 'CaptureTests.exe'
        args = ['cl', '/nologo', '/EHsc', '/W4', '/WX', '/std:c++17', '/utf-8',
                '/DWIN32_LEAN_AND_MEAN', '/DNOMINMAX', '/I' + str(self.root),
                str(ROOT / 'tests/wolf/CaptureTests.cpp'), '/Fe:' + str(output),
                '/link', 'user32.lib', 'gdi32.lib', 'gdiplus.lib']
        script = self.root / 'compile.cmd'
        script.write_text('@echo off\ncall "E:/vsCode/VSBuildTools/VC/Auxiliary/Build/vcvars32.bat" >nul\n'
                          'if errorlevel 1 exit /b 1\n' + subprocess.list2cmdline(args) + '\n', encoding='utf-8')
        subprocess.run(['cmd', '/d', '/c', str(script)], cwd=self.root, check=True, timeout=45)
        import itertools
        for awareness, mode in itertools.product(
                ('aware', 'unaware'), ('native', 'blank-full-content', 'black', 'white')):
            with self.subTest(awareness=awareness, mode=mode), DesktopProcess(
                    [str(output), str(self.root / (awareness + '-' + mode)), awareness, mode],
                    cwd=self.root) as child:
                child.run()
                from gameqakit.desktop import k, W
                self.assertEqual(k.WaitForSingleObject(child.process, 10000), 0, 'capture fixture did not exit')
                code = W.DWORD()
                self.assertTrue(k.GetExitCodeProcess(child.process, __import__('ctypes').byref(code)))
                self.assertEqual(code.value, 0, 'full client image or corner marker was lost')

    def test_native_control_core(self):
        compiler = Path('E:/vsCode/VSBuildTools/VC/Auxiliary/Build/vcvars32.bat')
        if os.name != 'nt' or not compiler.is_file():
            self.skipTest('需要已安装的 Windows MSVC x86')
        output = self.root / 'CoreTests.exe'
        result = subprocess.run(
            [sys.executable, str(ROOT / 'plugins/wolf/build.py'), '--test-core', '--output', str(output)],
            capture_output=True, encoding='utf-8', errors='replace', timeout=45,
            env={**os.environ, 'TEMP': str(self.root), 'TMP': str(self.root)})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Wolf core: all safety checks passed', result.stdout)


if __name__ == '__main__':
    unittest.main()
