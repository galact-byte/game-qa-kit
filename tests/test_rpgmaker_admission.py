"""固定 MV/MZ SDK 工程的受管接入与原数据保护回归。"""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from gameqakit import admission
from gameqakit.sessions import read_json, write_json
from plugins.rpgmaker_js import prepare as recipe
from plugins.rpgmaker_js.session import RpgMakerSession


class RpgMakerAdmissionTests(unittest.TestCase):
    engine = 'rpgmaker_mv'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.artifacts = self.root / 'source', self.root / 'artifacts'
        core = 'rpg_core.js' if self.engine.endswith('_mv') else 'rmmz_core.js'
        for name in ('index.html', 'js/' + core, 'js/plugins.js', 'data/System.json'):
            item = self.source / name
            item.parent.mkdir(parents=True, exist_ok=True)
            item.write_text(name, encoding='utf-8')
        write_json(self.source / 'package.json', {'name': 'original-game', 'main': 'index.html',
                                                 'window': {'width': 816, 'height': 624}})
        self.artifacts.mkdir()
        (self.artifacts / 'nw.exe').write_bytes(b'fixed runtime')
        (self.source / 'save').mkdir()
        (self.source / 'save/file9.rpgsave').write_bytes(b'original manual progress')
        self.env = mock.patch.dict(os.environ, {'LOCALAPPDATA': str(self.root / 'Local'),
                                                'APPDATA': str(self.root / 'Roaming')})
        self.env.start()
        self.addCleanup(self.env.stop)
        for patch in (mock.patch.object(recipe, 'SOURCE_FINGERPRINTS', {
                self.engine: admission.fingerprint(recipe.source_manifest(self.source))}),
                mock.patch.object(recipe, 'RUNTIME_FINGERPRINT', admission.fingerprint(recipe.manifest(self.artifacts)))):
            patch.start()
            self.addCleanup(patch.stop)
        self.plan = admission.make_plan(self.source, self.root / 'session', artifacts=self.artifacts)

    def prepared(self):
        session = RpgMakerSession(self.plan)
        session.prepare()
        return session

    def test_large_project_plan_fits_control_record_and_revalidates(self):
        from gameqakit.game_session import Manager
        digests = {f'img/asset-{index:04}.png': 'a' * 64 for index in range(1500)}
        with mock.patch.object(recipe, 'source_digests', return_value=digests):
            plan = admission.make_plan(self.source, self.root / 'large-session', artifacts=self.artifacts)
            record, content = Manager._request(plan['destination'], 'large-sdk-prepare', 'prepare', plan)
            write_json(record, content)
            self.assertEqual(read_json(record), content)
            self.assertEqual(admission.revalidate_plan(plan), plan)
            digests['img/asset-0000.png'] = 'b' * 64
            with self.assertRaisesRegex(ValueError, 'plan_changed'):
                admission.revalidate_plan(plan)

    def test_plan_is_read_only_explicit_and_dispatched(self):
        from gameqakit.game_worker import session_for
        before = list(self.root.rglob('*'))
        self.assertEqual(admission.inspect(self.source, artifacts=self.artifacts)['launch_readiness'], 'ready')
        self.assertEqual(self.plan['backend'], 'rpgmaker_js')
        self.assertEqual(self.plan['engine'], self.engine)
        self.assertEqual(self.plan['desktop'], 'private')
        self.assertIn('不带原存档', '；'.join(self.plan['changes']))
        self.assertIsInstance(session_for(self.plan), RpgMakerSession)
        self.assertEqual(admission.revalidate_plan(self.plan), self.plan)
        self.assertEqual(before, list(self.root.rglob('*')))

    def test_unknown_project_runtime_or_added_plugin_is_blocked(self):
        original = (self.source / 'index.html').read_bytes()
        (self.source / 'index.html').write_bytes(b'unknown project')
        self.assertEqual(admission.inspect(self.source, artifacts=self.artifacts)['launch_readiness'], 'blocked')
        (self.source / 'index.html').write_bytes(original)
        extra = self.source / 'js/plugins/unknown.js'
        extra.parent.mkdir()
        extra.write_bytes(b'unknown plugin')
        self.assertEqual(admission.inspect(self.source, artifacts=self.artifacts)['launch_readiness'], 'blocked')
        extra.unlink()
        (self.artifacts / 'nw.exe').write_bytes(b'unknown runtime')
        self.assertEqual(admission.inspect(self.source, artifacts=self.artifacts)['launch_readiness'], 'blocked')

    def test_missing_runtime_stays_blocked(self):
        result = admission.inspect(self.source)
        self.assertEqual(result['launch_readiness'], 'blocked')
        self.assertTrue(any(row['field'] == 'artifacts' for row in result['blockers']))

    def test_session_cannot_be_created_inside_runtime_source(self):
        with self.assertRaisesRegex(ValueError, 'overlapping_paths'):
            admission.make_plan(self.source, self.artifacts / 'session', artifacts=self.artifacts)
        self.assertFalse((self.artifacts / 'session').exists())

    def test_changed_summary_is_rejected_before_copying(self):
        plan = {**self.plan, 'changes': ['changed']}
        with self.assertRaisesRegex(ValueError, 'plan_changed'):
            RpgMakerSession(plan).prepare()
        self.assertFalse(Path(self.plan['destination']).exists())

    def test_preparation_omits_progress_and_keeps_originals(self):
        before = recipe.manifest(self.source)
        session = self.prepared()
        app = session.root / 'runtime/app'
        self.assertEqual(before, recipe.manifest(self.source))
        self.assertFalse((app / 'save').exists())
        self.assertTrue((app / 'js/plugins/GameQaBridge.js').is_file())
        self.assertIn('GameQaBridge', (app / 'js/plugins.js').read_text(encoding='utf-8'))
        package = read_json(app / 'package.json')
        self.assertNotEqual(package['name'], 'original-game')
        self.assertIs(package['single-instance'], False)
        result = session.stop()
        self.assertEqual(result['state'], 'stopped')
        self.assertTrue(result['source_unchanged'])
        self.assertTrue(result['runtime_source_unchanged'])
        self.assertTrue(result['external_unchanged'])
        self.assertFalse((session.root / 'runtime').exists())

    def test_new_save_is_retained_and_original_manual_slot_stays_unchanged(self):
        session = self.prepared()
        save = session.root / 'runtime/app/save/file1.rpgsave'
        save.parent.mkdir()
        save.write_bytes(b'new private progress')
        result = session.stop()
        self.assertEqual(result['state'], 'stopped')
        self.assertTrue(result['runtime_retained'])
        self.assertEqual(save.read_bytes(), b'new private progress')
        self.assertEqual((self.source / 'save/file9.rpgsave').read_bytes(), b'original manual progress')

    def test_changed_external_profile_is_not_discarded(self):
        session = self.prepared()
        profile = self.root / 'Local/original-game/Default'
        profile.mkdir(parents=True)
        (profile / 'changed').write_bytes(b'external change')
        result = session.stop()
        self.assertEqual(result['state'], 'cleanup_failed')
        self.assertFalse(result['external_unchanged'])
        self.assertTrue(result['runtime_retained'])

    def test_cancel_or_changed_copy_never_creates_target(self):
        session = self.prepared()
        with mock.patch('plugins.rpgmaker_js.session.DesktopProcess') as target:
            with self.assertRaises(InterruptedError):
                session.start(lambda: True)
            target.assert_not_called()
        (session.root / 'runtime/nw.exe').write_bytes(b'changed')
        with mock.patch('plugins.rpgmaker_js.session.DesktopProcess') as target:
            with self.assertRaisesRegex(RuntimeError, 'runtime_changed'):
                session.start()
            target.assert_not_called()

    def test_black_first_frame_stops_without_input(self):
        from PIL import Image
        session = self.prepared()
        image = session.root / 'bridge/screenshots/startup.png'
        image.parent.mkdir(parents=True)
        Image.new('RGB', (32, 24), 'black').save(image)
        client, host = mock.Mock(), mock.MagicMock()
        host.pid = 123456789
        host.alive.return_value = True
        client.status.return_value = {'connected': True, 'input_ready': True, 'observation_seq': 1,
                                      'session_id': 'sample', 'paused': True, 'held': []}
        with mock.patch('plugins.rpgmaker_js.session.DesktopProcess', return_value=host), \
                mock.patch('plugins.rpgmaker_js.session.BridgeClient', return_value=client), \
                mock.patch('plugins.rpgmaker_js.session._pid_path', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'startup_image_invalid'):
                session.start()
            result = session.stop()
        self.assertEqual(result['state'], 'stopped')
        self.assertTrue(result['release_confirmed'])
        host.close.assert_called_once()
        client.resume.assert_not_called()
        client.execute.assert_not_called()

    def test_ready_requires_private_save_and_browser_paths(self):
        from PIL import Image
        for field in ('runtime_data_path', 'save_directory'):
            with self.subTest(field=field):
                session = self.prepared() if not Path(self.plan['destination']).exists() else RpgMakerSession(self.plan)
                session.baseline = read_json(session.root / 'runtime-before.json')
                image = session.root / 'bridge/screenshots/startup.png'
                image.parent.mkdir(parents=True, exist_ok=True)
                Image.new('RGB', (32, 24), 'navy').save(image)
                state = {'connected': True, 'input_ready': True, 'observation_seq': 1,
                         'session_id': 'sample', 'paused': True, 'held': [],
                         'runtime_data_path': str(session.root / 'runtime-data/Default'),
                         'save_directory': str(session.root / 'runtime/app/save')}
                state[field] = str(self.root / 'external')
                client, host = mock.Mock(), mock.MagicMock()
                host.pid = 123456789
                host.alive.return_value = True
                client.status.return_value = state
                with mock.patch('plugins.rpgmaker_js.session.DesktopProcess', return_value=host), \
                        mock.patch('plugins.rpgmaker_js.session.BridgeClient', return_value=client):
                    with self.assertRaisesRegex(RuntimeError, 'unexpected_' + field):
                        session.start()
                client.resume.assert_not_called()
                client.execute.assert_not_called()
                host.close()

    def test_disconnect_cannot_count_as_release_even_after_exit(self):
        session = self.prepared()
        session.host, session.bridge = mock.Mock(), mock.Mock()
        session.host.pid = 123456789
        session.bridge.status.return_value = {'connected': False, 'error': 'heartbeat expired'}
        with mock.patch('plugins.rpgmaker_js.session._pid_path', return_value=None):
            result = session.stop()
        self.assertEqual(result['state'], 'cleanup_failed')
        self.assertIs(result['release_confirmed'], False)
        self.assertTrue(result['target_exited'])
        self.assertTrue(result['runtime_retained'])


class RpgMakerMzAdmissionTests(RpgMakerAdmissionTests):
    engine = 'rpgmaker_mz'


if __name__ == '__main__':
    unittest.main()
