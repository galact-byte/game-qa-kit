"""冻结自检的控制台子进程不得创建终端窗口。"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from gameqakit import packaging_check


@unittest.skipUnless(sys.platform == 'win32', 'Windows 控制台创建约定')
class PackagingConsoleTests(unittest.TestCase):
    def test_mcp_self_check_disables_console_window_creation(self):
        class ReachedMcpLaunch(Exception):
            pass

        launches = []

        def run(argv, **kwargs):
            if argv[1:2] == ['--worker-self-test']:
                Path(argv[2]).write_text(json.dumps({'frozen': True}), encoding='utf-8')
                return subprocess.CompletedProcess(argv, 0)
            if Path(argv[0]).name == 'GameQA-MCP.exe':
                launches.append(kwargs)
                raise ReachedMcpLaunch
            self.fail(f'Unexpected subprocess: {argv}')

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.object(sys, 'frozen', True, create=True), \
                    mock.patch.object(sys, 'executable', str(root / 'GameQA.exe')), \
                    mock.patch.object(packaging_check.subprocess, 'run', side_effect=run):
                with self.assertRaises(ReachedMcpLaunch):
                    packaging_check.check(root / 'self-test.json')
            self.assertEqual(len(launches), 1)
            self.assertTrue(launches[0].get('creationflags', 0) & subprocess.CREATE_NO_WINDOW,
                            'GUI 自检必须禁止 MCP 子进程创建控制台窗口')
            self.assertTrue(launches[0]['capture_output'])
            messages = [json.loads(line) for line in launches[0]['input'].splitlines()]
            self.assertEqual([message['method'] for message in messages], ['initialize', 'tools/list'])


if __name__ == '__main__':
    unittest.main()
