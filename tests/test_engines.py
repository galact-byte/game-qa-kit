"""只读引擎识别：抓错家族、无限资源扫描或把识别当控制成功都会失败。"""
import importlib
import importlib.util
import tempfile
import unittest
from pathlib import Path


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.assertIsNotNone(importlib.util.find_spec('gameqakit.engines'), '尚无只读引擎识别入口')
        self.engines = importlib.import_module('gameqakit.engines')

    def touch(self, name, body=b''):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        return path

    def test_mv_and_mz_recognize_web_root_without_claiming_control(self):
        for prefix, core, want in [('', 'rpg_core.js', 'rpgmaker_mv'),
                                    ('www/', 'rmmz_core.js', 'rpgmaker_mz')]:
            with self.subTest(engine=want):
                path = self.touch(prefix + 'js/' + core)
                result = self.engines.inspect_game(self.root)
                self.assertEqual(result['engine'], want)
                self.assertEqual(result['adapter'], 'rpgmaker_js')
                self.assertFalse(result['control_verified'])
                self.assertIn(prefix + 'js/' + core, result['evidence'])
                path.unlink()

    def test_rgss_versions_are_separate_from_javascript(self):
        for dll, want in [('RGSS104E.dll', 'rpgmaker_xp'), ('RGSS202E.dll', 'rpgmaker_vx'),
                           ('RGSS301.dll', 'rpgmaker_vxace')]:
            with self.subTest(dll=dll):
                self.touch('Game.ini', ('[Game]\nLibrary=System\\' + dll + '\nTitle=\ufffd\n').encode())
                self.assertEqual(self.engines.inspect_game(self.root)['engine'], want)

    def test_executable_name_alone_is_not_an_engine(self):
        self.touch('Game.exe')
        self.assertEqual(self.engines.inspect_game(self.root / 'Game.exe')['engine'], 'unknown')

    def test_unity_backend_requires_matching_data_evidence(self):
        self.touch('UnityPlayer.dll')
        self.touch('Demo_Data/Managed/Assembly-CSharp.dll')
        self.assertEqual(self.engines.inspect_game(self.root)['engine'], 'unity_mono')
        self.touch('GameAssembly.dll')
        self.touch('Demo_Data/il2cpp_data/Metadata/global-metadata.dat')
        self.assertEqual(self.engines.inspect_game(self.root)['engine'], 'unity_il2cpp')

    def test_wolf_and_srpg_are_candidates_not_claimed_adapters(self):
        for marker, want in [('Data.wolf', 'wolf_candidate'), ('Runtime.rts', 'srpgstudio_candidate')]:
            with self.subTest(engine=want):
                path = self.touch(marker)
                result = self.engines.inspect_game(self.root)
                self.assertEqual(result['engine'], want)
                self.assertIsNone(result['adapter'])
                self.assertFalse(result['control_verified'])
                path.unlink()

    def test_hsp_bytecode_signature_is_required_and_not_control_verification(self):
        marker = self.touch('start.ax', b'not hsp')
        self.assertEqual(self.engines.inspect_game(self.root)['engine'], 'unknown')
        marker.write_bytes(b'HSP3' + bytes(108))
        result = self.engines.inspect_game(self.root)
        self.assertEqual(result['engine'], 'hsp3')
        self.assertEqual(result['adapter'], 'hsp')
        self.assertFalse(result['control_verified'])

    def test_duckstation_needs_runtime_evidence_without_claiming_version_support(self):
        executable = self.touch('duckstation-qt-x64-ReleaseLTCG.exe', b'MZ')
        self.assertEqual(self.engines.inspect_game(self.root)['engine'], 'unknown')
        self.touch('SDL3.dll')
        executable.write_bytes(b'not a PE image')
        self.assertEqual(self.engines.inspect_game(self.root)['engine'], 'unknown')
        executable.write_bytes(b'MZ' + bytes(64))
        result = self.engines.inspect_game(executable)
        self.assertEqual(result['engine'], 'duckstation_windows')
        self.assertEqual(result['adapter'], 'duckstation')
        self.assertFalse(result['control_verified'])

    def test_scan_obeys_directory_budget_and_does_not_descend_into_assets(self):
        self.touch('game/js/rpg_core.js')
        self.touch('Graphics/not_a_game/js/rmmz_core.js')
        for number in range(5):
            self.touch(f'other-{number}/nested/file')
        result = self.engines.scan_games([self.root], max_depth=3, max_dirs=20)
        self.assertEqual([row['engine'] for row in result['games']], ['rpgmaker_mv'])
        limited = self.engines.scan_games([self.root], max_depth=5, max_dirs=1)
        self.assertEqual(limited['visited'], 1)
        self.assertTrue(limited['truncated'])

    def test_invalid_scan_limits_and_missing_root_are_explicit(self):
        for options in ({'max_dirs': 0}, {'max_depth': -1}, {'max_dirs': True}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.engines.scan_games([self.root], **options)
        result = self.engines.scan_games([self.root / 'absent'])
        self.assertEqual(len(result['errors']), 1)
        self.assertEqual(result['games'], [])


if __name__ == '__main__':
    unittest.main()
