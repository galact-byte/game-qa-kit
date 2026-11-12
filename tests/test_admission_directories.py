"""目录偏好及有界会话发现的真实文件行为。"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from gameqakit import admission
from gameqakit.sessions import write_json


class AdmissionDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_preferences_round_trip_in_selected_root_with_separate_locator(self):
        locator = self.root / 'local-config/location.txt'
        qa = self.root / 'qa'
        with mock.patch('gameqakit.admission.preference_locator', return_value=locator):
            self.assertEqual(admission.load_preferences(), {})
            admission.save_preferences(qa, source=str(self.root / 'game'), artifacts='build')
            result = admission.load_preferences()
        self.assertEqual(result['qa_root'], str(qa))
        self.assertEqual(result['source'], str(self.root / 'game'))
        self.assertEqual(result['artifacts'], 'build')
        self.assertTrue((qa / 'admission-preferences.json').is_file())
        self.assertNotIn('build', locator.read_text(encoding='utf-8'))

    def test_preferences_refuse_source_overlap_and_unknown_commands(self):
        locator = self.root / 'location.txt'
        game = self.root / 'game'
        game.mkdir()
        with mock.patch('gameqakit.admission.preference_locator', return_value=locator):
            with self.assertRaisesRegex(ValueError, 'overlapping'):
                admission.save_preferences(game / 'qa', source=str(game), artifacts='build')
            self.assertFalse(locator.exists())
            qa = self.root / 'qa'
            qa.mkdir()
            write_json(qa / 'admission-preferences.json', {'schema_version': 1, 'command': 'run.exe'})
            with self.assertRaisesRegex(ValueError, 'preferences'):
                admission.load_preferences(qa)

    def test_discovery_is_bounded_direct_and_does_not_trust_recorded_paths(self):
        for index in range(5):
            folder = self.root / f'session-{index}'
            write_json(folder / 'session.json', {'schema_version': 1, 'state': 'ready_paused',
                       'directory': 'elsewhere', 'bridge_directory': 'elsewhere/bridge', 'session_id': str(index)})
        write_json(self.root / 'nested/hidden/session.json', {'schema_version': 1})
        (self.root / 'broken').mkdir()
        (self.root / 'broken/session.json').write_text('broken', encoding='utf-8')
        result = admission.list_sessions(self.root, limit=3)
        self.assertEqual(len(result['sessions']), 3)
        self.assertTrue(result['truncated'])
        for row in result['sessions']:
            self.assertEqual(Path(row['directory']).parent, self.root)
            self.assertEqual(row['bridge_directory'], str(Path(row['directory']) / 'bridge'))
        result = admission.list_sessions(self.root)
        self.assertEqual(len(result['sessions']), 5)
        self.assertEqual(len(result['errors']), 1)

    def test_discovery_rejects_invalid_limit_and_unsupported_records(self):
        for value in (0, 101, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                admission.list_sessions(self.root, limit=value)
        write_json(self.root / 'future/session.json', {'schema_version': 2})
        result = admission.list_sessions(self.root)
        self.assertEqual(result['sessions'], [])
        self.assertEqual(len(result['errors']), 1)


if __name__ == '__main__':
    unittest.main()
