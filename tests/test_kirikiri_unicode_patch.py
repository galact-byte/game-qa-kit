"""Unicode 覆盖的字节保真、坏输入拒绝及准备入口防篡改验证。"""
import hashlib
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib

from plugins.kirikiri import prepare
from plugins.kirikiri.unicode_patch import archive_scripts, patch_bytes, write_patch


def authored_archive(files):
    def chunk(tag, data):
        return tag + struct.pack('<Q', len(data)) + data
    body, index = bytearray(), bytearray()
    for name, data in files.items():
        encoded = name.encode('utf-16le')
        entry = chunk(b'info', struct.pack('<LQQH', 0x80000000, len(data), len(data), len(encoded)//2) + encoded)
        entry += chunk(b'segm', struct.pack('<LQQQ', 0, 19 + len(body), len(data), len(data)))
        entry += chunk(b'adlr', struct.pack('<L', zlib.adler32(data)))
        index += chunk(b'File', entry)
        body += data
    return b'XP3\r\n \n\x1a\x8bg\x01' + struct.pack('<Q', 19 + len(body)) + body + b'\x00' + struct.pack('<Q', len(index)) + index


class UnicodePatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='E:/DevData')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_character_and_newline_preservation_without_binary_or_unicode_changes(self):
        original = '// 半角ｱ\r\nvar word="日本語";\r\n'.encode('cp932')
        native = b'\xff\xfe' + '変換済み'.encode('utf-16le')
        source = self.root / 'data.xp3'
        source.write_bytes(authored_archive({'system/probe.tjs': original,
            'scenario/native.ks': native, 'image/a.png': b'\x89PNG\0', 'ascii.tjs': b'var a=1;'}))
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        files = archive_scripts(source)
        self.assertEqual(set(files), {'system/probe.tjs', 'scenario/native.ks', 'ascii.tjs'})
        report = write_patch(self.root / 'patch.xp3', files)
        actual = archive_scripts(self.root / 'patch.xp3')
        self.assertEqual(actual, {'probe.tjs': b'\xff\xfe' + '// 半角ｱ\r\nvar word="日本語";\r\n'.encode('utf-16le')})
        self.assertEqual([r['name'] for r in report['entries']], ['system/probe.tjs'])
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), digest)
        again, _ = patch_bytes(dict(reversed(list(files.items()))))
        self.assertEqual(hashlib.sha256(again).hexdigest(), report['sha256'])

    def test_flat_patch_lookup_rejects_conflicting_basenames(self):
        with self.assertRaises(ValueError):
            patch_bytes({'system/shared.tjs': '// ｱ'.encode('cp932'),
                         'others/SHARED.tjs': '// 日本語'.encode('cp932')})

    def test_invalid_text_fails_before_creating_output(self):
        for data in (b'\x81', b'a\0b', b'\xfe\xfe\x01', b'\xef\xbb\xbfhello', b'\xff\xfe\x00'):
            with self.subTest(data=data), self.assertRaises((ValueError, UnicodeError)):
                write_patch(self.root / 'patch.xp3', {'script.tjs': data})
            self.assertFalse((self.root / 'patch.xp3').exists())

    def test_existing_evidence_is_never_overwritten(self):
        path = self.root / 'patch.xp3'
        path.write_bytes(b'preserved')
        with self.assertRaises(FileExistsError):
            write_patch(path, {'script.tjs': '// ｱ'.encode('cp932')})
        self.assertEqual(path.read_bytes(), b'preserved')

    def test_bad_archive_checksums_and_truncation_are_rejected(self):
        valid = authored_archive({'script.tjs': '// ｱ'.encode('cp932')})
        for data in (valid[:5], valid[:-1], valid[:19] + b'!' + valid[20:]):
            source = self.root / 'data.xp3'
            source.write_bytes(data)
            with self.subTest(data=data[:20]), self.assertRaises(ValueError):
                archive_scripts(source)

    def test_preparation_overlay_is_opt_in_and_tampering_cannot_redefine_hash(self):
        source = self.root / 'source'
        source.mkdir()
        inputs = {'runtime.exe': b'authored runtime', 'wuvorbis.dll': b'authored audio',
                  'data.xp3': authored_archive({'others/probe.ks': '; ｱ\n[wait]\n'.encode('cp932')})}
        for name, data in inputs.items():
            (source / name).write_bytes(data)
        hashes = {name: hashlib.sha256(data).hexdigest() for name, data in inputs.items()}
        with patch.object(prepare, 'INPUTS', hashes):
            report = prepare.prepare(source, self.root / 'run', 'runtime.exe', unicode_patch=True)
            prepare.validate(self.root / 'run')
            overlay = self.root / 'run/game/patch.xp3'
            self.assertEqual(archive_scripts(overlay)['probe.ks'].decode('utf-16'), '; ｱ\n[wait]\n')
            self.assertEqual((self.root / 'run/game/data.xp3').read_bytes(), inputs['data.xp3'])
            import json
            overlay.write_bytes(b'tampered')
            report['unicode_patch']['sha256'] = hashlib.sha256(b'tampered').hexdigest()
            (self.root / 'run/preparation.json').write_text(json.dumps(report), encoding='utf-8')
            with self.assertRaises(ValueError):
                prepare.validate(self.root / 'run')


if __name__ == '__main__':
    unittest.main()
