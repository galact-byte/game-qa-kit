"""固定 Unity Mono 受管接入的摘要、数据边界与生命周期回归。"""
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from gameqakit import admission
from gameqakit.sessions import write_json
from plugins.unity import prepare as unity
from plugins.unity.session import UnitySession


class UnityAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.artifacts = self.root / 'artifacts'
        for name in unity.SOURCE_DIGESTS:
            item = self.source / name
            item.parent.mkdir(parents=True, exist_ok=True)
            item.write_bytes(name.encode())
        for name in unity.CORE_DLLS:
            item = self.artifacts / 'BepInEx/core' / name
            item.parent.mkdir(parents=True, exist_ok=True)
            item.write_bytes(name.encode())
        for name in ('winhttp.dll', 'GameQaBridge.dll'):
            (self.artifacts / name).write_bytes(name.encode())
        (self.source / 'SaveData').mkdir()
        (self.source / 'SaveData/original.save').write_bytes(b'original progress')
        (self.source / 'BepInEx/plugins').mkdir(parents=True)
        (self.source / 'BepInEx/plugins/original-plugin.dll').write_bytes(b'original plugin')
        self.patches = [
            mock.patch.object(unity, 'SOURCE_DIGESTS', {name: admission.file_digest(self.source / name)
                                                     for name in unity.SOURCE_DIGESTS}),
            mock.patch.object(unity, 'MANAGED_FINGERPRINT', admission.fingerprint(
                unity.managed_manifest(self.source))),
            mock.patch.object(unity, 'LOADER_DIGEST', admission.file_digest(self.artifacts / 'winhttp.dll')),
            mock.patch.object(unity, 'local_low', return_value=self.root / 'LocalLow'),
            mock.patch.object(unity, 'registry_snapshot', return_value={})]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.plan = admission.make_plan(self.source, self.root / 'session', artifacts=self.artifacts)

    def test_fixed_unity_plan_is_explicit_and_inspection_does_not_write(self):
        before = list(self.root.rglob('*'))
        checked = admission.inspect(self.source, artifacts=self.artifacts)
        self.assertEqual(checked['launch_readiness'], 'ready')
        self.assertEqual(self.plan['backend'], 'unity_mono_legacy')
        self.assertEqual(self.plan['desktop'], 'private')
        self.assertIn('只修改副本', '；'.join(self.plan['changes']))
        self.assertEqual(before, list(self.root.rglob('*')))
        self.assertEqual(admission.revalidate_plan(self.plan), self.plan)

    def test_unknown_script_or_loader_build_stays_blocked(self):
        for name in ('Taimashi_Data/Managed/Assembly-CSharp.dll', 'Taimashi.exe'):
            item = self.source / name
            original = item.read_bytes()
            item.write_bytes(b'unknown')
            self.assertEqual(admission.inspect(self.source, artifacts=self.artifacts)['launch_readiness'], 'blocked')
            item.write_bytes(original)
        (self.source / 'Taimashi_Data/Managed/Extra.dll').write_bytes(b'unknown script')
        self.assertEqual(admission.inspect(self.source, artifacts=self.artifacts)['launch_readiness'], 'blocked')
        (self.source / 'Taimashi_Data/Managed/Extra.dll').unlink()
        (self.artifacts / 'winhttp.dll').write_bytes(b'unknown loader')
        self.assertEqual(admission.inspect(self.source, artifacts=self.artifacts)['launch_readiness'], 'blocked')

    def test_artifact_changes_invalidate_confirmation_before_copying(self):
        (self.artifacts / 'GameQaBridge.dll').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'plan_changed'):
            UnitySession(self.plan).prepare()
        self.assertFalse(Path(self.plan['destination']).exists())

    def prepare_session(self):
        session = UnitySession(self.plan)
        def patch_settings(data, company):
            (data / 'globalgamemanagers').write_bytes(company.encode())
            (data / 'app.info').write_text(company + '\nCompatibilityProbe\n', encoding='utf-8')
        with mock.patch.object(unity, 'patch_settings', side_effect=patch_settings):
            session.prepare()
        return session

    def test_prepare_preserves_originals_omits_original_progress_and_plugins(self):
        before = unity.manifest(self.source)
        session = self.prepare_session()
        runtime = session.root / 'runtime'
        self.assertEqual(before, unity.manifest(self.source))
        self.assertFalse((runtime / 'SaveData').exists())
        self.assertFalse((runtime / 'BepInEx/plugins/original-plugin.dll').exists())
        self.assertEqual((runtime / 'BepInEx/plugins/GameQaKit/GameQaBridge.dll').read_bytes(), b'GameQaBridge.dll')
        self.assertEqual(session.baseline, unity.manifest(runtime))
        self.assertFalse(session.link.exists())
        result = session.stop()
        self.assertEqual(result['state'], 'stopped')
        self.assertTrue(result['source_unchanged'])
        self.assertFalse(runtime.exists())

    def test_new_progress_and_changed_external_data_are_retained(self):
        session = self.prepare_session()
        progress = session.root / 'runtime/SaveData/new.save'
        progress.parent.mkdir()
        progress.write_bytes(b'new progress')
        external = self.root / 'LocalLow/DefaultCompany/Taimashi'
        external.mkdir(parents=True)
        (external / 'changed.save').write_bytes(b'external changed')
        result = session.stop()
        self.assertEqual(result['state'], 'cleanup_failed')
        self.assertTrue(result['runtime_retained'])
        self.assertEqual(progress.read_bytes(), b'new progress')

    @unittest.skipUnless(os.name == 'nt', '需要 Windows 目录联接')
    def test_private_save_junction_targets_session_and_removal_keeps_progress(self):
        session = self.prepare_session()
        session.connect_persistent()
        self.assertTrue(session.link.is_junction())
        marker = session.link / 'CompatibilityProbe/progress.save'
        marker.parent.mkdir()
        marker.write_bytes(b'private progress')
        self.assertEqual((session.root / 'persistent/CompatibilityProbe/progress.save').read_bytes(), b'private progress')
        result = session.stop()
        self.assertEqual(result['state'], 'stopped')
        self.assertFalse(session.link.exists())
        self.assertEqual((session.root / 'persistent/CompatibilityProbe/progress.save').read_bytes(), b'private progress')

    def test_cancellation_before_launch_creates_no_target_or_external_link(self):
        session = self.prepare_session()
        with mock.patch('plugins.unity.session.DesktopProcess') as host:
            with self.assertRaises(InterruptedError):
                session.start(lambda: True)
            host.assert_not_called()
        self.assertFalse(session.link.exists())
        self.assertEqual(session.stop()['state'], 'stopped')

    def test_black_first_frame_fails_without_input_and_stop_reaps_target(self):
        from PIL import Image
        session = self.prepare_session()
        image = session.root / 'bridge/screenshots/startup.png'
        image.parent.mkdir(parents=True)
        Image.new('RGB', (32, 24), 'black').save(image)
        client, host = mock.Mock(), mock.MagicMock()
        host.pid = 123456789
        host.alive.return_value = True
        client.status.return_value = {'connected': True, 'input_ready': True, 'observation_seq': 1,
                                      'session_id': 'sample', 'paused': True, 'held': []}
        with mock.patch('plugins.unity.session.DesktopProcess', return_value=host), \
                mock.patch('plugins.unity.session.BridgeClient', return_value=client), \
                mock.patch('plugins.unity.session._pid_path', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'startup_image_invalid'):
                session.start()
            result = session.stop()
        self.assertEqual(result['state'], 'stopped')
        self.assertTrue(result['target_exited'])
        self.assertTrue(result['private_link_removed'])
        host.close.assert_called_once()
        client.resume.assert_not_called()
        client.execute.assert_not_called()

    def test_unconfirmed_release_records_failure_even_when_job_has_exited(self):
        session = self.prepare_session()
        session.host, session.bridge = mock.Mock(), mock.Mock()
        session.host.pid = 123456789
        session.bridge.status.return_value = {'connected': True, 'paused': False, 'held': ['ok']}
        clock = mock.Mock()
        clock.monotonic.side_effect = [0, 0, 4]
        with mock.patch('plugins.unity.session.time', clock), \
                mock.patch('plugins.unity.session._pid_path', return_value=None):
            result = session.stop()
        self.assertEqual(result['state'], 'cleanup_failed')
        self.assertFalse(result['release_confirmed'])
        self.assertTrue(result['target_exited'])
        self.assertTrue(result['runtime_retained'])
        session.host.close.assert_called_once()

    def test_disconnected_bridge_does_not_claim_release_or_discard_runtime(self):
        session = self.prepare_session()
        session.host, session.bridge = mock.Mock(), mock.Mock()
        session.host.pid = 123456789
        session.bridge.status.return_value = {'connected': False, 'paused': True, 'error': 'heartbeat expired'}
        with mock.patch('plugins.unity.session._pid_path', return_value=None):
            result = session.stop()
        self.assertEqual(result['state'], 'cleanup_failed')
        self.assertIs(result['release_confirmed'], False)
        self.assertIn('heartbeat expired', result['release_error'])
        self.assertTrue(result['target_exited'])
        self.assertTrue(result['runtime_retained'])
        session.host.close.assert_called_once()
        session.bridge.resume.assert_not_called()

    def test_changed_runtime_is_rejected_before_target_creation(self):
        session = self.prepare_session()
        (session.root / 'runtime/Taimashi.exe').write_bytes(b'changed')
        with mock.patch('plugins.unity.session.DesktopProcess') as host:
            with self.assertRaisesRegex(RuntimeError, 'runtime_changed'):
                session.start()
            host.assert_not_called()


if __name__ == '__main__':
    unittest.main()
