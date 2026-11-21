"""非 Windows 公共入口与本机配置路径的回归。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from gameqakit import profile


class PlatformEntryTests(unittest.TestCase):
    def test_public_entries_do_not_load_windows_modules_on_other_systems(self):
        script = r'''
import importlib.abc
import json
import sys
import platform
platform.machine = lambda: 'x86_64'
platform.mac_ver = lambda: ('13.0', ('', '', ''), 'x86_64')
sys.platform = sys.argv[1]
class NoWindows(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in ('gameqakit.win32', 'gameqakit.inputs', 'gameqakit.desktop'):
            raise ImportError('Windows module imported: ' + fullname)
sys.meta_path.insert(0, NoWindows())
from gameqakit import cli, gui, mcp_server
from gameqakit.platform_support import UnsupportedPlatformError
assert cli.main(['profiles']) == 0
names = {item['name'] for item in mcp_server._tool_list()}
from PyQt6.QtWidgets import QApplication
from gameqakit import profile
qt = QApplication.instance() or QApplication([])
window = gui.App()
window.profile = profile.GameProfile(name='test', proc='Game')
window._apply_stats({'running': False})
assert not window.card_watch.isEnabled()
assert not window.b_admission.isEnabled()
assert window.card_report.isEnabled()
from unittest import mock
with mock.patch.object(gui.QDesktopServices, 'openUrl', return_value=True) as open_url:
    window._open_data()
    assert gui.QUrl.fromLocalFile(str(window.profile.data_path.resolve())) == open_url.call_args.args[0]
dialog = gui.ProfileDialog(window)
assert not dialog.cb_process.isEnabled()
dialog.close()
window.close()
qt.processEvents()
assert {'profile', 'save', 'list_saves', 'watch_report', 'platform_info'} <= names
assert not names.intersection({'capture', 'click', 'key', 'game_prepare', 'background_start'})
try:
    gui.win32.find_window('Game')
except UnsupportedPlatformError:
    pass
else:
    raise AssertionError('Unavailable capture must fail explicitly')
result = mcp_server.handle('tools/call', {'name': 'platform_info', 'arguments': {}})
assert json.loads(result['content'][0]['text'])['platform'] == sys.platform, result
import unittest
from tests.test_capture_session import CaptureSessionTests
suite = unittest.TestSuite(CaptureSessionTests(name) for name in (
    'test_stream_frame_reaches_evidence_with_original_time_and_source',
    'test_changed_source_ends_session_without_archiving_another_window',
    'test_close_failure_is_reported_and_analysis_thread_is_drained'))
assert unittest.TextTestRunner().run(suite).wasSuccessful()
print('PLATFORM_OK')
'''
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, GAMEQAKIT_PROFILES=directory, GAMEQAKIT_DATA=directory,
                       QT_QPA_PLATFORM='offscreen')
            for target in ('darwin', 'linux'):
                with self.subTest(platform=target):
                    run = subprocess.run([sys.executable, '-X', 'utf8', '-B', '-c', script, target],
                                         capture_output=True, text=True, encoding='utf-8',
                                         timeout=25, env=env)
                    self.assertEqual(run.returncode, 0, run.stderr)
                    self.assertIn('PLATFORM_OK', run.stdout)

    def test_frozen_macos_keeps_writable_configuration_outside_app_bundle(self):
        home = Path.home()
        with mock.patch.object(sys, 'platform', 'darwin'), \
             mock.patch.object(sys, 'frozen', True, create=True), \
             mock.patch.object(sys, 'executable', '/Applications/GameQA.app/Contents/MacOS/GameQA'), \
             mock.patch.object(Path, 'home', return_value=home), \
             mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(profile.profiles_dir(),
                             Path.home() / 'Library/Application Support/game-qa-kit/profiles')
            self.assertEqual(profile.GameProfile(name='sample', proc='Game').data_path,
                             Path.home() / 'Library/Application Support/game-qa-kit/data/sample')

    def test_linux_respects_xdg_locations_and_explicit_profile_override(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {'XDG_CONFIG_HOME': str(root / 'config'), 'XDG_DATA_HOME': str(root / 'data')}
            with mock.patch.object(sys, 'platform', 'linux'), \
                 mock.patch.dict(os.environ, env, clear=True):
                self.assertEqual(profile.profiles_dir(), root / 'config/game-qa-kit/profiles')
                self.assertEqual(profile.GameProfile(name='sample', proc='Game').data_path,
                                 root / 'data/game-qa-kit/sample')
                os.environ['GAMEQAKIT_PROFILES'] = str(root / 'custom')
                saved = profile.save_profile(profile.GameProfile(name='sample', proc='Game'))
                self.assertEqual(saved, root / 'custom/sample.json')
                self.assertEqual(profile.load_profile('sample').name, 'sample')


if __name__ == '__main__':
    unittest.main()
