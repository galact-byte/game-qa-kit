"""Verify a release includes the five platform packages with valid checksums."""
import hashlib
from pathlib import Path
import tempfile
import unittest

from tools.assemble_release import assemble


class AssembleReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.downloads = self.root / 'downloads'
        self.output = self.root / 'release'
        self.packages = []
        for target in ('windows-x64.zip', 'macos-x64.tar.gz', 'macos-arm64.tar.gz',
                       'linux-x64.tar.gz', 'linux-arm64.tar.gz'):
            directory = self.downloads / target
            directory.mkdir(parents=True)
            package = directory / ('game-qa-kit-v0.4.2-' + target)
            package.write_bytes(target.encode())
            checksum = hashlib.sha256(package.read_bytes()).hexdigest()
            (directory / 'SHA256SUMS.txt').write_text(checksum + '  ' + package.name + '\n', encoding='utf-8')
            self.packages.append(package)

    def test_collects_all_five_packages_and_writes_one_manifest(self):
        assemble(self.downloads, self.output, 'v0.4.2')
        lines = (self.output / 'SHA256SUMS.txt').read_text(encoding='utf-8').splitlines()
        self.assertEqual(5, len(lines))
        for package in self.packages:
            copied = self.output / package.name
            self.assertEqual(package.read_bytes(), copied.read_bytes())
            self.assertIn(hashlib.sha256(copied.read_bytes()).hexdigest() + '  ' + copied.name, lines)

    def test_rejects_missing_platform_or_corrupted_archive_before_assembly(self):
        for corruption in ('missing', 'bytes', 'manifest'):
            with self.subTest(corruption=corruption):
                package = self.packages[-1]
                data = package.read_bytes()
                manifest = package.parent / 'SHA256SUMS.txt'
                text = manifest.read_text(encoding='utf-8')
                if corruption == 'missing':
                    package.unlink()
                elif corruption == 'bytes':
                    package.write_bytes(b'changed')
                else:
                    manifest.write_text(text.replace(package.name, '../' + package.name), encoding='utf-8')
                with self.assertRaises(ValueError):
                    assemble(self.downloads, self.output, 'v0.4.2')
                self.assertFalse(self.output.exists())
                package.write_bytes(data)
                manifest.write_text(text, encoding='utf-8')
