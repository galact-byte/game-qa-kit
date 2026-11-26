"""发布构建必须拒绝不安全版本和已有输出，不清空用户目录。"""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('release_build', Path(__file__).resolve().parents[1] / 'tools/build_windows.py')
BUILD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD)


class ReleaseBuildTests(unittest.TestCase):
    def test_remote_windows_candidate_regressions_include_route_tools(self):
        workflow = (BUILD.ROOT / '.github/workflows/universal-desktop.yml').read_text(encoding='utf-8')
        windows = workflow.split('      - name: Run portable Windows regression suite', 1)[1].split('      - name:', 1)[0]
        self.assertIn('tests.test_route_report', windows)
        self.assertIn('tests.test_route_mcp', windows)

    def test_remote_windows_candidate_retains_frozen_startup_evidence(self):
        workflow = (BUILD.ROOT / '.github/workflows/universal-desktop.yml').read_text(encoding='utf-8')
        upload = workflow.split('      - name: Upload Windows candidate without publishing', 1)[1].split('  build:', 1)[0]
        self.assertIn('/worker-self-test.json', upload)
        self.assertIn('/portable-gui.png', upload)

    def test_invalid_version_is_rejected_before_creating_files_or_spawning(self):
        with tempfile.TemporaryDirectory() as root, mock.patch.object(BUILD.subprocess, 'run') as run:
            output = Path(root) / 'new-build'
            for version in ('v1', '../v1.0.0', 'v1.0.0;echo bad', ''):
                with self.assertRaises(ValueError):
                    BUILD.build(version, output)
            self.assertFalse(output.exists())
            run.assert_not_called()

    def test_existing_output_is_preserved_and_never_overwritten(self):
        with tempfile.TemporaryDirectory() as root, mock.patch.object(BUILD.subprocess, 'run') as run, \
                mock.patch.object(BUILD.sys, 'platform', 'win32'), \
                mock.patch.object(BUILD.struct, 'calcsize', return_value=8):
            marker = Path(root) / 'previous-data'
            marker.write_text('keep', encoding='utf-8')
            with self.assertRaises(FileExistsError):
                BUILD.build('v0.2.1', Path(root))
            self.assertEqual(marker.read_text(encoding='utf-8'), 'keep')
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
