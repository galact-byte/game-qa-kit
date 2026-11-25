"""旧停止与恢复入口的拒绝边界；OS 破坏性调用在红灯阶段替换。"""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from gameqakit import mcp_server, profile, saves, win32


class LegacyIsolationTests(unittest.TestCase):
    def test_stop_without_pid_refuses_instead_of_killing_by_name(self):
        with mock.patch.object(saves, 'is_game_running', return_value=True), \
             mock.patch.object(saves.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')):
            with self.assertRaises(ValueError):
                saves.kill_game('Game')

    def test_stop_without_executable_identity_refuses_even_with_pid(self):
        with mock.patch.object(saves.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')):
            with self.assertRaises(ValueError):
                saves.kill_game('Game', pid=11)

    def test_mcp_stop_without_exact_path_refuses_before_resolving_by_name(self):
        p = profile.GameProfile(name='old', proc='Game')
        with mock.patch.object(mcp_server, 'P', return_value=p), \
             mock.patch.object(saves.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')), \
             mock.patch.object(saves, 'is_game_running', return_value=True):
            with self.assertRaises(ValueError):
                mcp_server.t_kill_game({})

    def test_live_restore_preserves_current_saves_and_snapshot_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / 'saves'
            data.mkdir()
            slot = data / 'Save01.rvdata2'
            slot.write_bytes(b'checkpoint')
            p = profile.GameProfile(name='test', proc='Game', savedata_dir=str(data),
                                    data_dir=str(root / 'qa'), save_globs=['*.rvdata2'])
            saves.snapshot_save('checkpoint', data, p.save_globs, p.saves_dir)
            slot.write_bytes(b'live-progress')
            before = {str(f.relative_to(root)): f.read_bytes() for f in root.rglob('*') if f.is_file()}
            with mock.patch.object(mcp_server, 'P', return_value=p), \
                 mock.patch.object(saves, 'is_game_running', return_value=True):
                with self.assertRaises(RuntimeError):
                    mcp_server.t_load({'name': 'checkpoint'})
            after = {str(f.relative_to(root)): f.read_bytes() for f in root.rglob('*') if f.is_file()}
            self.assertEqual(after, before)

    def test_process_without_visible_window_still_blocks_restore(self):
        output = '"Game.exe","11","Console","1","1,000 K"\r\n'
        with mock.patch.object(saves, 'find_window', side_effect=RuntimeError('no window')), \
             mock.patch.object(saves.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, output, '')):
            self.assertTrue(saves.is_game_running('Game'))

    def test_process_query_failure_is_not_treated_as_no_game(self):
        with mock.patch.object(saves, 'find_window', side_effect=RuntimeError('no window')), \
             mock.patch.object(saves.subprocess, 'run', return_value=subprocess.CompletedProcess([], 5, '', 'access denied')):
            with self.assertRaises(RuntimeError):
                saves.is_game_running('Game')

    def test_unrelated_process_does_not_block_stopped_game_restore(self):
        output = '"Other.exe","12","Console","1","1,000 K"\r\n'
        with mock.patch.object(saves, 'find_window', side_effect=RuntimeError('no window')), \
             mock.patch.object(saves.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, output, '')):
            self.assertFalse(saves.is_game_running('Game'))


class ProcessHandleIsolationTests(unittest.TestCase):
    def child(self):
        process = subprocess.Popen([sys.executable, '-B', '-c', 'import time; time.sleep(120)'],
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        def cleanup():
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)
        self.addCleanup(cleanup)
        return process

    def test_live_process_query_detects_a_real_windowless_child(self):
        process = self.child()
        self.assertIsNone(process.poll())
        self.assertTrue(saves.is_game_running(Path(sys.executable).name))

    def test_stopping_one_of_two_same_name_processes_preserves_the_other(self):
        first, second = self.child(), self.child()
        result = saves.kill_game(Path(sys.executable).name, pid=first.pid, exe_path=sys.executable)
        self.assertTrue(result['killed'])
        self.assertIsNotNone(first.poll())
        self.assertIsNone(second.poll())

    def test_wrong_executable_path_does_not_terminate_either_process(self):
        first, second = self.child(), self.child()
        with self.assertRaises(RuntimeError):
            saves.kill_game('Game', pid=first.pid, exe_path='E:/not-the-target/Game.exe')
        self.assertIsNone(first.poll())
        self.assertIsNone(second.poll())

    def test_exited_target_does_not_fall_back_to_another_same_name_process(self):
        first, second = self.child(), self.child()
        first.terminate()
        first.wait(timeout=5)
        with self.assertRaises((OSError, RuntimeError)):
            saves.kill_game(Path(sys.executable).name, pid=first.pid, exe_path=sys.executable)
        self.assertIsNone(second.poll())


class ProcessHandleFailureTests(unittest.TestCase):
    def test_identity_and_os_failures_close_handle_without_fallback(self):
        cases = [
            ('exited', [0], 'E:/test/Game.exe', True),
            ('wait_failed', [0xffffffff], 'E:/test/Game.exe', True),
            ('unreadable', [258], '', True),
            ('changed_path', [258], 'E:/other/Game.exe', True),
            ('terminate_failed', [258], 'E:/test/Game.exe', False),
            ('exit_timeout', [258, 258], 'E:/test/Game.exe', True),
        ]
        for name, waits, actual, terminated in cases:
            with self.subTest(name=name), \
                 mock.patch.object(win32.kernel32, 'OpenProcess', return_value=12345) as opened, \
                 mock.patch.object(win32.kernel32, 'WaitForSingleObject', side_effect=waits), \
                 mock.patch.object(win32, '_process_path', return_value=actual), \
                 mock.patch.object(win32.kernel32, 'TerminateProcess', return_value=terminated) as terminate, \
                 mock.patch.object(win32.kernel32, 'CloseHandle') as closed:
                with self.assertRaises((OSError, RuntimeError)):
                    saves.kill_game('Game', pid=11, exe_path='E:/test/Game.exe')
                self.assertEqual(opened.call_count, 1)
                closed.assert_called_once_with(12345)
                if name in ('exited', 'wait_failed', 'unreadable', 'changed_path'):
                    terminate.assert_not_called()
                else:
                    terminate.assert_called_once_with(12345, 1)

    def test_open_denied_does_not_attempt_termination(self):
        with mock.patch.object(win32.kernel32, 'OpenProcess', return_value=0), \
             mock.patch.object(win32.kernel32, 'TerminateProcess') as terminate:
            with self.assertRaises(OSError):
                saves.kill_game('Game', pid=11, exe_path='E:/test/Game.exe')
            terminate.assert_not_called()


if __name__ == '__main__':
    unittest.main()
