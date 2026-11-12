"""原生样本准备入口的数据边界与拒绝行为。"""
import hashlib
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from plugins.native_input import prepare as native


class NativePreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        image = bytearray(128)
        image[:2] = b'MZ'
        struct.pack_into('<I', image, 0x3c, 64)
        image[64:68] = b'PE\0\0'
        struct.pack_into('<H', image, 68, 0x14c)
        (self.source / 'game.exe').write_bytes(image)
        (self.source / 'game.ini').write_bytes(b'NonActiveAnimationPlay=1\r\nNonActiveMusicPlay=0\r\n')
        for name in ('data.dts', 'runtime.rts', 'system.dat', 'environment.evs'):
            (self.source / name).write_bytes(b'fixture')
        (self.source / 'Audio').mkdir()
        (self.source / 'Audio/music.ogg').write_bytes(b'fixture')
        (self.source / 'Save').mkdir()
        (self.source / 'Save/slot9').write_bytes(b'protected')
        self.external = self.root / 'external'
        self.external.mkdir()
        self.workspace = self.root / 'batch'
        self.digest = hashlib.sha256(image).hexdigest()

    def prepare(self):
        with patch.object(native, 'SUPPORTED_SHA256', self.digest):
            return native.prepare(self.source, self.workspace, external=self.external)

    def test_copies_known_resources_without_saves_and_preserves_baselines(self):
        before = native.manifest(self.source)
        record = self.prepare()
        self.assertEqual(record['exe_sha256'], self.digest)
        self.assertFalse((self.workspace / 'runtime/Save/slot9').exists())
        self.assertTrue((self.workspace / 'runtime/Save').is_dir())
        self.assertEqual(native.manifest(self.source), before)
        self.assertEqual((self.workspace / 'runtime/game.ini').read_bytes(),
                         b'NonActiveAnimationPlay=0\r\nNonActiveMusicPlay=1\r\n')
        self.assertTrue((self.workspace / 'source-before.json').is_file())
        self.assertTrue((self.workspace / 'external-before.json').is_file())
        self.assertTrue((self.workspace / '测试数据记录.md').is_file())

    def test_unknown_executable_rejected_before_workspace_creation(self):
        with self.assertRaisesRegex(ValueError, 'unsupported_native_image'):
            native.prepare(self.source, self.workspace, external=self.external)
        self.assertFalse(self.workspace.exists())

    def test_missing_data_mode_and_unknown_ini_rejected_before_copy(self):
        (self.source / 'data.dts').unlink()
        with self.assertRaisesRegex(ValueError, 'data_mode_required'):
            self.prepare()
        self.assertFalse(self.workspace.exists())
        (self.source / 'data.dts').write_bytes(b'fixture')
        (self.source / 'game.ini').write_bytes(b'unknown')
        with self.assertRaisesRegex(ValueError, 'unsupported_background_configuration'):
            self.prepare()
        self.assertFalse(self.workspace.exists())

    def test_existing_or_overlapping_workspace_never_overwritten(self):
        self.workspace.mkdir()
        sentinel = self.workspace / 'sentinel'
        sentinel.write_text('keep', encoding='utf-8')
        with self.assertRaises(FileExistsError):
            self.prepare()
        self.assertEqual(sentinel.read_text(encoding='utf-8'), 'keep')
        with self.assertRaisesRegex(ValueError, 'overlapping_paths'):
            native.prepare(self.source, self.source / 'batch', external=self.external)

    def test_non_x86_image_rejected_even_when_digest_matches_fixture(self):
        data = bytearray((self.source / 'game.exe').read_bytes())
        struct.pack_into('<H', data, 68, 0x8664)
        (self.source / 'game.exe').write_bytes(data)
        self.digest = hashlib.sha256(data).hexdigest()
        with self.assertRaisesRegex(ValueError, 'x86_image_required'):
            self.prepare()
        self.assertFalse(self.workspace.exists())


if __name__ == '__main__':
    unittest.main()
