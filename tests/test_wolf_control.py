"""Wolf 原生桥的版本／副本边界与真实 C++ 安全核心回归。"""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def prepare_module():
    path = ROOT / 'plugins/wolf/prepare.py'
    if not path.is_file():
        raise AssertionError('Wolf 正式副本准备入口尚未实现')
    spec = importlib.util.spec_from_file_location('wolf_prepare_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WolfControlTests(unittest.TestCase):
    def setUp(self):
        parent = Path('E:/DevData/gameqa-wolf/tmp') if os.name == 'nt' else None
        if parent:
            parent.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'sample'
        self.source.mkdir()
        (self.source / 'Game.exe').write_bytes(b'MZ-not-supported')

    def test_unknown_binary_is_rejected_without_creating_workspace(self):
        module = prepare_module()
        workspace = self.root / 'workspace'
        with self.assertRaises(ValueError):
            module.prepare(self.source, workspace)
        self.assertFalse(workspace.exists())
        self.assertEqual((self.source / 'Game.exe').read_bytes(), b'MZ-not-supported')

    def test_overlap_and_existing_workspace_cannot_overwrite_data(self):
        module = prepare_module()
        existing = self.root / 'existing'
        existing.mkdir()
        (existing / 'progress').write_bytes(b'preserve')
        with patch.object(module, 'validate_image'):
            for workspace in (self.source / 'child', self.root):
                with self.subTest(workspace=workspace), self.assertRaises(ValueError):
                    module.prepare(self.source, workspace)
            with self.assertRaises(FileExistsError):
                module.prepare(self.source, existing)
        self.assertEqual((existing / 'progress').read_bytes(), b'preserve')

    def test_preparation_preserves_assets_but_excludes_original_saves(self):
        module = prepare_module()
        (self.source / 'Data').mkdir()
        (self.source / 'Data/asset').write_bytes(b'game-assets')
        for name in ('Save', 'SaveData'):
            (self.source / name).mkdir()
            (self.source / name / 'Save9.dat').write_bytes(b'original-progress')
        workspace = self.root / 'fresh'
        with patch.object(module, 'validate_image'):
            record = module.prepare(self.source, workspace)
        runtime = Path(record['runtime'])
        self.assertEqual((runtime / 'Data/asset').read_bytes(), b'game-assets')
        for name in ('Save', 'SaveData'):
            self.assertFalse((runtime / name / 'Save9.dat').exists())
            self.assertEqual((self.source / name / 'Save9.dat').read_bytes(), b'original-progress')
        self.assertTrue((workspace / 'preparation.json').is_file())
        self.assertTrue((workspace / '测试数据记录.md').is_file())
        self.assertFalse(record['copied_user_saves'])

    def test_native_control_core(self):
        compiler = Path('E:/vsCode/VSBuildTools/VC/Auxiliary/Build/vcvars32.bat')
        if os.name != 'nt' or not compiler.is_file():
            self.skipTest('需要已安装的 Windows MSVC x86')
        output = self.root / 'CoreTests.exe'
        result = subprocess.run(
            [sys.executable, str(ROOT / 'plugins/wolf/build.py'), '--test-core', '--output', str(output)],
            capture_output=True, encoding='utf-8', errors='replace', timeout=45,
            env={**os.environ, 'TEMP': str(self.root), 'TMP': str(self.root)})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Wolf core: all safety checks passed', result.stdout)


if __name__ == '__main__':
    unittest.main()
