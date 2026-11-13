"""固定 Unity 5.6 Rewired 构建的受管登记、准备与生命周期回归。"""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from gameqakit import admission, game_worker
from gameqakit.sessions import read_json, write_json
from plugins.unity import prepare as unity
from plugins.unity.session import UnitySession


class UnityRewiredAdmissionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source, self.artifacts = self.root / 'source', self.root / 'artifacts'
        self.data = self.source / 'NightofRevenge_Data'
        names = ('NightofRevenge.exe', 'NightofRevenge_Data/globalgamemanagers',
                 'NightofRevenge_Data/Managed/Assembly-CSharp.dll')
        for name in (*names, 'NightofRevenge_Data/Managed/Rewired_Core.dll',
                     'SaveData/SaveData01.txt', 'OtherConfigData/OtherConfigData.txt',
                     'BepInEx/plugins/original.dll'):
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(name.encode())
        (self.data / 'app.info').write_text('D-lis\nNightofRevenge\n', encoding='utf-8')
        for name in ('winhttp.dll', 'GameQaBridge.dll', 'GameQaRewired.dll',
                     *('BepInEx/core/' + name for name in unity.CORE_DLLS)):
            path = self.artifacts / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(name.encode())
        digests = {name: admission.file_digest(self.source / name) for name in names}
        managed = {path.relative_to(self.source).as_posix(): admission.file_digest(path)
                   for path in (self.data / 'Managed').iterdir()}
        for patch in (
            mock.patch.object(unity, 'REWIRED_DIGESTS', digests, create=True),
            mock.patch.object(unity, 'REWIRED_MANAGED_FINGERPRINT', admission.fingerprint(managed), create=True),
            mock.patch.object(unity, 'REWIRED_LOADER_DIGEST', admission.file_digest(self.artifacts / 'winhttp.dll'), create=True),
            mock.patch.object(unity, 'local_low', return_value=self.root / 'LocalLow'),
            mock.patch.object(unity, 'registry_snapshot', return_value={})):
            patch.start()
            self.addCleanup(patch.stop)
        self.values = {'companyName': 'D-lis', 'productName': 'NightofRevenge', 'displayResolutionDialog': 1}

    def plan(self):
        return admission.make_plan(self.source, self.root / 'session', artifacts=self.artifacts)

    def prepare_session(self):
        settings = SimpleNamespace(type=SimpleNamespace(name='PlayerSettings'),
                                   read_typetree=lambda: dict(self.values),
                                   save_typetree=lambda values: self.values.update(values))
        env = SimpleNamespace(objects=[settings], file=SimpleNamespace(save=lambda: repr(self.values).encode()))
        session = UnitySession(self.plan())
        with mock.patch('UnityPy.load', return_value=env):
            session.prepare()
        self.addCleanup(session.stop)
        return session

    def test_fixed_rewired_inspection_and_plan_are_read_only(self):
        before = unity.manifest(self.source)
        checked = admission.inspect(self.source, artifacts=self.artifacts)
        self.assertEqual(checked['launch_readiness'], 'ready')
        self.assertEqual(checked['profile_id'], 'unity-mono-rewired-nightofrevenge-fixed')
        plan = self.plan()
        self.assertEqual(plan['backend'], 'unity_mono_rewired')
        self.assertEqual(plan['data_policy'], 'copied-unity-progress-private-identity')
        self.assertIn('SaveData 和 OtherConfigData', '\n'.join(plan['changes']))
        self.assertIn('GameQaRewired.dll', plan['artifact_digests'])
        self.assertNotIn('GameQaInputSystem.dll', plan['artifact_digests'])
        self.assertEqual(admission.revalidate_plan(plan), plan)
        self.assertEqual(before, unity.manifest(self.source))
        self.assertFalse((self.root / 'session').exists())

    def test_missing_rewired_plugin_or_wrong_loader_blocks(self):
        for name in ('GameQaRewired.dll', 'winhttp.dll'):
            path = self.artifacts / name
            before = path.read_bytes()
            if name == 'GameQaRewired.dll':
                path.unlink()
            else:
                path.write_bytes(b'wrong loader')
            result = admission.inspect(self.source, artifacts=self.artifacts)
            self.assertTrue(any(row['code'] == 'missing_artifact' for row in result['blockers']))
            path.write_bytes(before)

    def test_changed_rewired_assembly_is_rejected(self):
        (self.data / 'Managed/Rewired_Core.dll').write_bytes(b'changed middleware')
        result = admission.inspect(self.source, artifacts=self.artifacts)
        self.assertEqual(result['launch_readiness'], 'blocked')
        self.assertTrue(any(row['code'] == 'source_not_ready' for row in result['blockers']))

    def test_changed_rewired_plugin_invalidates_plan_before_copy(self):
        plan = self.plan()
        (self.artifacts / 'GameQaRewired.dll').write_bytes(b'changed plugin')
        with self.assertRaisesRegex(ValueError, 'plan_changed'):
            UnitySession(plan).prepare()
        self.assertFalse((self.root / 'session').exists())

    def test_prepare_uses_private_identity_preserves_copied_progress_and_copies_plugin(self):
        before = unity.manifest(self.source)
        session = self.prepare_session()
        runtime = session.root / 'runtime'
        record = read_json(session.root / 'unity-preparation.json')
        self.assertTrue(self.values['companyName'].startswith('GameQaKitSession_'))
        self.assertEqual(self.values['productName'], 'CompatibilityProbe')
        self.assertEqual(self.values['displayResolutionDialog'], 0)
        self.assertTrue(self.values['runInBackground'])
        self.assertEqual((runtime / 'NightofRevenge_Data/app.info').read_text(encoding='utf-8').splitlines(),
                         [record['company'], record['product']])
        self.assertFalse((runtime / 'BepInEx/plugins/original.dll').exists())
        for name in ('SaveData/SaveData01.txt', 'OtherConfigData/OtherConfigData.txt'):
            self.assertEqual((runtime / name).read_bytes(), (self.source / name).read_bytes())
        for name in ('GameQaBridge.dll', 'GameQaRewired.dll'):
            self.assertEqual((runtime / 'BepInEx/plugins/GameQaKit' / name).read_bytes(), name.encode())
        external = read_json(session.root / 'external-before.json')
        self.assertEqual((external['company'], external['product']), ('D-lis', 'NightofRevenge'))
        self.assertEqual(before, unity.manifest(self.source))
        self.assertEqual(session.stop()['state'], 'stopped')

    def test_cleanup_reads_generated_source_manifest_larger_than_control_limit(self):
        session = self.prepare_session()
        manifest = {f'assets/{index:04d}/' + 'resource-name' * 4: '0' * 64 for index in range(1000)}
        snapshot = session.root / 'source-before.json'
        write_json(snapshot, manifest)
        self.assertGreater(snapshot.stat().st_size, 131072)
        original = unity.manifest
        with mock.patch.object(unity, 'manifest', side_effect=lambda path: manifest if Path(path) == self.source else original(path)):
            result = session.stop()
        write_json(snapshot, original(self.source))
        self.assertTrue(result['source_unchanged'])
        self.assertEqual(result['state'], 'stopped')

    def test_unexpected_original_save_identity_is_rejected(self):
        self.values['companyName'] = 'unexpected company'
        with self.assertRaisesRegex(ValueError, 'unexpected_unity_save_identity'):
            self.prepare_session()
        self.assertFalse((self.root / 'session/bridge').exists())

    def test_start_uses_rewired_backend_and_fixed_executable(self):
        session = self.prepare_session()
        with mock.patch.object(session, 'connect_persistent'), \
             mock.patch('plugins.unity.session.DesktopProcess') as factory, \
             mock.patch('plugins.unity.session.BridgeClient'), \
             mock.patch.object(session, 'wait_ready', return_value={'state': 'ready_paused'}) as wait:
            factory.return_value.pid = 0
            result = session.start()
        try:
            self.assertEqual(result['state'], 'ready_paused')
            args, kwargs = factory.call_args
            self.assertEqual(Path(args[0][0]).name, 'NightofRevenge.exe')
            self.assertEqual(kwargs['env']['GAMEQAKIT_INPUT_BACKEND'], 'rewired')
            self.assertEqual(wait.call_args.args[0], session.root / 'persistent/CompatibilityProbe')
            self.assertFalse((session.root / 'bridge/request.ini').exists())
        finally:
            session.host = session.bridge = None

    def test_worker_dispatches_rewired_to_common_unity_lifecycle(self):
        self.assertIsInstance(game_worker.session_for(self.plan()), UnitySession)


if __name__ == '__main__':
    unittest.main()
