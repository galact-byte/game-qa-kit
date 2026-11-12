"""固定 VX Ace SDK 受管准备、保存保护与启动边界。"""
import importlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zlib

from rubymarshal.writer import writes
from gameqakit import admission


class RgssAdmissionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source, self.artifacts = self.root / 'source', self.root / 'artifacts'
        (self.source / 'Data').mkdir(parents=True)
        (self.source / 'System').mkdir()
        self.artifacts.mkdir()
        (self.source / 'Game.exe').write_bytes(b'fixed SDK executable')
        (self.source / 'Game.ini').write_bytes(b'[Game]\nRTP=RPGVXAce\nLibrary=System\\RGSS300.dll\nScripts=Data\\Scripts.rvdata2\nTitle=Project1\n')
        self.scripts = [[1, b'SceneManager', zlib.compress(b'module SceneManager; def self.run; end; end')],
                        [2, b'Main', zlib.compress(b'rgss_main { SceneManager.run }')]]
        (self.source / 'Data/Scripts.rvdata2').write_bytes(writes(self.scripts))
        (self.source / 'System/RGSS300.dll').write_bytes(b'original SDK library')
        (self.source / 'Save09.rvdata2').write_bytes(b'original manual slot')
        (self.artifacts / 'RGSS301.dll').write_bytes(b'registered runtime')
        self.recipe = None
        if importlib.util.find_spec('plugins.rgss3.prepare'):
            self.recipe = importlib.import_module('plugins.rgss3.prepare')
            for patch in (mock.patch.object(self.recipe, 'SOURCE_FINGERPRINT', admission.fingerprint(
                    self.recipe.source_manifest(self.source))),
                    mock.patch.object(self.recipe, 'LIBRARY_SHA256', admission.file_digest(self.artifacts / 'RGSS301.dll'))):
                patch.start()
                self.addCleanup(patch.stop)

    def plan(self):
        self.assertEqual(admission.inspect(self.source, artifacts=self.artifacts)['launch_readiness'], 'ready')
        return admission.make_plan(self.source, self.root / 'session', artifacts=self.artifacts)

    def prepared(self):
        plan = self.plan()
        from plugins.rgss3.session import RgssSession
        session = RgssSession(plan)
        session.prepare()
        return session

    def test_plan_is_read_only_revalidates_and_dispatches(self):
        from gameqakit.game_worker import session_for
        before = list(self.root.rglob('*'))
        plan = self.plan()
        self.assertEqual(plan['backend'], 'rgss3')
        self.assertEqual(plan['desktop'], 'private')
        self.assertIn('不带原存档', '；'.join(plan['changes']))
        self.assertEqual(admission.revalidate_plan(plan), plan)
        self.assertEqual(type(session_for(plan)).__name__, 'RgssSession')
        self.assertEqual(before, list(self.root.rglob('*')))

    def test_unknown_project_runtime_and_added_script_are_rejected(self):
        self.plan()
        for item in (self.source / 'Game.exe', self.artifacts / 'RGSS301.dll', self.source / 'Data/Scripts.rvdata2'):
            original = item.read_bytes()
            item.write_bytes(b'unregistered content')
            self.assertEqual(admission.inspect(self.source, artifacts=self.artifacts)['launch_readiness'], 'blocked')
            item.write_bytes(original)
        extra = self.source / 'extra.rb'
        extra.write_bytes(b'unknown plugin')
        self.assertEqual(admission.inspect(self.source, artifacts=self.artifacts)['launch_readiness'], 'blocked')

    def test_overlapping_runtime_source_and_changed_plan_are_rejected_before_copy(self):
        plan = self.plan()
        with self.assertRaisesRegex(ValueError, 'overlapping_paths'):
            admission.make_plan(self.source, self.artifacts / 'session', artifacts=self.artifacts)
        from plugins.rgss3.session import RgssSession
        with self.assertRaisesRegex(ValueError, 'plan_changed'):
            RgssSession({**plan, 'changes': ['changed']}).prepare()
        self.assertFalse(Path(plan['destination']).exists())

    def test_preparation_only_patches_copy_and_inserts_bridge_before_real_main(self):
        session = self.prepared()
        runtime = session.root / 'runtime'
        self.assertFalse((runtime / 'Save09.rvdata2').exists())
        self.assertEqual((self.source / 'Save09.rvdata2').read_bytes(), b'original manual slot')
        self.assertIn(b'RGSS300.dll', (self.source / 'Game.ini').read_bytes())
        self.assertIn(b'RGSS301.dll', (runtime / 'Game.ini').read_bytes())
        self.assertEqual((self.source / 'Data/Scripts.rvdata2').read_bytes(), writes(self.scripts))
        scripts = self.recipe.read_scripts(runtime / 'Data/Scripts.rvdata2')
        self.assertEqual([row[1] for row in scripts], [b'SceneManager', b'GameQaBridge', b'Main'])
        self.assertEqual(scripts[0], self.scripts[0])
        self.assertEqual(scripts[-1], self.scripts[-1])
        self.assertIn(b"load 'GameQaBridge.rb'", zlib.decompress(scripts[1][2]))
        result = session.stop()
        self.assertEqual(result['state'], 'stopped')
        self.assertTrue(result['source_unchanged'])
        self.assertTrue(result['runtime_source_unchanged'])
        self.assertFalse(result['runtime_retained'])

    def test_new_save_stays_private_and_prevents_runtime_reclamation(self):
        session = self.prepared()
        save = session.root / 'runtime/Save01.rvdata2'
        save.write_bytes(b'new progress')
        result = session.stop()
        self.assertEqual(result['state'], 'stopped')
        self.assertTrue(result['runtime_retained'])
        self.assertEqual(save.read_bytes(), b'new progress')
        self.assertEqual((self.source / 'Save09.rvdata2').read_bytes(), b'original manual slot')

    def test_cancel_and_changed_runtime_never_start_target(self):
        session = self.prepared()
        with mock.patch('plugins.rgss3.session.DesktopProcess') as target:
            with self.assertRaises(InterruptedError):
                session.start(lambda: True)
            (session.root / 'runtime/Game.exe').write_bytes(b'changed')
            with self.assertRaisesRegex(RuntimeError, 'runtime_changed'):
                session.start()
            target.assert_not_called()

    def test_black_startup_frame_is_rejected_without_sending_input(self):
        from PIL import Image
        session = self.prepared()
        image = session.root / 'bridge/screenshots/startup.png'
        image.parent.mkdir(parents=True)
        Image.new('RGB', (32, 24), 'black').save(image)
        bridge, host = mock.Mock(), mock.MagicMock()
        host.pid = 123456789
        host.alive.return_value = True
        bridge.status.return_value = {'connected': True, 'input_ready': True, 'observation_seq': 1,
                                     'session_id': 'sample', 'paused': True, 'held': []}
        with mock.patch('plugins.rgss3.session.DesktopProcess', return_value=host), \
                mock.patch('plugins.rgss3.session.BridgeClient', return_value=bridge), \
                mock.patch('plugins.rgss3.session._pid_path', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'startup_image_invalid'):
                session.start()
            result = session.stop()
        self.assertTrue(result['release_confirmed'])
        host.close.assert_called_once()
        bridge.resume.assert_not_called()
        bridge.execute.assert_not_called()

    def test_disconnected_bridge_never_counts_as_release_confirmation(self):
        session = self.prepared()
        session.bridge = mock.Mock()
        session.bridge.status.return_value = {'connected': False, 'error': 'heartbeat_expired'}
        result = session.stop()
        self.assertEqual(result['state'], 'cleanup_failed')
        self.assertFalse(result['release_confirmed'])
        self.assertTrue(result['runtime_retained'])


if __name__ == '__main__':
    unittest.main()
