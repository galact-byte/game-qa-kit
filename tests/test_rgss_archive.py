"""RGSSAD v3 在真实字节输入上的路径、数据范围与提取回收边界。"""
import importlib
from pathlib import Path
import struct
import tempfile
import unittest


def archive_bytes(entries, *, offsets=None):
    seed = 123
    key = (seed * 9 + 3) & 0xffffffff
    key_bytes = struct.pack('<I', key)
    names = [name.encode('utf-8') for name, _ in entries]
    position = 12 + sum(16 + len(name) for name in names) + 16
    table, data = bytearray(), bytearray()
    for index, ((_, body), name) in enumerate(zip(entries, names)):
        content_key = 7123 + index
        offset = position if offsets is None else offsets[index]
        table.extend(struct.pack('<IIII', *(value ^ key for value in (offset, len(body), content_key, len(name)))))
        table.extend(value ^ key_bytes[i % 4] for i, value in enumerate(name))
        encrypted = bytearray(body)
        for start in range(0, len(body), 4):
            block = struct.pack('<I', content_key)
            for i in range(min(4, len(body) - start)):
                encrypted[start + i] ^= block[i]
            content_key = (content_key * 7 + 3) & 0xffffffff
        data.extend(encrypted)
        position += len(body)
    table.extend(struct.pack('<IIII', key, key, key, key))
    return b'RGSSAD\0\x03' + struct.pack('<I', seed) + table + data


class RgssArchiveTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.module = importlib.import_module('plugins.rgss3.archive')
        self.path = self.root / 'Game.rgss3a'

    def read(self, entries):
        self.path.write_bytes(archive_bytes(entries))
        return self.module.table(self.path)

    def test_decrypt_and_extract_binary_and_unicode_names(self):
        entries = [('Data\\Scripts.rvdata2', bytes(range(256)) + b'last'), ('Graphics/图.png', b'image')]
        rows = self.read(entries)
        destination = self.root / 'runtime'
        destination.mkdir()
        self.module.extract(self.path, destination)
        for (name, body), row in zip(entries, rows):
            self.assertEqual(self.module.decrypt(self.path, row), body)
            self.assertEqual((destination / name.replace('\\', '/')).read_bytes(), body)

    def test_unsafe_paths_and_aliases_are_rejected_before_writes(self):
        for name in ['../outside', '/outside', '\\outside', 'C:/outside', 'a:stream',
                     'a/../outside', 'a/./b', 'a//b', 'a/b.', 'a/b ', 'CON.txt',
                     'a/NUL', 'a\x00b', 'a\nb', 'a/LPT1.txt', '']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.read([(name, b'bad')])
        self.assertFalse((self.root / 'outside').exists())

    def test_duplicate_and_file_directory_collisions_are_rejected(self):
        for names in [('Data/a', 'data/A'), ('Data', 'Data/a'), ('Data/a', 'data')]:
            with self.subTest(names=names), self.assertRaises(ValueError):
                self.read([(name, b'x') for name in names])

    def test_truncation_header_and_content_ranges_are_rejected(self):
        body = archive_bytes([('Data/a', b'abcdef')])
        for invalid in [body[:7], body[:12], body[:-1], body.replace(b'RGSSAD', b'BADBAD', 1),
                        archive_bytes([('Data/a', b'ab')], offsets=[12]),
                        archive_bytes([('a', b'abcd'), ('b', b'efgh')], offsets=[1000, 1002])]:
            self.path.write_bytes(invalid)
            with self.subTest(size=len(invalid)), self.assertRaises(ValueError):
                self.module.table(self.path)

    def test_existing_file_or_link_cannot_be_overwritten(self):
        self.read([('Data/a', b'new')])
        destination = self.root / 'runtime'
        (destination / 'Data').mkdir(parents=True)
        (destination / 'Data/a').write_bytes(b'existing')
        with self.assertRaises(ValueError):
            self.module.extract(self.path, destination)
        self.assertEqual((destination / 'Data/a').read_bytes(), b'existing')


if __name__ == '__main__':
    unittest.main()
