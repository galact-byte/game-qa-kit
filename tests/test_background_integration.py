"""后台窗口代理必须通过现有 MCP 和便携程序入口实际可达。"""
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

from gameqakit import mcp_server


@unittest.skipUnless(sys.platform == 'win32', 'Windows background integration')
class BackgroundIntegrationTests(unittest.TestCase):
    def test_mcp_publishes_all_background_operations_on_windows(self):
        names = {tool['name'] for tool in mcp_server.handle('tools/list', {})['tools']}
        self.assertTrue({'background_start', 'background_status', 'background_capture',
                         'background_input', 'background_stop'} <= names)

    def test_background_operations_are_not_published_on_other_platforms(self):
        for platform in ('darwin', 'linux'):
            with self.subTest(platform=platform), mock.patch.object(sys, 'platform', platform):
                names = {tool['name'] for tool in mcp_server.handle('tools/list', {})['tools']}
                self.assertFalse(any(name.startswith('background_') for name in names))

    def test_start_and_input_require_explicit_confirmation_before_binding_or_input(self):
        for tool in ('background_start', 'background_input'):
            for confirm in (None, False, 1, 'true'):
                with self.subTest(tool=tool, confirm=confirm), self.assertRaisesRegex(ValueError, 'confirm=true'):
                    mcp_server.handle('tools/call', {'name': tool, 'arguments': {'confirm': confirm}})

    def test_status_and_stop_without_a_session_do_not_start_an_agent(self):
        for tool in ('background_status', 'background_stop'):
            response = mcp_server.handle('tools/call', {'name': tool, 'arguments': {}})
            result = json.loads(response['content'][0]['text'])
            self.assertFalse(result['running'])
            self.assertFalse(result['ready'])

    def test_registered_worker_entry_runs_agent_help_without_opening_gui(self):
        response = subprocess.run([sys.executable, '-B', '-X', 'utf8', '-m', 'gameqakit.application',
                                   '-m', 'gameqakit.background_agent', '--help'],
                                  cwd=Path(__file__).resolve().parents[1], capture_output=True,
                                  text=True, encoding='utf-8', timeout=15)
        self.assertEqual(response.returncode, 0, response.stderr)
        self.assertIn('--hwnd', response.stdout)
        self.assertIn('--parent', response.stdout)


if __name__ == '__main__':
    unittest.main()
