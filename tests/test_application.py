"""便携版入口不能把 Python 子进程命令再次启动成 GUI。"""
import importlib
from pathlib import Path
import sys
import unittest
from unittest import mock


class ApplicationTests(unittest.TestCase):
    def app(self):
        return importlib.import_module('gameqakit.application')

    def test_frozen_worker_preserves_module_arguments_without_opening_gui(self):
        with mock.patch('runpy.run_module') as run:
            original = sys.argv[:]
            self.assertEqual(self.app().main(['-m', 'gameqakit.game_worker', 'request.json', '123']), 0)
            run.assert_called_once_with('gameqakit.game_worker', run_name='__main__', alter_sys=True)
            self.assertEqual(sys.argv, original)

    def test_unapproved_module_and_bad_arguments_are_rejected(self):
        with mock.patch('runpy.run_module') as run:
            with self.assertRaises(ValueError):
                self.app().main(['-m', 'os'])
            with self.assertRaises(ValueError):
                self.app().main(['--unknown'])
            run.assert_not_called()

    def test_mcp_mode_uses_the_existing_server(self):
        with mock.patch('runpy.run_module') as run:
            self.app().main(['--mcp'])
            run.assert_called_once_with('gameqakit.mcp_server', run_name='__main__', alter_sys=True)

    def test_frozen_profiles_and_default_data_are_beside_executable(self):
        from gameqakit import profile
        with mock.patch.object(sys, 'frozen', True, create=True), \
                mock.patch.object(sys, 'executable', 'E:/portable/GameQA.exe'):
            self.assertEqual(profile.profiles_dir(), Path('E:/portable/profiles'))
            with mock.patch.dict('os.environ', {}, clear=True):
                self.assertEqual(profile.GameProfile(name='sample', proc='Game').data_path,
                                 Path('E:/portable/data/sample'))


if __name__ == '__main__':
    unittest.main()
