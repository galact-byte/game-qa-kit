"""Unity 新 Input System 固定构建的受管准备与生命周期回归。"""
import os
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from gameqakit import admission, game_worker
from plugins.unity import prepare as unity
from plugins.unity.session import UnitySession


class UnityInputAdmissionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source, self.artifacts = self.root / 'source', self.root / 'artifacts'
        self.data = self.source / 'OmaNeko-Seikatsu_Data'
        for name in ('OmaNeko-Seikatsu.exe', 'UnityPlayer.dll',
                     'OmaNeko-Seikatsu_Data/globalgamemanagers',
                     'OmaNeko-Seikatsu_Data/Managed/Assembly-CSharp.dll'):
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(name.encode())
        self.raw = b'prefix' + struct.pack('<I', 7) + b'omaneko' + b'\0' + struct.pack('<I', 16) + b'OmaNeko-Seikatsu' + b'suffix'
        (self.data / 'globalgamemanagers').write_bytes(self.raw)
        (self.data / 'app.info').write_text('omaneko\nOmaNeko-Seikatsu\n', encoding='utf-8')
        for name in ('winhttp.dll', 'GameQaBridge.dll', 'GameQaInputSystem.dll',
                     *('BepInEx/core/' + name for name in unity.CORE_DLLS)):
            path = self.artifacts / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(name.encode())
        self.digests = {name: admission.file_digest(self.source / name) for name in (
            'OmaNeko-Seikatsu.exe', 'UnityPlayer.dll', 'OmaNeko-Seikatsu_Data/globalgamemanagers',
            'OmaNeko-Seikatsu_Data/Managed/Assembly-CSharp.dll')}
        managed = {'OmaNeko-Seikatsu_Data/Managed/Assembly-CSharp.dll': self.digests['OmaNeko-Seikatsu_Data/Managed/Assembly-CSharp.dll']}
        for patch in (
            mock.patch.object(unity, 'INPUT_SYSTEM_DIGESTS', self.digests, create=True),
            mock.patch.object(unity, 'INPUT_SYSTEM_MANAGED_FINGERPRINT', admission.fingerprint(managed), create=True),
            mock.patch.object(unity, 'LOADER_DIGEST', admission.file_digest(self.artifacts / 'winhttp.dll')),
            mock.patch.object(unity, 'local_low', return_value=self.root / 'LocalLow'),
            mock.patch.object(unity, 'registry_snapshot', return_value={})):
            patch.start()
            self.addCleanup(patch.stop)

    def plan(self):
        return admission.make_plan(self.source, self.root / 'session', artifacts=self.artifacts)

    def test_inspection_and_plan_select_new_input_without_writing(self):
        before = unity.manifest(self.source)
        checked = admission.inspect(self.source, artifacts=self.artifacts)
        self.assertEqual(checked['launch_readiness'], 'ready')
        self.assertEqual(checked['profile_id'], 'unity-mono-input-system-omaneko-fixed')
        plan = self.plan()
        self.assertEqual(plan['backend'], 'unity_mono_input_system')
        self.assertIn('GameQaInputSystem.dll', plan['artifact_digests'])
        self.assertEqual(admission.revalidate_plan(plan), plan)
        self.assertEqual(before, unity.manifest(self.source))
        self.assertFalse((self.root / 'session').exists())

    def test_missing_input_plugin_blocks_new_input(self):
        (self.artifacts / 'GameQaInputSystem.dll').unlink()
        checked = admission.inspect(self.source, artifacts=self.artifacts)
        self.assertEqual(checked['launch_readiness'], 'blocked')
        self.assertTrue(any(row['code'] == 'missing_artifact' for row in checked['blockers']))

    def test_changed_input_assembly_stays_blocked(self):
        path = self.data / 'Managed/Unity.InputSystem.dll'
        path.write_bytes(b'unknown input system')
        self.assertEqual(admission.inspect(self.source, artifacts=self.artifacts)['launch_readiness'], 'blocked')

    def test_changed_input_plugin_invalidates_plan_before_copy(self):
        plan = self.plan()
        (self.artifacts / 'GameQaInputSystem.dll').write_bytes(b'changed plugin')
        with self.assertRaisesRegex(ValueError, 'plan_changed'):
            UnitySession(plan).prepare()
        self.assertFalse((self.root / 'session').exists())

    def prepare(self):
        # UnityPy 无法解析本构建的类型树；在序列化对象边界替身之上验证真实准备器的字节修改。
        settings = SimpleNamespace(type=SimpleNamespace(name='PlayerSettings'),
                                   raw=self.raw)
        settings.get_raw_data = lambda: settings.raw
        settings.set_raw_data = lambda raw: setattr(settings, 'raw', raw)
        env = SimpleNamespace(objects=[settings], file=SimpleNamespace(save=lambda: settings.raw))
        with mock.patch('UnityPy.load', return_value=env):
            session = UnitySession(self.plan())
            session.prepare()
        self.addCleanup(session.stop)
        return session

    def test_prepare_uses_private_identity_and_copies_both_input_plugins(self):
        before = unity.manifest(self.source)
        session = self.prepare()
        runtime = session.root / 'runtime'
        company, product = (runtime / 'OmaNeko-Seikatsu_Data/app.info').read_text(encoding='utf-8').splitlines()
        self.assertEqual((len(company), len(product)), (7, 16))
        self.assertTrue(company.startswith('QA') and product.startswith('QA'))
        changed = (runtime / 'OmaNeko-Seikatsu_Data/globalgamemanagers').read_bytes()
        self.assertNotIn(b'omaneko', changed)
        self.assertNotIn(b'OmaNeko-Seikatsu', changed)
        self.assertIn(struct.pack('<I', 7) + company.encode(), changed)
        self.assertIn(struct.pack('<I', 16) + product.encode(), changed)
        self.assertEqual(len(changed), len(self.raw))
        for name in ('GameQaBridge.dll', 'GameQaInputSystem.dll'):
            self.assertEqual((runtime / 'BepInEx/plugins/GameQaKit' / name).read_bytes(), name.encode())
        self.assertEqual(before, unity.manifest(self.source))
        result = session.stop()
        self.assertEqual(result['state'], 'stopped')
        self.assertFalse(runtime.exists())

    def test_ambiguous_private_identity_aborts_before_any_game_launch(self):
        self.raw += struct.pack('<I', 7) + b'omaneko'
        with self.assertRaisesRegex(ValueError, 'private_identity_marker_mismatch'):
            self.prepare()
        self.assertFalse((self.root / 'session/bridge').exists())
        self.assertEqual((self.data / 'app.info').read_text(encoding='utf-8'), 'omaneko\nOmaNeko-Seikatsu\n')

    def test_start_uses_input_backend_private_product_and_fixed_executable(self):
        from gameqakit.sessions import read_json
        session = self.prepare()
        record = read_json(session.root / 'unity-preparation.json')
        with mock.patch.object(session, 'connect_persistent'), \
             mock.patch('plugins.unity.session.DesktopProcess') as host_factory, \
             mock.patch('plugins.unity.session.BridgeClient'), \
             mock.patch.object(session, 'wait_ready', return_value={'state': 'ready_paused'}) as wait:
            host_factory.return_value.pid = 0
            result = session.start()
        try:
            self.assertEqual(result['state'], 'ready_paused')
            args, kwargs = host_factory.call_args
            self.assertEqual(Path(args[0][0]).name, 'OmaNeko-Seikatsu.exe')
            self.assertIn('-force-d3d11', args[0])
            self.assertEqual(kwargs['env']['GAMEQAKIT_INPUT_BACKEND'], 'input_system')
            self.assertEqual(wait.call_args.args[0], session.root / 'persistent' / record['product'])
            self.assertFalse((session.root / 'bridge/request.ini').exists())
        finally:
            session.host = session.bridge = None

    def test_cleanup_checks_new_input_original_persistent_and_registry(self):
        session = self.prepare()
        original = self.root / 'LocalLow/omaneko/OmaNeko-Seikatsu/ReleaseV1/Saves'
        original.mkdir(parents=True)
        (original / 'unexpected.save').write_bytes(b'external change')
        result = session.stop()
        self.assertEqual(result['state'], 'cleanup_failed')
        self.assertFalse(result['external_unchanged'])
        self.assertTrue(result['runtime_retained'])

    def test_worker_dispatches_new_input_to_managed_unity_session(self):
        session = game_worker.session_for(self.plan())
        self.assertIsInstance(session, UnitySession)


if __name__ == '__main__':
    unittest.main()
