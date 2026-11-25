"""VX Ace 检查点在确认、复制、启动及回收边界的真实文件行为。"""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest import mock

from gameqakit import admission, cli, mcp_server
from gameqakit.sessions import read_json
from plugins.rgss3.session import RgssSession
from tests import test_rgss_admission as fixtures


class RgssCheckpointTests(unittest.TestCase):
    def setUp(self):
        fixtures.RgssAdmissionTests.setUp(self)
        self.checkpoint = self.root / 'checkpoint'
        self.checkpoint.mkdir()
        (self.checkpoint / 'Save01.rvdata2').write_bytes(b'branch point')

    def inspect(self, **options):
        return mcp_server.t_game_inspect({'path': str(self.source),
            'artifacts': str(self.artifacts), 'checkpoint': str(self.checkpoint), **options})

    def plan(self, destination=None):
        return self.inspect(destination=str(destination or self.root / 'session'))['plan']

    def test_mcp_plan_carries_checkpoint_hash_and_inspection_does_not_write(self):
        before = self.recipe.manifest(self.root)
        result = self.inspect(destination=str(self.root / 'session'))
        self.assertIn('checkpoint', result['plan'])
        self.assertEqual(result['plan']['checkpoint'], {
            'directory': str(self.checkpoint),
            'files': {'Save01.rvdata2': hashlib.sha256(b'branch point').hexdigest()}})
        self.assertEqual(result['checkpoint'], result['plan']['checkpoint'])
        self.assertEqual(self.recipe.manifest(self.root), before)
        self.assertFalse((self.root / 'session').exists())

    def test_preparation_copies_checkpoint_before_runtime_baseline_and_preserves_inputs(self):
        (self.checkpoint / 'Save07.rvdata2').write_bytes(b'last test slot')
        original = self.recipe.manifest(self.source)
        checkpoint = self.recipe.manifest(self.checkpoint)
        session = RgssSession(self.plan())
        session.prepare()
        for name, content in [('Save01.rvdata2', b'branch point'), ('Save07.rvdata2', b'last test slot')]:
            self.assertEqual((session.root / 'runtime' / name).read_bytes(), content)
            self.assertEqual(session.baseline[name], hashlib.sha256(content).hexdigest())
        self.assertNotIn('Save09.rvdata2', session.baseline)
        self.assertEqual(read_json(session.root / 'checkpoint-before.json'), session.plan['checkpoint'])
        self.assertEqual(self.recipe.manifest(self.source), original)
        self.assertEqual(self.recipe.manifest(self.checkpoint), checkpoint)
        result = session.stop()
        self.assertEqual(result['state'], 'stopped')
        self.assertTrue(result['checkpoint_unchanged'])
        self.assertFalse(result['runtime_retained'])
        self.assertEqual(self.recipe.manifest(self.checkpoint), checkpoint)

    def test_invalid_checkpoint_is_blocked_without_creating_session(self):
        for name in ['Save00.rvdata2', 'Save08.rvdata2', 'Save09.rvdata2', 'Save10.rvdata2',
                     'Save1.rvdata2', 'Game.ini', 'extra.rb']:
            with self.subTest(name=name):
                entry = self.checkpoint / name
                entry.write_bytes(b'rejected')
                result = self.inspect(destination=str(self.root / 'session'))
                self.assertEqual(result['launch_readiness'], 'blocked')
                self.assertNotIn('plan', result)
                self.assertEqual(result['blockers'][-1]['field'], 'checkpoint')
                entry.unlink()
        self.assertFalse((self.root / 'session').exists())

    def test_empty_missing_and_nested_checkpoint_are_rejected(self):
        (self.checkpoint / 'Save01.rvdata2').unlink()
        self.assertEqual(self.inspect()['launch_readiness'], 'blocked')
        self.checkpoint.rmdir()
        self.assertEqual(self.inspect()['launch_readiness'], 'blocked')
        (self.checkpoint / 'Save01.rvdata2').mkdir(parents=True)
        self.assertEqual(self.inspect()['launch_readiness'], 'blocked')

    def test_checkpoint_change_invalidates_confirmation_before_copy(self):
        for operation in ('modify', 'add', 'remove'):
            with self.subTest(operation=operation):
                save = self.checkpoint / 'Save01.rvdata2'
                save.write_bytes(b'branch point')
                plan = self.plan()
                if operation == 'modify':
                    save.write_bytes(b'changed point')
                elif operation == 'add':
                    (self.checkpoint / 'Save02.rvdata2').write_bytes(b'added')
                else:
                    save.unlink()
                with self.assertRaises(ValueError):
                    RgssSession(plan).prepare()
                self.assertFalse(Path(plan['destination']).exists())
                extra = self.checkpoint / 'Save02.rvdata2'
                if extra.exists():
                    extra.unlink()

    def test_checkpoint_metadata_cannot_be_forged_or_omitted_from_confirmed_plan(self):
        plan = self.plan()
        changed = {**plan, 'checkpoint': {**plan['checkpoint'], 'files': {'Save09.rvdata2': '0' * 64}}}
        with self.assertRaises(ValueError):
            RgssSession(changed).prepare()
        changed = dict(plan)
        changed.pop('checkpoint')
        with self.assertRaises(ValueError):
            RgssSession(changed).prepare()
        self.assertFalse(Path(plan['destination']).exists())

    def test_checkpoint_and_session_paths_must_not_overlap(self):
        for destination in (self.checkpoint, self.checkpoint / 'child', self.root):
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                self.plan(destination)
        for directory in (self.source, self.artifacts):
            result = self.inspect(checkpoint=str(directory))
            self.assertEqual(result['launch_readiness'], 'blocked')

    @unittest.skipUnless(os.name == 'nt', 'Windows 目录联接边界')
    def test_checkpoint_directory_junction_is_rejected(self):
        link = self.root / 'checkpoint-link'
        subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(self.checkpoint)],
                       check=True, capture_output=True)
        self.addCleanup(lambda: link.rmdir() if link.is_junction() else None)
        result = self.inspect(checkpoint=str(link))
        self.assertEqual(result['launch_readiness'], 'blocked')

    def test_checkpoint_copy_and_input_changes_are_detected_before_baseline(self):
        copy = shutil.copy2
        for changed in ('input', 'copy'):
            with self.subTest(changed=changed):
                (self.checkpoint / 'Save01.rvdata2').write_bytes(b'branch point')
                plan = self.plan(self.root / ('session-' + changed))
                def mutate(source, destination, *args, **kwargs):
                    result = copy(source, destination, *args, **kwargs)
                    if Path(source).parent == self.checkpoint:
                        target = Path(source) if changed == 'input' else Path(destination)
                        target.write_bytes(b'changed during copy')
                    return result
                with mock.patch('plugins.rgss3.prepare.shutil.copy2', side_effect=mutate):
                    with self.assertRaisesRegex(ValueError, 'checkpoint_changed|checkpoint_copy_changed'):
                        RgssSession(plan).prepare()
                self.assertFalse((Path(plan['destination']) / 'runtime-before.json').exists())

    def test_baseline_cannot_accept_checkpoint_mutation_after_copy_check(self):
        plan = self.plan()
        runtime = Path(plan['destination']) / 'runtime'
        manifest = self.recipe.manifest
        def mutate(directory):
            save = runtime / 'Save01.rvdata2'
            if Path(directory) == runtime and save.exists():
                save.write_bytes(b'changed before sealing')
            return manifest(directory)
        with mock.patch.object(self.recipe, 'manifest', side_effect=mutate):
            with self.assertRaisesRegex(ValueError, 'checkpoint_copy_changed'):
                RgssSession(plan).prepare()
        self.assertFalse((Path(plan['destination']) / 'runtime-before.json').exists())

    def test_checkpoint_copy_failure_retains_partial_runtime_and_input_evidence(self):
        session = RgssSession(self.plan())
        copy = shutil.copy2
        def fail(source, destination, *args, **kwargs):
            if Path(source).parent == self.checkpoint:
                raise OSError('simulated checkpoint copy failure')
            return copy(source, destination, *args, **kwargs)
        with mock.patch('plugins.rgss3.prepare.shutil.copy2', side_effect=fail):
            with self.assertRaisesRegex(OSError, 'copy failure'):
                session.prepare()
        self.assertFalse((session.root / 'runtime-before.json').exists())
        self.assertTrue((session.root / 'checkpoint-before.json').exists())
        self.assertTrue(session.stop()['runtime_retained'])
        self.assertEqual((self.checkpoint / 'Save01.rvdata2').read_bytes(), b'branch point')

    def test_cancelled_checkpoint_start_does_not_create_target(self):
        session = RgssSession(self.plan())
        session.prepare()
        with mock.patch('plugins.rgss3.session.DesktopProcess') as target:
            with self.assertRaises(InterruptedError):
                session.start(cancelled=lambda: True)
        target.assert_not_called()
        self.assertTrue(session.stop()['checkpoint_unchanged'])

    def test_modified_seed_save_is_retained_and_not_written_back(self):
        session = RgssSession(self.plan())
        session.prepare()
        save = session.root / 'runtime/Save01.rvdata2'
        save.write_bytes(b'new branch progress')
        with self.assertRaisesRegex(RuntimeError, 'runtime_changed_before_start'):
            session.start()
        result = session.stop()
        self.assertEqual(result['state'], 'stopped')
        self.assertTrue(result['runtime_retained'])
        self.assertEqual(save.read_bytes(), b'new branch progress')
        self.assertEqual((self.checkpoint / 'Save01.rvdata2').read_bytes(), b'branch point')
        self.assertEqual((self.source / 'Save09.rvdata2').read_bytes(), b'original manual slot')

    def test_changed_checkpoint_at_cleanup_is_reported_and_runtime_retained(self):
        session = RgssSession(self.plan())
        session.prepare()
        (self.checkpoint / 'Save01.rvdata2').write_bytes(b'externally changed')
        result = session.stop()
        self.assertEqual(result['state'], 'cleanup_failed')
        self.assertFalse(result['checkpoint_unchanged'])
        self.assertTrue(result['runtime_retained'])

    def test_cli_inspect_carries_checkpoint_into_reviewable_plan(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = cli.main(['game', 'inspect', str(self.source), '--artifacts', str(self.artifacts),
                             '--checkpoint', str(self.checkpoint), '--destination', str(self.root / 'session')])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())['checkpoint']['files'],
                         {'Save01.rvdata2': hashlib.sha256(b'branch point').hexdigest()})

    def test_mcp_advertises_checkpoint_and_requires_plan_confirmation(self):
        self.assertEqual(mcp_server.TOOLS['game_inspect'][1]['properties']['checkpoint']['type'], 'string')
        with self.assertRaises(ValueError):
            mcp_server.t_game_prepare({'plan': self.plan(), 'request_id': 'seed-without-confirm'})
        self.assertFalse((self.root / 'session').exists())

    def test_checkpoint_is_refused_for_unrecognized_engine(self):
        unknown = self.root / 'unknown'
        unknown.mkdir()
        result = self.inspect(path=str(unknown))
        self.assertEqual(result['launch_readiness'], 'blocked')
        self.assertTrue(any(row['field'] == 'checkpoint' for row in result['blockers']))


if __name__ == '__main__':
    unittest.main()
