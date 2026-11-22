"""固定商业准备的文件边界；测试清单仅在测试进程内替换为自写内容。"""
import hashlib
import importlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class CommercialPreparationTests(unittest.TestCase):
    def setUp(self):
        self.module_path = Path(__file__).resolve().parents[1] / 'plugins/kirikiri/prepare.py'
        self.assertTrue(self.module_path.is_file(), '固定商业准备器尚未实现')
        self.prepare = importlib.import_module('plugins.kirikiri.prepare')
        test_root = Path('E:/DevData/gameqa-kirikiri/test-artifacts')
        test_root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=test_root)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        self.run = self.root / 'run'
        contents = {'runtime.exe': b'authored runtime', 'data.xp3': b'authored archive', 'wuvorbis.dll': b'authored audio'}
        self.inputs = {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()}
        for name, data in contents.items():
            (self.source / name).write_bytes(data)
        self.addCleanup(patch.stopall)
        patch.object(self.prepare, 'INPUTS', self.inputs).start()

    def test_only_pinned_inputs_and_bridge_code_are_copied(self):
        (self.source / 'old-save.ksd').write_bytes(b'must not copy')
        result = self.prepare.prepare(self.source, self.run, 'runtime.exe')
        self.assertEqual(set(p.name for p in (self.run / 'game').iterdir()),
                         {'runtime.exe', 'data.xp3', 'wuvorbis.dll', 'AfterInit2.tjs', 'KagBridge.tjs'})
        self.assertEqual(result['session_root'], str(self.run))
        for name, digest in self.inputs.items():
            self.assertEqual(hashlib.sha256((self.run / 'game' / name).read_bytes()).hexdigest(), digest)
        self.assertTrue((self.run / 'preparation.json').is_file())
        self.assertTrue((self.source / 'old-save.ksd').is_file())

    def test_bad_hash_rejected_without_partial_session(self):
        (self.source / 'data.xp3').write_bytes(b'wrong archive')
        with self.assertRaises(ValueError):
            self.prepare.prepare(self.source, self.run, 'runtime.exe')
        self.assertFalse(self.run.exists())

    def test_existing_session_and_source_alias_are_never_overwritten(self):
        self.run.mkdir()
        marker = self.run / 'evidence.txt'
        marker.write_bytes(b'preserve')
        for destination in (self.run, self.source, self.source / 'nested'):
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                self.prepare.prepare(self.source, destination, 'runtime.exe')
        self.assertEqual(marker.read_bytes(), b'preserve')

    def test_low_space_fails_before_copy(self):
        with patch.object(self.prepare.shutil, 'disk_usage', return_value=(100, 99, 1)):
            with self.assertRaises(ValueError):
                self.prepare.prepare(self.source, self.run, 'runtime.exe')
        self.assertFalse(self.run.exists())

    def test_deployment_tampering_refuses_launch_validation(self):
        self.prepare.prepare(self.source, self.run, 'runtime.exe')
        self.prepare.validate(self.run)
        (self.run / 'game' / 'Config.tjs').write_bytes(b'unapproved')
        with self.assertRaises(ValueError):
            self.prepare.validate(self.run)

    def test_alternate_stream_network_and_device_paths_are_rejected(self):
        for destination in ('E:/session:stream', '//server/share/run', '//?/E:/run', 'relative/run'):
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                self.prepare.prepare(self.source, Path(destination), 'runtime.exe')


if __name__ == '__main__':
    unittest.main()
