"""固定原生接入的首图、暂停与副本数据处置边界。"""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from PIL import Image
from plugins.native_input import session


class NativeSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bridge = self.root / 'bridge'
        (self.bridge / 'screenshots').mkdir(parents=True)
        self.image = self.bridge / 'screenshots/startup.png'
        image = Image.new('RGB', (50, 40), 'red')
        image.save(self.image)

    def test_ready_requires_real_connection_pause_release_and_valid_first_image(self):
        valid = dict(connected=True, paused=True, held=[], observation_seq=1, session_id='a')
        self.assertEqual(session.validate_ready(self.root, valid)['screenshot'], str(self.image))
        for change in ({'connected': False}, {'paused': False}, {'held': ['left']},
                       {'observation_seq': 0}, {'session_id': ''}):
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                session.validate_ready(self.root, {**valid, **change})

    def test_black_or_missing_first_image_is_never_ready(self):
        valid = dict(connected=True, paused=True, held=[], observation_seq=1, session_id='a')
        Image.new('RGB', (50, 40), 'black').save(self.image)
        with self.assertRaisesRegex(RuntimeError, 'image'):
            session.validate_ready(self.root, valid)
        self.image.unlink()
        with self.assertRaises((RuntimeError, FileNotFoundError)):
            session.validate_ready(self.root, valid)

    def test_baseline_only_runtime_can_be_reclaimed_and_unknown_progress_is_retained(self):
        for extra in (False, True):
            with self.subTest(extra=extra):
                runtime = self.root / ('progress' if extra else 'unchanged')
                runtime.mkdir()
                (runtime / 'data.bin').write_bytes(b'fixed')
                before = {'data.bin': session.file_digest(runtime / 'data.bin')}
                if extra:
                    (runtime / 'new-progress.dat').write_bytes(b'keep')
                result = session.reclaim_runtime(runtime, before, safe_to_remove=True)
                self.assertEqual(result['runtime_retained'], extra)
                self.assertEqual(runtime.exists(), extra)
                if extra:
                    self.assertEqual((runtime / 'new-progress.dat').read_bytes(), b'keep')

    def test_uncertain_target_exit_or_changed_source_prevents_removal(self):
        runtime = self.root / 'runtime'
        runtime.mkdir()
        before = {}
        result = session.reclaim_runtime(runtime, before, safe_to_remove=False)
        self.assertTrue(result['runtime_retained'])
        self.assertTrue(runtime.exists())


if __name__ == '__main__':
    unittest.main()
