"""固定 IL2CPP 构建受管接入的身份隔离、离线依赖及生命周期回归。"""
import configparser
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from gameqakit import admission, game_worker
from gameqakit.sessions import read_json
from plugins.unity import prepare as unity
from plugins.unity.session import UnitySession


class UnityIl2CppAdmissionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source, self.artifacts = self.root / 'source', self.root / 'artifacts'
        self.data = self.source / 'NoviceSuccubusLillia_Data'
        self.raw = b'prefix' + struct.pack('<I', 10) + b'PaconeSoft' + b'\0' + struct.pack('<I', 22) + b'Novice Succubus Lillia' + b'suffix'
        names = ('NoviceSuccubusLillia.exe', 'UnityPlayer.dll', 'GameAssembly.dll',
                 'NoviceSuccubusLillia_Data/globalgamemanagers',
                 'NoviceSuccubusLillia_Data/il2cpp_data/Metadata/global-metadata.dat')
        for name in (*names, 'SaveData/progress.dat', 'BepInEx/plugins/original.dll',
                     'BepInEx/cache/stale.dat', 'dotnet/original-only.dll'):
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(name.encode())
        (self.data / 'globalgamemanagers').write_bytes(self.raw)
        (self.data / 'app.info').write_text('PaconeSoft\nNovice Succubus Lillia\n', encoding='utf-8')
        runtime = ('BepInEx/core/BepInEx.Unity.IL2CPP.dll', 'BepInEx/core/BepInEx.Core.dll',
                   'BepInEx/interop/Unity.InputSystem.dll', 'BepInEx/interop/Il2Cppmscorlib.dll',
                   'BepInEx/unity-libs/UnityEngine.CoreModule.dll',
                   'dotnet/coreclr.dll', 'dotnet/System.Private.CoreLib.dll')
        for name in ('winhttp.dll', 'GameQaBridge.dll', 'GameQaInputSystem.dll', *runtime):
            path = self.artifacts / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(name.encode())
        digests = {name: admission.file_digest(self.source / name) for name in names}
        runtime_fingerprints = {folder: admission.fingerprint(unity.manifest(self.artifacts / folder))
                                for folder in ('BepInEx/core', 'BepInEx/interop', 'BepInEx/unity-libs', 'dotnet')}
        for patch in (
            mock.patch.object(unity, 'IL2CPP_DIGESTS', digests, create=True),
            mock.patch.object(unity, 'IL2CPP_RUNTIME_FINGERPRINTS', runtime_fingerprints, create=True),
            mock.patch.object(unity, 'IL2CPP_LOADER_DIGEST', admission.file_digest(self.artifacts / 'winhttp.dll'), create=True),
            mock.patch.object(unity, 'local_low', return_value=self.root / 'LocalLow'),
            mock.patch.object(unity, 'registry_snapshot', return_value={})):
            patch.start()
            self.addCleanup(patch.stop)

    def plan(self):
        return admission.make_plan(self.source, self.root / 'session', artifacts=self.artifacts)

    def prepare(self):
        settings = SimpleNamespace(type=SimpleNamespace(name='PlayerSettings'), raw=self.raw)
        settings.get_raw_data = lambda: settings.raw
        settings.set_raw_data = lambda raw: setattr(settings, 'raw', raw)
        environment = SimpleNamespace(objects=[settings], file=SimpleNamespace(save=lambda: settings.raw))
        session = UnitySession(self.plan())
        with mock.patch('UnityPy.load', return_value=environment):
            session.prepare()
        self.addCleanup(session.stop)
        return session

    def test_fixed_il2cpp_plan_is_read_only_and_uses_separate_loader(self):
        before = unity.manifest(self.source)
        checked = admission.inspect(self.source, artifacts=self.artifacts)
        self.assertEqual(checked['launch_readiness'], 'ready')
        self.assertEqual(checked['profile_id'], 'unity-il2cpp-input-system-lillia-fixed')
        plan = self.plan()
        self.assertEqual(plan['backend'], 'unity_il2cpp_input_system')
        self.assertIn('dotnet/coreclr.dll', plan['artifact_digests'])
        self.assertIn('BepInEx/interop/Unity.InputSystem.dll', plan['artifact_digests'])
        self.assertEqual(admission.revalidate_plan(plan), plan)
        self.assertEqual(before, unity.manifest(self.source))
        self.assertFalse((self.root / 'session').exists())

    def test_missing_cached_dependencies_or_input_plugin_block(self):
        for name in ('BepInEx/interop/Unity.InputSystem.dll', 'dotnet/coreclr.dll', 'GameQaInputSystem.dll'):
            with self.subTest(name=name):
                path = self.artifacts / name
                body = path.read_bytes()
                path.unlink()
                checked = admission.inspect(self.source, artifacts=self.artifacts)
                self.assertEqual(checked['launch_readiness'], 'blocked')
                self.assertTrue(any(row['code'] == 'missing_artifact' for row in checked['blockers']))
                path.write_bytes(body)

    def test_changed_native_assembly_or_metadata_blocks(self):
        for name in ('GameAssembly.dll', 'NoviceSuccubusLillia_Data/il2cpp_data/Metadata/global-metadata.dat'):
            with self.subTest(name=name):
                path = self.source / name
                body = path.read_bytes()
                path.write_bytes(b'unknown build')
                checked = admission.inspect(self.source, artifacts=self.artifacts)
                self.assertEqual(checked['launch_readiness'], 'blocked')
                self.assertTrue(any(row['code'] == 'source_not_ready' for row in checked['blockers']))
                path.write_bytes(body)

    def test_changed_cached_runtime_invalidates_plan_before_copy(self):
        plan = self.plan()
        (self.artifacts / 'dotnet/coreclr.dll').write_bytes(b'changed runtime')
        with self.assertRaises(ValueError):
            UnitySession(plan).prepare()
        self.assertFalse((self.root / 'session').exists())

    def test_prepare_preserves_identity_lengths_and_only_uses_approved_offline_runtime(self):
        before = unity.manifest(self.source)
        session = self.prepare()
        runtime = session.root / 'runtime'
        company, product = (runtime / 'NoviceSuccubusLillia_Data/app.info').read_text(encoding='utf-8').splitlines()
        self.assertEqual((len(company), len(product)), (10, 22))
        self.assertTrue(company.startswith('QA') and product.startswith('QA'))
        changed = (runtime / 'NoviceSuccubusLillia_Data/globalgamemanagers').read_bytes()
        self.assertNotIn(b'PaconeSoft', changed)
        self.assertNotIn(b'Novice Succubus Lillia', changed)
        self.assertEqual(len(changed), len(self.raw))
        self.assertIn(struct.pack('<I', 10) + company.encode(), changed)
        for name in ('BepInEx/plugins/original.dll', 'BepInEx/cache/stale.dat',
                     'dotnet/original-only.dll', 'SaveData/progress.dat'):
            self.assertFalse((runtime / name).exists(), name)
        for name in ('BepInEx/interop/Unity.InputSystem.dll', 'dotnet/coreclr.dll',
                     'BepInEx/plugins/GameQaKit/GameQaBridge.dll',
                     'BepInEx/plugins/GameQaKit/GameQaInputSystem.dll'):
            self.assertTrue((runtime / name).is_file(), name)
        loader = configparser.ConfigParser()
        loader.read(runtime / 'doorstop_config.ini', encoding='utf-8')
        self.assertEqual(loader['General']['target_assembly'], 'BepInEx\\core\\BepInEx.Unity.IL2CPP.dll')
        self.assertEqual(loader['Il2Cpp']['coreclr_path'], 'dotnet\\coreclr.dll')
        config = configparser.ConfigParser()
        config.read(runtime / 'BepInEx/config/BepInEx.cfg', encoding='utf-8')
        self.assertFalse(config.getboolean('IL2CPP', 'UpdateInteropAssemblies'))
        self.assertFalse(config.getboolean('Logging.Console', 'Enabled'))
        self.assertEqual(before, unity.manifest(self.source))
        self.assertEqual(session.stop()['state'], 'stopped')
        self.assertFalse(runtime.exists())

    def test_ambiguous_private_identity_aborts(self):
        self.raw += struct.pack('<I', 10) + b'PaconeSoft'
        with self.assertRaisesRegex(ValueError, 'private_identity_marker_mismatch'):
            self.prepare()
        self.assertFalse((self.root / 'session/bridge').exists())

    def test_start_binds_il2cpp_identity_and_normal_input_backend(self):
        session = self.prepare()
        with mock.patch.object(session, 'connect_persistent'), \
             mock.patch('plugins.unity.session.DesktopProcess') as host, \
             mock.patch('plugins.unity.session.BridgeClient'), \
             mock.patch.object(session, 'wait_ready', return_value={'state': 'ready_paused'}):
            host.return_value.pid = 0
            try:
                self.assertEqual(session.start()['state'], 'ready_paused')
                connection = read_json(session.root / 'bridge/connection.json')
                self.assertEqual(connection['engine'], 'unity_il2cpp')
                args, kwargs = host.call_args
                self.assertEqual(Path(args[0][0]).name, 'NoviceSuccubusLillia.exe')
                self.assertEqual(kwargs['env']['GAMEQAKIT_INPUT_BACKEND'], 'input_system')
                self.assertFalse((session.root / 'bridge/request.ini').exists())
            finally:
                session.host = session.bridge = None

    def test_worker_uses_common_unity_lifecycle(self):
        self.assertIsInstance(game_worker.session_for(self.plan()), UnitySession)


if __name__ == '__main__':
    unittest.main()
