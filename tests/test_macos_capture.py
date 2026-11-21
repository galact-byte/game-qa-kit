"""窗口绑定和原生 IPC 的行为检查；使用真实子进程与管道。"""
import importlib.util
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest import mock

from gameqakit.capture_session import CaptureEnded

SOURCE = {'window_id': 42, 'pid': 7, 'started_at': '100.25',
          'title': 'Fixture', 'name': 'Fixture', 'exe_path': '/tmp/Fixture', 'size': [3, 2]}


class MacOSCaptureTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('gameqakit.macos_capture'),
                             'macOS 原生采集会话尚未实现')
        from gameqakit import macos_capture
        self.module = macos_capture
        self.sessions = []

    def tearDown(self):
        for session in getattr(self, 'sessions', []):
            session.close()
            self.assertIsNotNone(session.process.poll(), '辅助进程必须被回收')
            self.assertFalse(session.worker.is_alive(), '读取线程必须退出')

    def session(self, scenario='normal', **options):
        helper = Path(__file__).parent / 'fixtures/macos_capture_helper.py'
        with mock.patch.object(self.module, 'helper_command', return_value=[sys.executable, '-X', 'utf8', '-B', str(helper), scenario]):
            session = self.module.MacOSCaptureSession(SOURCE, interval=.02, **options)
        self.sessions.append(session)
        return session

    def frame(self, session):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            frame = session.read(timeout=.05, stop_event=threading.Event())
            if frame is not None:
                return frame
        self.fail('真实管道未传回采集帧')

    def test_real_pipe_frame_preserves_pixels_identity_and_original_timestamp(self):
        frame = self.frame(self.session())
        self.assertEqual(frame.image.size, (3, 2))
        self.assertEqual(frame.image.getpixel((0, 0)), (19, 51, 81))
        self.assertEqual(frame.source.platform, 'darwin')
        self.assertEqual(frame.source.source_id, '42:7:100.25')
        self.assertIsNone(frame.source.hwnd)
        self.assertEqual(frame.captured_at, '2026-01-02T03:04:05.123+00:00')
        self.assertGreater(frame.sequence, 0)

    def test_pipe_damage_invalid_image_metadata_and_source_change_are_terminal(self):
        for scenario in ['eof', 'oversize', 'bad_png', 'bad_length', 'bad_version', 'changed', 'wrong_dimensions']:
            with self.subTest(scenario=scenario):
                session = self.session(scenario)
                with self.assertRaises(CaptureEnded):
                    self.frame(session)
                session.close()

    def test_executable_change_with_same_pid_and_start_time_is_terminal(self):
        with self.assertRaisesRegex(CaptureEnded, '来源变化'):
            self.frame(self.session('changed_path'))

    def test_permission_failure_has_recovery_message(self):
        with self.assertRaisesRegex(CaptureEnded, '屏幕录制'):
            self.frame(self.session('permission'))

    def test_read_budget_and_cancellation_do_not_wait_for_native_request(self):
        session = self.session('hang')
        started = time.monotonic()
        self.assertIsNone(session.read(timeout=.05, stop_event=threading.Event()))
        self.assertLess(time.monotonic() - started, .2)
        stop = threading.Event()
        stop.set()
        started = time.monotonic()
        self.assertIsNone(session.read(timeout=3, stop_event=stop))
        self.assertLess(time.monotonic() - started, .1)
        session.close()
        session.close()
        with self.assertRaises(CaptureEnded):
            session.read(timeout=.05, stop_event=threading.Event())

    def test_hung_helper_times_out_and_is_reaped(self):
        session = self.session('hang', request_timeout=.2)
        with self.assertRaisesRegex(CaptureEnded, '超时'):
            self.frame(session)
        session.close()
        self.assertIsNotNone(session.process.poll())

    def test_duplicate_native_sequence_is_rejected(self):
        session = self.session('duplicate')
        self.frame(session)
        with self.assertRaises(CaptureEnded):
            self.frame(session)

    def test_list_uses_same_native_protocol_and_reaps_helper(self):
        helper = Path(__file__).parent / 'fixtures/macos_capture_helper.py'
        with mock.patch.object(self.module, 'helper_command', return_value=[sys.executable, '-X', 'utf8', '-B', str(helper), 'normal']):
            self.assertEqual(self.module.list_windows(), [{**SOURCE, 'client_size': SOURCE['size']}])

    def test_ambiguous_lookup_never_picks_an_arbitrary_window(self):
        other = {**SOURCE, 'window_id': 43}
        with mock.patch.object(self.module, 'list_windows', return_value=[SOURCE, other]):
            with self.assertRaisesRegex(RuntimeError, '多个'):
                self.module.find_window('Fixture')
            self.assertEqual(self.module.find_window('Fixture', window_id=42), SOURCE)
        with mock.patch.object(self.module, 'list_windows', return_value=[SOURCE]):
            with self.assertRaises(RuntimeError):
                self.module.find_window('Fixture', pid=8)


if __name__ == '__main__':
    unittest.main()
