"""窗口绑定和原生 IPC 的行为检查；使用真实子进程与管道。"""
import importlib.util
from pathlib import Path
import sys
import tempfile
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
            if session.process is not None:
                self.assertIsNotNone(session.process.poll(), '辅助进程必须被回收')
            self.assertFalse(session.worker.is_alive(), '读取线程必须退出')

    def session(self, scenario='normal', *, helper_args=(), **options):
        helper = Path(__file__).parent / 'fixtures/macos_capture_helper.py'
        with mock.patch.object(self.module, 'helper_command', return_value=[sys.executable, '-X', 'utf8', '-B', str(helper), scenario, *helper_args]):
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

    def test_fresh_helper_rejects_invalid_sequence(self):
        with self.assertRaisesRegex(CaptureEnded, '序号'):
            self.frame(self.session('wrong_sequence'))

    def test_session_sequences_increase_across_fresh_helpers(self):
        session = self.session()
        previous = self.frame(session)
        previous_process = session.process
        current = self.frame(session)
        self.assertGreater(current.sequence, previous.sequence)
        self.assertIsNot(session.process, previous_process)
        self.assertIsNotNone(previous_process.poll())

    def test_observation_and_screenshot_never_overlap_native_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            first = self.session('exclusive', helper_args=[directory])
            second = self.session('exclusive', helper_args=[directory])
            try:
                for _ in range(3):
                    self.assertIsNotNone(self.frame(first))
                    self.assertIsNotNone(self.frame(second))
            finally:
                first.close()
                second.close()

    def test_busy_native_slot_obeys_timeout_and_releases_waiter_on_close(self):
        from gameqakit.sessions import SessionLease
        with SessionLease(self.module._capture_lock_path(SOURCE)):
            blocked = self.session(request_timeout=.2)
            with self.assertRaisesRegex(CaptureEnded, '超时'):
                self.frame(blocked)
            blocked.close()
            waiting = self.session()
            started = time.monotonic()
            waiting.close()
            self.assertLess(time.monotonic() - started, .5)
        self.assertIsNotNone(self.frame(self.session()))

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
