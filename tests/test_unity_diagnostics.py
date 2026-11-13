"""诊断必须留下阻塞现场，且不能改变失联后的失败回收。"""
from contextlib import nullcontext
from pathlib import Path
import json
import os
import runpy
import subprocess
import tempfile
import unittest
from unittest import mock

from gameqakit import game_worker
from gameqakit.sessions import read_json, write_json

ROOT = Path(__file__).resolve().parents[1]


class UnityDiagnosticsTests(unittest.TestCase):
    def test_background_stage_snapshot_survives_stall_and_is_bounded(self):
        source = ROOT / 'plugins/unity/PhaseDiagnostics.cs'
        self.assertTrue(source.is_file(), '缺少独立于 Unity 主线程的有界阶段诊断')
        tools = runpy.run_path(str(ROOT / 'plugins/unity/build.py'))
        parent = Path('E:/DevData/gameqa-admission/tmp')
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='diagnostics-', dir=parent) as directory:
            output = Path(directory) / 'DiagnosticsTests.exe'
            tools['compile_sources'](output, [source, ROOT / 'plugins/unity/ControlCore.cs',
                                              ROOT / 'tests/unity/PhaseDiagnosticsTests.cs'])
            result = subprocess.run([str(output), directory], capture_output=True,
                                    encoding='utf-8', errors='replace', timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Unity diagnostics assertions: passed', result.stdout)
        print(result.stdout.strip())

    def run_disconnected_worker(self, folder, *, enabled, broken_sink=False):
        root = Path(folder) / 'session'
        request = Path(folder) / 'request.json'
        write_json(request, {'payload': {'destination': str(root), 'backend': 'unity_mono_rewired',
                                        'fingerprint': 'checked'}, 'request_id': 'request-a'})
        session = mock.Mock()
        session.host.alive.return_value = True
        session.observe_loading.return_value = False
        def prepare():
            (root / 'bridge').mkdir(parents=True)
            write_json(root / 'start.request.json', {'operation': 'start', 'directory': str(root.resolve()),
                                                    'payload': {'fingerprint': 'checked'}})
            write_json(root / 'bridge/status.json', {'session_id': 'test', 'updated_at': 100,
                                                    'frame': 45, 'held': ['right']})
            (root / 'bridge/lease.ini').write_text('[bridge]\nsession_id=test\nexpires_at=101\n', encoding='utf-8')
            (root / 'bridge/pause.ini').write_text('[bridge]\nsession_id=test\nid=pause-a\n', encoding='utf-8')
            (root / 'bridge/request.ini').write_text('[bridge]\nid=move-a\n', encoding='utf-8')
            write_json(root / 'bridge/diagnostics.json', {'session_id': 'other-session', 'phase': 'view',
                                                        'sampled_at': 9999999999999})
        session.prepare.side_effect = prepare
        session.start.return_value = {'state': 'ready_paused'}
        session.bridge.status.return_value = {'connected': False, 'paused': True, 'error': 'heartbeat expired'}
        def stop():
            write_json(root / 'bridge/status.json', {'frame': 999})
            return {'state': 'stopped', 'target_exited': True, 'release_confirmed': False}
        session.stop.side_effect = stop
        sink = (mock.patch('plugins.unity.diagnostics.write_json', side_effect=PermissionError('blocked'))
                if broken_sink else nullcontext())
        with mock.patch.dict(os.environ, {'GAMEQAKIT_UNITY_DIAGNOSTICS': '1' if enabled else '0'}), \
                mock.patch('plugins.unity.session.UnitySession', return_value=session), sink:
            result = game_worker._run(request)
        if broken_sink:
            self.assertFalse((root / 'disconnect.json').exists())
        self.assertEqual(result, 1)
        record = read_json(root / 'session.json')
        self.assertEqual(record['state'], 'failed')
        self.assertIn('heartbeat expired', record['error'])
        self.assertFalse(record['release_confirmed'])
        session.stop.assert_called_once()
        session.bridge.resume.assert_not_called()
        return root

    def test_disconnect_preserves_raw_status_and_control_generation_before_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.run_disconnected_worker(directory, enabled=True)
            self.assertTrue((root / 'disconnect.json').is_file(), '失联瞬间必须先保全原状态')
            snapshot = read_json(root / 'disconnect.json')
            self.assertEqual(snapshot['bridge_status']['error'], 'heartbeat expired')
            self.assertGreater(snapshot['monotonic_ns'], 0)
            raw = snapshot['files']
            self.assertEqual(json.loads(raw['status.json']['text'])['frame'], 45)
            self.assertEqual(json.loads(raw['status.json']['text'])['held'], ['right'])
            self.assertIn('id=pause-a', raw['pause.ini']['text'])
            self.assertIn('id=move-a', raw['request.ini']['text'])
            self.assertIn('expires_at=101', raw['lease.ini']['text'])
            self.assertGreater(snapshot['heartbeat_age_seconds'], 2)
            self.assertEqual(read_json(root / 'bridge/status.json')['frame'], 999)
            self.assertIn('other-session', raw['diagnostics.json']['text'])

    def test_disabled_diagnostics_creates_no_disconnect_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.run_disconnected_worker(directory, enabled=False)
            self.assertFalse((root / 'disconnect.json').exists())

    def test_diagnostic_write_failure_does_not_mask_original_failure_or_skip_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            self.run_disconnected_worker(directory, enabled=True, broken_sink=True)

    def test_oversized_or_invalid_status_is_capped_and_never_treated_as_authorization(self):
        from plugins.unity import diagnostics
        for body in (b'{invalid', b'x' * 200_000):
            with self.subTest(size=len(body)), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'bridge').mkdir()
                (root / 'bridge/status.json').write_bytes(body)
                with mock.patch.dict(os.environ, {'GAMEQAKIT_UNITY_DIAGNOSTICS': '1'}):
                    diagnostics.capture_disconnect(root, {'connected': False, 'error': 'invalid state'})
                    first = (root / 'disconnect.json').read_bytes()
                    diagnostics.capture_disconnect(root, {'connected': True, 'error': 'later state'})
                self.assertEqual((root / 'disconnect.json').read_bytes(), first)
                snapshot = read_json(root / 'disconnect.json')
                self.assertEqual(snapshot['files']['status.json']['truncated'], len(body) > 8192)
                self.assertLessEqual(len(snapshot['files']['status.json']['text']), 8192)
                self.assertIsNone(snapshot['heartbeat_age_seconds'])
                self.assertLess((root / 'disconnect.json').stat().st_size, 128 * 1024)
