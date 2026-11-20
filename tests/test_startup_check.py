"""源码与冻结产物的公共启动自检。"""
from pathlib import Path
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


class StartupCheckTests(unittest.TestCase):
    def test_source_entry_checks_gui_profiles_and_real_mcp_subprocess(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / 'check.json'
            env = dict(os.environ, QT_QPA_PLATFORM='offscreen',
                       GAMEQAKIT_PROFILES=str(Path(directory) / 'profiles'))
            run = subprocess.run(
                [sys.executable, '-X', 'utf8', '-B', '-m', 'gameqakit.application',
                 '--smoke-test', str(report)], env=env, capture_output=True,
                text=True, encoding='utf-8', timeout=30)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertTrue(report.is_file(), '入口必须实际执行自检并写出报告')
            result = json.loads(report.read_text(encoding='utf-8'))
            self.assertTrue(result['ok'], result)
            self.assertFalse(result['frozen'])
            self.assertTrue(result['profile_roundtrip'])
            self.assertTrue(result['save_snapshot'])
            self.assertTrue(result['evidence_report'])
            self.assertGreater(result['mcp_tool_count'], 0)
            self.assertGreater(result['qt_window_size'][0], 0)
            self.assertTrue(report.with_suffix('.png').is_file())
            self.assertEqual(list((Path(directory) / 'profiles').glob('*')), [])

    def test_normal_gui_entry_keeps_window_alive_until_event_loop_exits(self):
        script = '''
import sys
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication
from gameqakit import gui
original = QApplication.exec
def event_loop(app):
    assert any(w.isVisible() for w in app.topLevelWidgets()), '主窗口在事件循环前已销毁'
    QTimer.singleShot(50, app.quit)
    return original()
QApplication.exec = event_loop
gui.main()
'''
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, QT_QPA_PLATFORM='offscreen', GAMEQAKIT_PROFILES=directory)
            run = subprocess.run([sys.executable, '-X', 'utf8', '-B', '-c', script], env=env,
                                 capture_output=True, text=True, encoding='utf-8', timeout=10)
            self.assertEqual(run.returncode, 0, run.stderr)

    def test_binary_only_check_records_failure_when_run_from_source(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / 'check.json'
            script = ('from gameqakit.startup_check import check; '
                      'import sys; from pathlib import Path; '
                      'check(Path(sys.argv[1]), require_frozen=True)')
            run = subprocess.run([sys.executable, '-X', 'utf8', '-B', '-c', script, str(report)],
                                 capture_output=True, text=True, timeout=10)
            self.assertNotEqual(run.returncode, 0)
            self.assertTrue(report.is_file(), '失败也必须留下自检报告')
            self.assertFalse(json.loads(report.read_text(encoding='utf-8'))['ok'])

    def test_unix_frozen_startup_failure_writes_log_without_win32_api(self):
        spec = importlib.util.spec_from_file_location(
            'portable_entry_under_test',
            Path(__file__).resolve().parents[1] / 'tools/portable_entry.py')
        portable = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(portable)
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / 'GameQA'
            with mock.patch.object(portable.sys, 'platform', 'linux'), \
                    mock.patch.object(portable.sys, 'executable', str(executable)), \
                    mock.patch.object(portable.sys, 'argv', [str(executable)]), \
                    mock.patch('gameqakit.application.main', side_effect=RuntimeError('boom')), \
                    contextlib.redirect_stderr(io.StringIO()) as stderr:
                self.assertEqual(portable.main(), 1)
            log = Path(directory) / 'startup-error.log'
            self.assertIn('启动失败，请查看：', stderr.getvalue())
            self.assertIn('RuntimeError', log.read_text(encoding='utf-8'))

