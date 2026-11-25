"""固定商业 RGSS300 配置复用统一准备、检查点与回收生命周期。"""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zlib

from rubymarshal.writer import writes
from gameqakit import admission
from plugins.rgss3 import prepare as recipe
from plugins.rgss3.session import RgssSession
from tests.test_rgss_archive import archive_bytes


class RgssCommercialTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source, self.artifacts = self.root / 'source', self.root / 'source/System'
        self.artifacts.mkdir(parents=True)
        (self.source / 'Game.exe').write_bytes(b'fixed commercial exe')
        (self.source / 'Game.ini').write_bytes(b'[Game]\nLibrary=System\\RGSS300.dll\nScripts=Data\\Scripts.rvdata2\n')
        (self.artifacts / 'RGSS300.dll').write_bytes(b'original commercial dll')
        self.scripts = [[1, b'Main', zlib.compress(b'# decoy Main')],
                        [2, b'actual entry', zlib.compress(b'rgss_main { SceneManager.run }')]]
        self.body = writes(self.scripts)
        (self.source / 'Game.rgss3a').write_bytes(archive_bytes([
            ('Data/Scripts.rvdata2', self.body), ('Data/System.rvdata2', b'system')]))
        (self.source / 'Save01.rvdata2').write_bytes(b'original progress')
        (self.source / 'Save09.rvdata2').write_bytes(b'manual progress')
        (self.source / 'system_data.rvdata2').write_bytes(b'original global progress')
        self.profile = {'profile_id': 'rpgmaker-vxace-commercial-rgss300-fixed',
                        'source_fingerprint': admission.fingerprint(recipe.source_manifest(self.source)),
                        'library_sha256': admission.file_digest(self.artifacts / 'RGSS300.dll'),
                        'scripts_sha256': hashlib.sha256(self.body).hexdigest(),
                        'main_index': 1, 'script_count': 2, 'archive_entries': 2}
        patch = mock.patch.object(recipe, 'COMMERCIAL_PROFILE', self.profile, create=True)
        patch.start()
        self.addCleanup(patch.stop)

    def plan(self):
        return admission.make_plan(self.source, self.root / 'session', artifacts=self.artifacts)

    def test_plan_reports_original_runtime_and_archive_without_writes(self):
        before = recipe.manifest(self.root)
        result = admission.inspect(self.source, artifacts=self.artifacts)
        self.assertEqual(result['launch_readiness'], 'ready', result)
        self.assertEqual(result['profile_id'], self.profile['profile_id'])
        plan = self.plan()
        self.assertIn('RGSS300', '；'.join(plan['changes']))
        self.assertIn('归档', '；'.join(plan['changes']))
        self.assertEqual(plan['profile_id'], self.profile['profile_id'])
        self.assertEqual(admission.revalidate_plan(plan), plan)
        self.assertEqual(recipe.manifest(self.root), before)

    def test_preparation_keeps_dll_and_ini_and_injects_before_actual_entry(self):
        before = recipe.manifest(self.source)
        session = RgssSession(self.plan())
        session.prepare()
        runtime = session.root / 'runtime'
        for name in ('Game.ini', 'System/RGSS300.dll'):
            self.assertEqual((runtime / name).read_bytes(), (self.source / name).read_bytes())
        self.assertFalse((runtime / 'Game.rgss3a').exists())
        self.assertFalse((runtime / 'System/RGSS301.dll').exists())
        for name in ('Save01.rvdata2', 'Save09.rvdata2', 'system_data.rvdata2'):
            self.assertFalse((runtime / name).exists())
        scripts = recipe.read_scripts(runtime / 'Data/Scripts.rvdata2')
        self.assertEqual([row[1] for row in scripts], [b'Main', b'GameQaBridge', b'actual entry'])
        self.assertEqual(recipe.manifest(self.source), before)
        self.assertEqual(session.stop()['state'], 'stopped')
        self.assertFalse(runtime.exists())

    def test_only_explicit_checkpoint_is_imported_and_changed_progress_is_retained(self):
        checkpoint = self.root / 'checkpoint'
        checkpoint.mkdir()
        (checkpoint / 'Save01.rvdata2').write_bytes(b'branch seed')
        plan = admission.make_plan(self.source, self.root / 'session', artifacts=self.artifacts, checkpoint=checkpoint)
        self.assertIn('rgss300', plan['data_policy'])
        session = RgssSession(plan)
        session.prepare()
        save = session.root / 'runtime/Save01.rvdata2'
        self.assertEqual(save.read_bytes(), b'branch seed')
        self.assertFalse((save.parent / 'Save09.rvdata2').exists())
        save.write_bytes(b'new branch')
        result = session.stop()
        self.assertEqual(result['state'], 'stopped')
        self.assertTrue(result['runtime_retained'])
        self.assertTrue(result['checkpoint_unchanged'])
        self.assertEqual((self.source / 'Save01.rvdata2').read_bytes(), b'original progress')

    def test_changed_fixed_inputs_are_refused_before_creating_session(self):
        for name in ('Game.exe', 'Game.ini', 'Game.rgss3a', 'System/RGSS300.dll'):
            path = self.source / name
            original = path.read_bytes()
            plan = self.plan()
            path.write_bytes(b'changed')
            self.assertNotEqual(admission.inspect(self.source, artifacts=self.artifacts)['launch_readiness'], 'ready')
            with self.assertRaises(ValueError):
                RgssSession(plan).prepare()
            self.assertFalse(Path(plan['destination']).exists())
            path.write_bytes(original)

    def test_adding_unregistered_code_and_wrong_selected_dll_is_refused(self):
        added = self.source / 'extra.rb'
        added.write_bytes(b'unknown code')
        self.assertEqual(admission.inspect(self.source, artifacts=self.artifacts)['launch_readiness'], 'blocked')
        added.unlink()
        wrong = self.root / 'wrong'
        wrong.mkdir()
        (wrong / 'RGSS300.dll').write_bytes(b'wrong dll')
        self.assertEqual(admission.inspect(self.source, artifacts=wrong)['launch_readiness'], 'blocked')

    def test_script_digest_or_unreviewed_entry_is_rejected_and_new_runtime_removed(self):
        for key, value in [('scripts_sha256', '0' * 64), ('main_index', 0)]:
            with self.subTest(key=key):
                with mock.patch.dict(self.profile, {key: value}):
                    with self.assertRaises(ValueError):
                        RgssSession(self.plan()).prepare()
                self.assertFalse((self.root / 'session/runtime').exists())
                # 审计文件允许保留；下轮使用新建目标目录。
                if (self.root / 'session').exists():
                    import shutil
                    shutil.rmtree(self.root / 'session')

    def test_registered_loose_graphics_override_keeps_reviewed_resource(self):
        archive = self.source / 'Game.rgss3a'
        archive.write_bytes(archive_bytes([('Data/Scripts.rvdata2', self.body),
            ('Data/System.rvdata2', b'system'), ('Graphics/System/window.png', b'archived window')]))
        loose = self.source / 'Graphics/System/Window.png'
        loose.parent.mkdir(parents=True)
        loose.write_bytes(b'reviewed loose window')
        self.profile.update(source_fingerprint=admission.fingerprint(recipe.source_manifest(self.source)),
                            archive_entries=3, loose_overrides=['Graphics/System/window.png'])
        session = RgssSession(self.plan())
        session.prepare()
        self.assertEqual((session.root / 'runtime/Graphics/System/window.png').read_bytes(), loose.read_bytes())
        self.assertEqual(session.stop()['state'], 'stopped')

    def test_existing_destination_is_never_reclaimed_after_prepare_failure(self):
        plan = self.plan()
        runtime = Path(plan['destination']) / 'runtime'
        runtime.mkdir(parents=True)
        existing = runtime / 'existing'
        existing.write_bytes(b'preserve user files')
        with self.assertRaises(FileExistsError):
            recipe.prepare(plan)
        self.assertEqual(existing.read_bytes(), b'preserve user files')

    def test_failed_extraction_reclaims_only_new_runtime(self):
        session = RgssSession(self.plan())
        with mock.patch('plugins.rgss3.archive.extract', side_effect=OSError('simulated disk failure')):
            with self.assertRaisesRegex(OSError, 'disk failure'):
                session.prepare()
        self.assertFalse((session.root / 'runtime').exists())
        self.assertTrue((self.source / 'Game.rgss3a').is_file())
        self.assertTrue((session.root / 'source-before.json').is_file())


if __name__ == '__main__':
    unittest.main()
