"""固定 Wolf 官方范例的受管准备、初始化与数据边界。"""
import hashlib
import itertools
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

from gameqakit import admission
from plugins.wolf import prepare as recipe


class WolfAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source, self.artifacts = self.root / 'source', self.root / 'artifacts'
        (self.source / 'Data/BasicData').mkdir(parents=True)
        (self.source / 'Data/BasicData/Game.dat').write_bytes(b'registered data')
        data = bytearray(128)
        data[:2] = b'MZ'
        struct.pack_into('<I', data, 0x3c, 64)
        data[64:68] = b'PE\0\0'
        struct.pack_into('<H', data, 68, 0x14c)
        (self.source / 'Game.exe').write_bytes(data)
        (self.source / 'Game.ini').write_bytes(b'[Game]\nTitle=sample\n')
        (self.source / 'Save').mkdir()
        (self.source / 'Save/Save09.sav').write_bytes(b'original manual progress')
        self.artifacts.mkdir()
        for name in ('GameQaWolf.dll', 'WolfInjector.exe'):
            (self.artifacts / name).write_bytes(b'local selected artifact')
        digest = hashlib.sha256(data).hexdigest()
        for patch in (mock.patch.object(recipe, 'SUPPORTED_SHA256', digest),
                      mock.patch.object(recipe, 'SUPPORTED_IMAGES', {digest})):
            patch.start()
            self.addCleanup(patch.stop)
        if hasattr(recipe, 'SOURCE_FINGERPRINT'):
            patch = mock.patch.object(recipe, 'SOURCE_FINGERPRINT', admission.fingerprint(recipe.source_manifest(self.source)))
            patch.start()
            self.addCleanup(patch.stop)

    def plan(self):
        result = admission.inspect(self.source, artifacts=self.artifacts)
        self.assertEqual(result['launch_readiness'], 'ready', result)
        return admission.make_plan(self.source, self.root / 'session', artifacts=self.artifacts)

    def prepared(self):
        from plugins.wolf.session import WolfSession
        session = WolfSession(self.plan())
        session.prepare()
        return session

    def test_inspection_plan_and_factory_are_read_only_and_explicit(self):
        from gameqakit.game_worker import session_for
        before = sorted(str(p) for p in self.root.rglob('*'))
        plan = self.plan()
        self.assertEqual(plan['backend'], 'wolf')
        self.assertEqual(plan['desktop'], 'private')
        self.assertIn('旧扩展点', '；'.join(plan['changes']))
        self.assertIn('120', '；'.join(plan['changes']))
        self.assertEqual(admission.revalidate_plan(plan), plan)
        self.assertEqual(type(session_for(plan)).__name__, 'WolfSession')
        self.assertEqual(before, sorted(str(p) for p in self.root.rglob('*')))

    def test_unknown_resources_artifacts_and_changed_confirmation_are_rejected(self):
        plan = self.plan()
        resource = self.source / 'Data/BasicData/Game.dat'
        resource.write_bytes(b'changed game')
        self.assertEqual(admission.inspect(self.source, artifacts=self.artifacts)['launch_readiness'], 'blocked')
        from plugins.wolf.session import WolfSession
        with self.assertRaises(ValueError):
            WolfSession(plan).prepare()
        self.assertFalse(Path(plan['destination']).exists())
        resource.write_bytes(b'registered data')
        (self.artifacts / 'WolfInjector.exe').unlink()
        self.assertEqual(admission.inspect(self.source, artifacts=self.artifacts)['launch_readiness'], 'blocked')

    def test_copy_preserves_source_excludes_manual_saves_and_retains_new_data(self):
        session = self.prepared()
        self.assertFalse((session.root / 'runtime/Save').exists())
        save = session.root / 'runtime/Save/new.sav'
        save.parent.mkdir()
        save.write_bytes(b'new private progress')
        result = session.stop()
        self.assertEqual(result['state'], 'stopped')
        self.assertTrue(result['source_unchanged'])
        self.assertTrue(result['artifact_unchanged'])
        self.assertTrue(result['runtime_retained'])
        self.assertTrue(save.is_file())
        self.assertEqual((self.source / 'Save/Save09.sav').read_bytes(), b'original manual progress')

    def test_cancel_runtime_and_artifact_changes_never_create_target(self):
        session = self.prepared()
        with mock.patch('plugins.wolf.session.DesktopProcess') as factory:
            with self.assertRaises(InterruptedError):
                session.start(lambda: True)
            (session.root / 'runtime/Game.exe').write_bytes(b'changed')
            with self.assertRaisesRegex(RuntimeError, 'runtime_changed'):
                session.start()
            factory.assert_not_called()

    def test_start_uses_explicit_policy_and_initializer_then_requires_real_ready_image(self):
        from PIL import Image
        from gameqakit.sessions import read_json
        session = self.prepared()
        bridge, host = mock.Mock(), mock.MagicMock()
        host.pid = 123456789
        host.desktop_name = 'gameqakit-fixture'
        host.alive.return_value = True
        initialized = []
        def state():
            return {'connected': True, 'input_ready': True, 'paused': True, 'held': [],
                    'frame': 1 if initialized else 0, 'at_frame_boundary': True,
                    'observation_seq': 1 if initialized else 0, 'session_id': 'fixture'}
        bridge.status.side_effect = state
        def startup(process, client):
            self.assertIs(process, host)
            self.assertIs(client, bridge)
            image = session.root / 'bridge/screenshots/startup.png'
            image.parent.mkdir(parents=True)
            Image.new('RGB', (32, 24), 'green').save(image)
            initialized.append(True)
            return {'ok': True, 'frame': 1, 'screenshot': str(image)}
        clock = itertools.count(0, .1)
        with mock.patch('plugins.wolf.session.DesktopProcess', return_value=host) as factory, \
                mock.patch('plugins.wolf.session.BridgeClient', return_value=bridge), \
                mock.patch('plugins.wolf.session.subprocess.run', return_value=mock.Mock(returncode=0, stdout='', stderr='')), \
                mock.patch('plugins.wolf.session.read_text', return_value='ready'), \
                mock.patch('plugins.wolf.session.time.monotonic', side_effect=lambda: next(clock)), \
                mock.patch('plugins.wolf.session.time.sleep'), \
                mock.patch('plugins.wolf.session.initialize', side_effect=startup):
            result = session.start()
        self.assertEqual(result['state'], 'ready_paused')
        self.assertEqual(result['frame'], 1)
        self.assertTrue(factory.call_args.kwargs['disable_extension_points'])
        self.assertEqual(read_json(session.root / 'bridge/connection.json')['wolf_initialize'], '1')
        bridge.resume.assert_not_called()
        bridge.action.assert_not_called()

    def test_disconnect_does_not_count_as_confirmed_release(self):
        session = self.prepared()
        session.bridge = mock.Mock()
        session.bridge.status.return_value = {'connected': False, 'error': 'heartbeat_expired'}
        result = session.stop()
        self.assertFalse(result['release_confirmed'])
        self.assertEqual(result['state'], 'cleanup_failed')
        self.assertTrue(result['runtime_retained'])

    def test_unchanged_prepared_copy_can_be_reclaimed_without_running(self):
        session = self.prepared()
        result = session.stop()
        self.assertEqual(result['state'], 'stopped')
        self.assertFalse(result['runtime_retained'])

    def test_destination_inside_artifact_tree_and_tampered_summary_are_rejected(self):
        plan = self.plan()
        with self.assertRaisesRegex(ValueError, 'overlapping_paths'):
            admission.make_plan(self.source, self.artifacts / 'session', artifacts=self.artifacts)
        from plugins.wolf.session import WolfSession
        with self.assertRaisesRegex(ValueError, 'plan_changed'):
            WolfSession({**plan, 'changes': ['omit security policy']}).prepare()


if __name__ == '__main__':
    unittest.main()
