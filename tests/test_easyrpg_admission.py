import os
from pathlib import Path
import shutil
import tempfile
import unittest

from gameqakit import admission

ROOT = Path(__file__).resolve().parents[1]
COMPONENTS = os.environ.get('GAMEQAKIT_TEST_EASYRPG_COMPONENTS')


class EasyRpgAdmissionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='gameqa-easyrpg-admission-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / 'sample'
        shutil.copytree(ROOT / 'plugins/easyrpg/samples/rm2k', self.source)

    def test_registered_fixture_is_recognized_but_missing_runtime_blocks(self):
        result = admission.inspect(self.source)
        self.assertEqual(result['engine'], 'rpgmaker_2000_2003')
        self.assertEqual(result['launch_readiness'], 'blocked')
        self.assertEqual([b['field'] for b in result['blockers']], ['artifacts'])
        self.assertFalse((self.root / 'session').exists())

    def test_modified_fixture_and_progress_are_rejected(self):
        from plugins.easyrpg.prepare import source_digests
        original = source_digests(self.source)
        (self.source / 'Save01.lsd').write_bytes(b'progress')
        with self.assertRaises(ValueError):
            source_digests(self.source)
        (self.source / 'Save01.lsd').unlink()
        (self.source / 'Map0001.lmu').write_bytes(b'changed')
        with self.assertRaises(ValueError):
            source_digests(self.source)
        self.assertEqual(len(original), 6)

    @unittest.skipUnless(COMPONENTS, 'Requires registered source-built EasyRPG runtime')
    def test_plan_runtime_hashes_and_revalidation(self):
        plan = admission.make_plan(self.source, self.root / 'session', artifacts=COMPONENTS)
        self.assertEqual(plan['backend'], 'easyrpg_authored')
        self.assertEqual(admission.revalidate_plan(plan), plan)
        components = self.root / 'artifacts'
        components.mkdir()
        shutil.copyfile(Path(COMPONENTS) / 'GameQaEasyRpg.exe', components / 'GameQaEasyRpg.exe')
        with (components / 'GameQaEasyRpg.exe').open('ab') as stream:
            stream.write(b'changed')
        self.assertEqual(admission.inspect(self.source, artifacts=components)['launch_readiness'], 'blocked')
