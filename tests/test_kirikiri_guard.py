"""原生边界以真实 Win32 调用验证；生成物遵循临时目录配置。"""
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

BASE = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == 'nt', '需要 Windows 原生文件 API')
class KiriKiriGuardTests(unittest.TestCase):
    def test_state_channel_rejects_stale_protocol_identity_and_releases_handles(self):
        build = importlib.import_module('plugins.kirikiri.build')
        root = Path(tempfile.mkdtemp(prefix='kirikiri-state-'))
        executable = build.build(root / 'StateChannelTests.exe', mode='state-tests')
        result = subprocess.run([str(executable)], capture_output=True, timeout=15)
        (root / 'stdout.txt').write_bytes(result.stdout + result.stderr)
        self.assertEqual(result.returncode, 0, str(root) + '\n' + result.stdout.decode(errors='replace'))
        report = json.loads(result.stdout)
        self.assertTrue(report['protocol_identity'])
        self.assertTrue(report['version_length_pid_creation_rejected'])
        self.assertTrue(report['shared_updates'])
        self.assertTrue(report['non_inherited_handle'])
        self.assertEqual(report['cycles'], 32)
        self.assertEqual(report['handles_before'], report['handles_after'])

    def test_real_file_handle_and_mapping_boundaries(self):
        self.run_boundary_case(experiment=False)

    def test_fixture_exception_still_refuses_handle_and_limits_scope(self):
        self.run_boundary_case(experiment=True)

    def test_commercial_compat_refuses_exact_requests_without_relaxing_neighbors(self):
        self.run_boundary_case(experiment=False, commercial=True)

    def run_boundary_case(self, *, experiment, commercial=False):
        self.assertTrue((BASE / 'plugins/kirikiri/Guard.hpp').is_file(), 'KiriKiri 专用写入守卫尚未实现')
        build = importlib.import_module('plugins.kirikiri.build')
        root = Path(tempfile.mkdtemp(prefix='kirikiri-guard-'))
        for folder in ('run/savedata', 'run/appdata', 'run/localappdata', 'run/tmp', 'run/bridge', 'run/game', 'outside'):
            (root / folder).mkdir(parents=True, exist_ok=True)
        sentinel = root / 'outside/sentinel.bin'
        sentinel.write_bytes(b'protected content')
        (root / 'run/game/code.tjs').write_bytes(b'protected code')
        os.link(sentinel, root / 'run/savedata/hardlink.bin')
        made = subprocess.run(['cmd', '/d', '/c', 'mklink', '/J', str(root / 'run/savedata/junction'), str(root / 'outside')],
                              capture_output=True, check=True)
        before = sentinel.stat()
        executable = build.build(root / 'GuardTests.exe', mode='guard-tests',
                                 shell_counter_experiment=experiment, commercial_compat=commercial)
        result = subprocess.run([str(executable), str(root / 'run'), str(sentinel)], capture_output=True, timeout=15)
        (root / 'stdout.txt').write_bytes(result.stdout + result.stderr)
        self.assertEqual(result.returncode, 0, str(root) + '\n' + result.stdout.decode('utf-8', errors='replace'))
        self.assertEqual(sentinel.read_bytes(), b'protected content')
        self.assertEqual(sentinel.stat().st_mtime_ns, before.st_mtime_ns)
        report = json.loads((root / 'run/bridge/target-observer.tjs').read_text())
        self.assertGreaterEqual(len(report), 15)
        self.assertTrue(all(report.values()), str(report))


if __name__ == '__main__':
    unittest.main()
