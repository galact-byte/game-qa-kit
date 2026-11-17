"""真实自写 EXE 的加载期守卫、线程交接、持续守卫及 Job 回收。"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
import unittest
from gameqakit.desktop import DesktopProcess
from plugins.kirikiri.build import build


@unittest.skipUnless(os.name == 'nt', 'Win32 x86 原生验证')
class StartupGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        cls.root = Path(tempfile.mkdtemp(prefix='kirikiri-startup-', dir='E:/DevData'))
        cls.target = build(cls.root / 'StartupTarget.exe', mode='startup-target')
        cls.guard = build(cls.root / 'KiriKiriGuard.dll', mode='guard', shell_counter_experiment=True)
        cls.strict_guard = build(cls.root / 'StrictGuard.dll', mode='guard')
        cls.init_dll = build(cls.root / 'AuthoredInit.dll', mode='startup-dll')
        cls.host = build(cls.root / 'FixtureHost.exe', mode='host', fixture_target=cls.target)
        cls.guard_hash = hashlib.sha256(cls.guard.read_bytes()).hexdigest()
        (cls.root / 'outside').mkdir()
        cls.sentinel = cls.root / 'outside/sentinel.bin'
        cls.sentinel.write_bytes(b'original-boundary')
        cls.original = (cls.sentinel.read_bytes(), cls.sentinel.stat().st_mtime_ns)

    def wait(self, predicate, seconds=15):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.02)
        self.fail('超时；证据目录：' + str(self.root))

    def run_case(self, name, mode=None, *, expected=None, resume=True, terminate_host=False, guard_hash=None, guard=None):
        guard = guard or self.guard
        digest = hashlib.sha256(guard.read_bytes()).hexdigest()
        root = self.root / name
        root.mkdir()
        for folder in ('game','savedata','bridge','appdata','localappdata','tmp'):
            (root / folder).mkdir()
        shutil.copyfile(self.target, root / 'game/runtime.exe')
        shutil.copyfile(self.init_dll, root / 'game/AuthoredInit.dll')
        if mode:
            (root / mode).touch()
        with DesktopProcess([str(self.host), str(root), str(guard), guard_hash or digest], cwd=root) as owner:
            owner.run()
            self.wait(lambda: (root / 'process.json').exists() or not owner.alive())
            if guard_hash:
                self.assertFalse((root / 'process.json').exists())
                self.assertFalse((root / 'savedata/early.bin').exists())
                return
            self.assertTrue((root / 'process.json').exists(), str(root))
            info = json.loads((root / 'process.json').read_text())
            self.assertTrue(info['guard_ready'])
            # No target static initializer runs while all loader threads are parked.
            self.assertFalse((root / 'savedata/early.bin').exists())
            if resume:
                (root / 'resume').touch()
            if terminate_host:
                from gameqakit.desktop import k
                self.assertTrue(k.TerminateProcess(owner.process, 99))
                self.wait(lambda: not owner.alive())
            elif not resume:
                (root / 'stop').touch()
                self.wait(lambda: not owner.alive())
                self.assertFalse((root / 'savedata/early.bin').exists())
            elif expected:
                self.wait(lambda: not owner.alive())
                result = json.loads((root / 'host-result.json').read_text())
                self.assertEqual(result['reason'], expected, str(root))
            elif mode:
                self.wait(lambda: not owner.alive())
                self.assertEqual(json.loads((root / 'host-result.json').read_text())['reason'], 'write_guard_denied')
                self.assertGreater(json.loads((root / 'bridge/guard-denial.json').read_text())['denied'], 0)
                self.assertTrue((root / 'savedata/denied-returned.bin').exists(),
                                '越界请求必须返回正常拒绝语义，不能永久挂起调用线程')
            else:
                self.wait(lambda: (root / 'savedata/static-init-confirmed.bin').exists() or not owner.alive())
                self.assertTrue((root / 'savedata/early.bin').exists(), str(root))
                self.assertTrue((root / 'savedata/static-init-confirmed.bin').exists(), str(root))
                (root / 'stop').touch()
                self.wait(lambda: not owner.alive())
            from gameqakit.win32 import _pid_path
            self.wait(lambda: _pid_path(info['pid']) == '')
            self.assertEqual(_pid_path(info['pid']), '')
        self.assertEqual((self.sentinel.read_bytes(), self.sentinel.stat().st_mtime_ns), self.original)

    def test_startup_handoff_preserves_static_initializers(self):
        self.run_case('normal')

    def test_pre_main_external_write_stops_batch(self):
        self.run_case('before-main', 'attempt-before-main')

    def test_later_external_write_stops_batch(self):
        self.run_case('later', 'attempt-outside')

    def test_dll_initializer_external_write_stops_batch(self):
        self.run_case('dll', 'attempt-dll-init')

    def test_changed_guard_hash_never_resumes_target(self):
        self.run_case('bad-guard', guard_hash='0' * 64)

    def test_stop_before_resume_runs_no_static_initializer(self):
        self.run_case('stop-before-resume', resume=False)

    def test_exact_shell_request_stays_denied_and_host_allows_normal_exit(self):
        self.run_case('shell-denied', 'attempt-shell-section', expected='target_exit')
        root = self.root / 'shell-denied'
        self.assertTrue((root / 'savedata/shell-denied-returned.bin').is_file())
        record = json.loads((root / 'bridge/nonfatal-rejections.json').read_text())
        self.assertEqual(record['count'], 1)
        self.assertEqual(record['status'], '0xc0000022')
        self.assertEqual(record['access'], 6)
        self.assertFalse(record['fatal'])
        self.assertTrue(record['path'].endswith('\\BaseNamedObjects\\windows_shell_global_counters'))
        self.assertFalse((root / 'bridge/guard-denial.json').exists())

    def test_exact_shell_creation_stays_denied_and_host_allows_normal_exit(self):
        self.run_case('shell-create-denied', 'attempt-shell-create', expected='target_exit')
        root = self.root / 'shell-create-denied'
        self.assertTrue((root / 'savedata/shell-denied-returned.bin').is_file())
        record = json.loads((root / 'bridge/nonfatal-creation-rejections.json').read_text())
        self.assertEqual(record['count'], 1)
        self.assertEqual(record['api'], 'NtCreateSection(named)')
        self.assertEqual(record['status'], '0xc0000022')
        self.assertEqual(record['access'], 0xf0007)
        self.assertFalse(record['fatal'])
        self.assertTrue(record['path'].endswith('\\BaseNamedObjects\\windows_shell_global_counters'))
        self.assertFalse((root / 'bridge/guard-denial.json').exists())

    def test_default_guard_keeps_exact_shell_creation_fatal(self):
        self.run_case('shell-create-default-strict', 'attempt-shell-create', guard=self.strict_guard, expected='write_guard_denied')
        record = json.loads((self.root / 'shell-create-default-strict/bridge/guard-denial.json').read_text())
        self.assertEqual(record['api'], 'NtCreateSection(named)')
        self.assertEqual(record['control'], 0xf0007)

    def test_default_guard_keeps_exact_shell_request_fatal(self):
        self.run_case('shell-default-strict', 'attempt-shell-section', guard=self.strict_guard, expected='write_guard_denied')
        record = json.loads((self.root / 'shell-default-strict/bridge/guard-denial.json').read_text())
        self.assertEqual(record['api'], 'NtOpenSection(write)')
        self.assertEqual(record['control'], 6)

    def test_exact_cng_request_returns_denial_and_host_allows_normal_exit(self):
        self.run_case('cng-denied', 'attempt-cng', expected='target_exit')
        root = self.root / 'cng-denied'
        self.assertTrue((root / 'savedata/cng-denied-returned.bin').is_file())
        record = json.loads((root / 'bridge/nonfatal-cng-rejections.json').read_text())
        self.assertEqual(record['count'], 1)
        self.assertEqual(record['path'], '\\Device\\CNG')
        self.assertEqual(record['control'], 0x390008)
        self.assertEqual(record['status'], '0xc0000022')
        self.assertFalse(record['fatal'])
        self.assertFalse((root / 'bridge/guard-denial.json').exists())

    def test_default_guard_keeps_exact_cng_request_fatal(self):
        self.run_case('cng-strict', 'attempt-cng', guard=self.strict_guard, expected='write_guard_denied')
        record = json.loads((self.root / 'cng-strict/bridge/guard-denial.json').read_text())
        self.assertEqual(record['path'], '\\Device\\CNG')
        self.assertEqual(record['control'], 0x390008)

    def test_normal_target_exit_reaps_job(self):
        self.run_case('exits', 'exit-target', expected='target_exit')

    def test_hook_integrity_failure_reaps_job(self):
        self.run_case('damaged', 'damage-hook', expected='write_guard_denied')

    def test_host_termination_reaps_suspended_target(self):
        self.run_case('killed-before-resume', resume=False, terminate_host=True)

    def test_host_termination_reaps_running_target(self):
        self.run_case('killed-running', terminate_host=True)
