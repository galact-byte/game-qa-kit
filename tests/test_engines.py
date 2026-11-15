"""只读引擎识别：抓错家族、无限资源扫描或把识别当控制成功都会失败。"""
import hashlib
import importlib
import importlib.util
import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


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

    def test_wolf_candidate_exposes_adapter_without_claiming_binary_support(self):
        for marker in ('Data.wolf', 'Data/BasicData/Game.dat'):
            with self.subTest(marker=marker):
                path = self.touch(marker)
                result = self.engines.inspect_game(self.root)
                self.assertEqual(result['engine'], 'wolf_candidate')
                self.assertEqual(result['adapter'], 'wolf')
                self.assertFalse(result['control_verified'])
                path.unlink()

    def test_srpg_remains_an_unverified_candidate(self):
        self.touch('Runtime.rts')
        result = self.engines.inspect_game(self.root)
        self.assertEqual(result['engine'], 'srpgstudio_candidate')
        self.assertIsNone(result['adapter'])
        self.assertFalse(result['control_verified'])

    def test_native_profile_matches_only_exact_image_and_data_mode(self):
        image = self.touch('game.exe', b'MZ' + bytes(126))
        self.touch('Runtime.rts')
        data = self.touch('data.dts')
        digest = hashlib.sha256(image.read_bytes()).hexdigest()
        sample = {'id': 'srpgstudio-x86-fixed', 'exe_sha256': digest}
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        with patch.object(self.engines, '_NATIVE_SAMPLE', sample, create=True):
            result = self.engines.inspect_game(self.root)
            self.assertEqual(result['adapter'], 'native_input')
            self.assertEqual(result['profile_id'], sample['id'])
            self.assertFalse(result['control_verified'])
            self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.iterdir()})
            image.write_bytes(image.read_bytes() + b'changed')
            unknown = self.engines.inspect_game(self.root)
            self.assertIsNone(unknown['adapter'])
            self.assertNotIn('profile_id', unknown)
            image.write_bytes(before['game.exe'])
            data.unlink()
            self.assertIsNone(self.engines.inspect_game(self.root)['adapter'])

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

    def test_godot_pack_requires_complete_header_and_stays_unmanaged(self):
        pack = self.touch('demo.pck', b'GDPC')
        self.assertEqual(self.engines.inspect_game(self.root)['engine'], 'unknown')
        for format_version, version in [(2, (4, 5, 1)), (3, (4, 6, 3)), (4, (4, 7, 2))]:
            with self.subTest(version=version):
                pack.write_bytes(struct.pack('<4s5IQ16I', b'GDPC', format_version, *version, 2, 96, *([0] * 16)))
                before = pack.read_bytes()
                result = self.engines.inspect_game(self.root)
                self.assertEqual(result['engine'], 'godot4')
                self.assertIsNone(result['adapter'])
                self.assertFalse(result['control_verified'])
                self.assertIn('demo.pck:GDPC', result['evidence'])
                self.assertEqual(pack.read_bytes(), before)
        pack.write_bytes(b'not a resource pack' + bytes(128))
        self.assertEqual(self.engines.inspect_game(self.root)['engine'], 'unknown')

    def test_godot_project_file_requires_version_evidence(self):
        project = self.touch('project.godot', b'not a project')
        self.assertEqual(self.engines.inspect_game(self.root)['engine'], 'unknown')
        project.write_bytes(b'config_version=5\n[application]\nconfig/name="Fixture"\n')
        result = self.engines.inspect_game(self.root)
        self.assertEqual(result['engine'], 'godot4_candidate')
        self.assertIsNone(result['adapter'])
        self.assertFalse(result['control_verified'])

    def test_renpy_requires_runtime_pair_and_keeps_control_unverified(self):
        self.touch('game/script.rpy', b'label start:\n    return\n')
        self.assertEqual(self.engines.inspect_game(self.root)['engine'], 'unknown')
        self.touch('renpy/__init__.py')
        self.assertEqual(self.engines.inspect_game(self.root)['engine'], 'unknown')
        self.touch('renpy/bootstrap.py')
        result = self.engines.inspect_game(self.root)
        self.assertEqual(result['engine'], 'renpy')
        self.assertIsNone(result['adapter'])
        self.assertFalse(result['control_verified'])

    def tyrano_archive(self, *, members=None):
        members = members or {'tyrano/tyrano.js': b'engine', 'tyrano/plugins/kag/kag.js': b'kag'}
        tree = {'files': {}}
        body = bytearray()
        for name, content in members.items():
            node = tree
            *parents, leaf = name.split('/')
            for parent in parents:
                node = node['files'].setdefault(parent, {'files': {}})
            node['files'][leaf] = {'size': len(content), 'offset': str(len(body))}
            body.extend(content)
        raw = json.dumps(tree, separators=(',', ':')).encode()
        padding = bytes(-len(raw) % 4)
        payload_size = 4 + len(raw) + len(padding)
        return self.touch('resources/app.asar', struct.pack('<4I', 4, payload_size + 4,
                          payload_size, len(raw)) + raw + padding + body)

    def test_tyrano_requires_both_engine_scripts_in_the_same_web_root(self):
        for prefix in ('', 'resources/app/', 'resources/app/src/'):
            with self.subTest(prefix=prefix):
                core = self.touch(prefix + 'tyrano/tyrano.js')
                self.assertEqual(self.engines.inspect_game(self.root)['engine'], 'unknown')
                kag = self.touch(prefix + 'tyrano/plugins/kag/kag.js')
                result = self.engines.inspect_game(self.root)
                self.assertEqual(result['engine'], 'tyranoscript_candidate')
                self.assertIsNone(result['adapter'])
                self.assertFalse(result['control_verified'])
                self.assertEqual(result['evidence'], [prefix + 'tyrano/tyrano.js',
                                                     prefix + 'tyrano/plugins/kag/kag.js'])
                core.unlink()
                kag.unlink()
        self.touch('tyrano/tyrano.js')
        self.touch('resources/app/tyrano/plugins/kag/kag.js')
        self.assertEqual(self.engines.inspect_game(self.root)['engine'], 'unknown')

    def test_tyrano_asar_index_is_read_only_and_not_control_support(self):
        archive = self.tyrano_archive()
        before = archive.read_bytes()
        result = self.engines.inspect_game(self.root)
        self.assertEqual(result['engine'], 'tyranoscript_candidate')
        self.assertIsNone(result['adapter'])
        self.assertFalse(result['control_verified'])
        self.assertEqual(result['evidence'], ['resources/app.asar:tyrano/tyrano.js',
                                             'resources/app.asar:tyrano/plugins/kag/kag.js'])
        self.assertEqual(archive.read_bytes(), before)
        self.assertEqual(list(archive.parent.iterdir()), [archive])

    def test_tyrano_asar_rejects_missing_scripts_and_non_engine_electron(self):
        for members in ({'tyrano/tyrano.js': b'engine'}, {'main.js': b'Electron application'},
                        {'backup/tyrano/tyrano.js': b'engine', 'backup/tyrano/plugins/kag/kag.js': b'kag'}):
            with self.subTest(members=list(members)):
                self.tyrano_archive(members=members)
                self.assertEqual(self.engines.inspect_game(self.root)['engine'], 'unknown')

    def test_tyrano_asar_rejects_truncation_and_unbounded_headers(self):
        archive = self.tyrano_archive()
        valid = archive.read_bytes()
        for broken in (b'', valid[:12], valid[:40], valid[:-1],
                       struct.pack('<4I', 4, 0xffffffff, 0xfffffffb, 0xfffffff7),
                       struct.pack('<4I', 4, 12, 8, 4) + b'null',
                       struct.pack('<4I', 4, 12, 8, 4) + b'{bad',
                       struct.pack('<I', 8) + valid[4:]):
            with self.subTest(prefix=broken[:16], size=len(broken)):
                archive.write_bytes(broken)
                self.assertEqual(self.engines.inspect_game(self.root)['engine'], 'unknown')

    def test_tyrano_asar_rejects_links_and_invalid_member_ranges(self):
        archive = self.tyrano_archive()
        valid = archive.read_bytes()
        tree = json.loads(valid[16:16 + struct.unpack('<4I', valid[:16])[3]])
        leaf = tree['files']['tyrano']['files']['tyrano.js']
        for entry in ({'link': 'outside.js'}, {'unpacked': True, 'size': 6},
                      {'offset': '-1', 'size': 6}, {'offset': '0', 'size': True},
                      {'offset': '999999', 'size': 6}, {'offset': '0', 'size': 999999}):
            with self.subTest(entry=entry):
                leaf.clear()
                leaf.update(entry)
                raw = json.dumps(tree).encode()
                padding = bytes(-len(raw) % 4)
                payload_size = 4 + len(raw) + len(padding)
                archive.write_bytes(struct.pack('<4I', 4, payload_size + 4, payload_size, len(raw))
                                    + raw + padding + b'enginekag')
                self.assertEqual(self.engines.inspect_game(self.root)['engine'], 'unknown')

    def test_emuera_candidate_requires_runtime_and_era_data_without_claiming_control(self):
        self.touch('CSV/FLAG.csv', b'0,Test\n')
        self.touch('ERB/probe.erb', b'@SYSTEM_TITLE\nWAIT\n')
        for name in ('Emuera.exe', 'Emuera_skiaV10_x64.exe', 'EmueraFL_v1.45-preview.exe'):
            with self.subTest(runtime=name):
                image = self.touch(name, b'MZ' + bytes(64))
                before = {str(p.relative_to(self.root)): p.read_bytes()
                          for p in self.root.rglob('*') if p.is_file()}
                result = self.engines.inspect_game(image)
                self.assertEqual(result['engine'], 'emuera_candidate')
                self.assertIsNone(result['adapter'])
                self.assertFalse(result['control_verified'])
                self.assertEqual(result['evidence'], ['CSV/FLAG.csv', 'ERB/', name + ':MZ'])
                self.assertEqual(before, {str(p.relative_to(self.root)): p.read_bytes()
                                         for p in self.root.rglob('*') if p.is_file()})
                image.unlink()

    def test_emuera_rejects_partial_layouts_and_non_runtime_images(self):
        cases = [(['Emuera.exe'], b'MZ'),
                 (['Emuera.exe', 'CSV/FLAG.csv'], b'MZ'),
                 (['Emuera.exe', 'ERB/probe.erb'], b'MZ'),
                 (['CSV/FLAG.csv', 'ERB/probe.erb'], b'MZ'),
                 (['update.exe', 'CSV/FLAG.csv', 'ERB/probe.erb'], b'MZ'),
                 (['Emuera.exe', 'CSV/FLAG.csv', 'ERB/probe.erb'], b'not executable')]
        for index, (names, header) in enumerate(cases):
            with self.subTest(names=names, header=header):
                for name in names:
                    self.touch(f'case-{index}/{name}', header if name.endswith('.exe') else b'fixture')
                self.assertEqual(self.engines.inspect_game(self.root / f'case-{index}')['engine'], 'unknown')

    def test_emuera_runtime_directory_is_not_an_executable(self):
        self.touch('Emuera.exe/not-an-image')
        self.touch('CSV/FLAG.csv')
        self.touch('ERB/probe.erb')
        self.assertEqual(self.engines.inspect_game(self.root)['engine'], 'unknown')

    def test_scan_finds_emuera_but_keeps_it_unmanaged(self):
        self.touch('nested/Emuera.exe', b'MZ')
        self.touch('nested/CSV/FLAG.csv')
        self.touch('nested/ERB/probe.erb')
        result = self.engines.scan_games([self.root], max_depth=2, max_dirs=5)
        self.assertEqual(len(result['games']), 1)
        self.assertEqual(result['games'][0]['engine'], 'emuera_candidate')
        self.assertIsNone(result['games'][0]['adapter'])
        self.assertFalse(result['games'][0]['control_verified'])

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
