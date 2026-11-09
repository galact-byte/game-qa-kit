"""版本锁定、隔离边界与原生核心；禁止把其他 EXE 套入固定偏移。"""
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load_prepare():
    path = ROOT / 'plugins/duckstation/prepare.py'
    if not path.is_file():
        raise AssertionError('尚无正式 DuckStation 隔离准备入口')
    spec = importlib.util.spec_from_file_location('duckstation_prepare_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DuckStationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_compiled_native_control_core_safety(self):
        compiler = Path('E:/vsCode/VSBuildTools/VC/Auxiliary/Build/vcvars64.bat')
        if os.name != 'nt' or not compiler.is_file():
            self.skipTest('需要已安装的 Windows MSVC x64')
        parent = Path('E:/DevData/gameqa-ps1/tmp')
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='core-', dir=parent) as directory:
            result = subprocess.run(
                [sys.executable, str(ROOT / 'plugins/duckstation/build.py'), '--test-core',
                 '--output', str(Path(directory) / 'CoreTests.exe'), '--vcvars', str(compiler)],
                capture_output=True, encoding='utf-8', errors='replace', timeout=30,
                env={**os.environ, 'TEMP': directory, 'TMP': directory})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('DuckStation core: native frames', result.stdout)

    def test_other_binaries_are_rejected_before_creating_workspace(self):
        module = load_prepare()
        source = self.root / 'emulator'
        source.mkdir()
        (source / module.EXE).write_bytes(b'MZ-not-the-supported-build')
        game = self.root / 'game/title.cue'
        game.parent.mkdir()
        game.write_text('FILE "disc.bin" BINARY', encoding='utf-8')
        workspace = self.root / 'isolated'
        with self.assertRaises(ValueError):
            module.prepare(source, workspace, game)
        self.assertFalse(workspace.exists())
        self.assertEqual((source / module.EXE).read_bytes(), b'MZ-not-the-supported-build')

    def test_fresh_configuration_skips_setup_and_uses_only_new_save_paths(self):
        import configparser
        module = load_prepare()
        source = self.root / 'emulator'
        source.mkdir()
        (source / module.EXE).write_bytes(b'fixture')
        (source / 'settings.ini').write_text('[Main]\nUserToken=private', encoding='utf-8')
        (source / 'memcards').mkdir()
        (source / 'memcards/original.mcd').write_bytes(b'original-progress')
        for folder in ('bios', 'resources', 'QtPlugins', 'translations'):
            (source / folder).mkdir()
        game = self.root / 'game/title.cue'
        game.parent.mkdir()
        game.touch()
        workspace = self.root / 'isolated'
        with patch.object(module, 'validate_image'):
            module.prepare(source, workspace, game)
        cfg = configparser.ConfigParser()
        cfg.read(workspace / 'runtime/settings.ini', encoding='utf-8')
        self.assertFalse(cfg.getboolean('Main', 'SetupWizardIncomplete'))
        self.assertFalse(cfg.has_option('Main', 'UserToken'))
        self.assertFalse((workspace / 'runtime/memcards/original.mcd').exists())
        self.assertTrue(Path(cfg['MemoryCards']['Card1Path']).is_relative_to(workspace))
        self.assertEqual((source / 'memcards/original.mcd').read_bytes(), b'original-progress')

    def test_workspaces_cannot_overlap_original_data_or_be_overwritten(self):
        module = load_prepare()
        source = self.root / 'source'
        source.mkdir()
        game = self.root / 'game/title.cue'
        game.parent.mkdir()
        game.touch()
        for workspace in (source / 'child', game.parent / 'child', self.root):
            with self.subTest(workspace=workspace), self.assertRaises((ValueError, FileExistsError)):
                module.prepare(source, workspace, game)
        existing = self.root / 'existing'
        existing.mkdir()
        (existing / 'progress').write_bytes(b'preserve')
        with self.assertRaises(FileExistsError):
            module.prepare(source, existing, game)
        self.assertEqual((existing / 'progress').read_bytes(), b'preserve')


if __name__ == '__main__':
    unittest.main()
