"""X11 来源、像素布局及独立管道回归；不依赖本地 X server。"""
import importlib.util
import io
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from gameqakit.capture_session import CaptureEnded

SOURCE = {'window_id': 42, 'pid': 7, 'started_at': '100.25',
          'title': 'Fixture', 'name': 'Fixture', 'exe_path': '/tmp/Fixture', 'size': [3, 2],
          'display': ':99', 'connection_id': 'fixture-connection'}


class X11CaptureTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('gameqakit.x11_capture'), 'X11 后端尚未实现')
        from gameqakit import x11_capture
        self.module = x11_capture

    def test_stat_comm_with_spaces_and_parentheses_keeps_start_tick(self):
        fields = ['S'] + ['0'] * 18 + ['987654'] + ['0'] * 10
        self.assertEqual(self.module.parse_start_ticks('12 (a (b) c) ' + ' '.join(fields)), '987654')
        for text in ('12 broken', '12 (x) S', '12 (x) ' + ' '.join(fields[:19] + ['-1'])):
            with self.subTest(text=text), self.assertRaises(CaptureEnded):
                self.module.parse_start_ticks(text)

    def test_identity_checks_display_connection_pid_start_and_executable(self):
        identity = self.module.source_identity(SOURCE)
        for key, value in [('window_id', 43), ('pid', 8), ('started_at', '101'),
                           ('exe_path', '/other'), ('display', ':98'), ('connection_id', 'other')]:
            self.assertNotEqual(identity, self.module.source_identity({**SOURCE, key: value}))
        for key in ('window_id', 'pid', 'started_at', 'exe_path', 'display', 'connection_id'):
            bad = dict(SOURCE); bad.pop(key)
            with self.subTest(key=key), self.assertRaises(CaptureEnded):
                self.module.source_identity(bad)

    def test_pixels_respect_row_padding_byte_order_and_rgb565_masks(self):
        from gameqakit.x11_native import decode_pixels
        image = decode_pixels(bytes.fromhex('0000ff00 00ff0000 00000000 ff000000 ffffff00 00000000'),
                              2, 2, 32, 12, 0, (0xff0000, 0xff00, 0xff))
        self.assertEqual(list(image.getdata()), [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)])
        image = decode_pixels(bytes.fromhex('f80007e0'), 2, 1, 16, 4, 1, (0xf800, 0x7e0, 0x1f))
        self.assertEqual(list(image.getdata()), [(255, 0, 0), (0, 255, 0)])

    def test_pixmap_without_visual_uses_verified_window_visual_masks(self):
        import ctypes as C
        from gameqakit import x11_native as native
        image = native.XImage()
        visual = native.Visual(visual_class=4, red_mask=0xff0000, green_mask=0xff00, blue_mask=0xff)
        attrs = native.Attributes(visual=C.cast(C.pointer(visual), C.c_void_p))
        self.assertEqual(native.image_masks(image, attrs), (0xff0000, 0xff00, 0xff))
        visual.visual_class = 3
        with self.assertRaises(CaptureEnded): native.image_masks(image, attrs)

    def test_bad_layout_and_allocation_budget_fail_before_reading(self):
        from gameqakit.x11_native import decode_pixels
        for args in [(b'', 0, 2, 32, 8, 0, (0xff0000, 0xff00, 0xff)),
                     (b'', 100000, 100000, 32, 400000, 0, (0xff0000, 0xff00, 0xff)),
                     (b'\0' * 8, 2, 1, 32, 4, 0, (0xff0000, 0xff00, 0xff)),
                     (b'\0' * 8, 2, 1, 32, 8, 2, (0xff0000, 0xff00, 0xff)),
                     (b'\0' * 8, 2, 1, 32, 8, 0, (0xff0000, 0xff00, 0xff00))]:
            with self.subTest(args=args[1:]), self.assertRaises(CaptureEnded):
                decode_pixels(*args)

    def test_unvalidated_x11_is_closed_unless_explicitly_enabled(self):
        from gameqakit import platform_support as platform
        with mock.patch.object(sys, 'platform', 'linux'), mock.patch.dict(os.environ,
                {'DISPLAY': ':99', 'XDG_SESSION_TYPE': 'x11'}, clear=True):
            self.assertFalse(platform.x11_capture_supported())
            self.assertFalse(platform.info()['features']['window_capture'])
            with self.assertRaises(platform.UnsupportedPlatformError):
                platform.require_capture()
            os.environ['GAMEQA_X11_VALIDATION'] = '1'
            self.assertTrue(platform.x11_capture_supported())
            os.environ['WAYLAND_DISPLAY'] = 'wayland-0'
            self.assertFalse(platform.x11_capture_supported())

    def test_platform_dispatch_without_loading_native_library(self):
        import subprocess
        script = '''import os, sys, ctypes
sys.platform = 'linux'
os.environ['DISPLAY'] = ':99'
os.environ['XDG_SESSION_TYPE'] = 'x11'
os.environ['GAMEQA_X11_VALIDATION'] = '1'
ctypes.CDLL = lambda *a, **k: (_ for _ in ()).throw(AssertionError('native load in parent'))
from gameqakit import platform_support as p
assert p.info()['features']['window_capture']
assert not p.info()['features']['game_control']
assert p.window_backend.__name__ == 'gameqakit.x11_capture'
'''
        result = subprocess.run([sys.executable, '-X', 'utf8', '-B', '-c', script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_native_protocol_timeout_cancel_and_identity_change(self):
        helper = Path(__file__).parent / 'fixtures/x11_capture_helper.py'
        for scenario in ('normal', 'changed', 'hang', 'oversize', 'bad_png'):
            with self.subTest(scenario=scenario), mock.patch.object(self.module, 'helper_command',
                    return_value=[sys.executable, '-X', 'utf8', '-B', str(helper), scenario]):
                session = self.module.X11CaptureSession(SOURCE, interval=.02, request_timeout=.4)
                try:
                    if scenario == 'normal':
                        frame = self.wait_frame(session)
                        self.assertEqual(frame.image.getpixel((0, 0)), (19, 51, 81))
                        self.assertEqual(frame.source.platform, 'linux')
                        self.assertEqual(frame.captured_at, '2026-01-02T03:04:05.123+00:00')
                    else:
                        with self.assertRaises(CaptureEnded): self.wait_frame(session)
                    stop = threading.Event(); stop.set()
                    start = time.monotonic()
                    self.assertIsNone(session.read(timeout=3, stop_event=stop))
                    self.assertLess(time.monotonic() - start, .1)
                finally:
                    session.close(); session.close()
                    self.assertFalse(session.worker.is_alive())
                    self.assertIsNotNone(session.process.poll())

    def wait_frame(self, session):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            frame = session.read(timeout=.05, stop_event=threading.Event())
            if frame is not None: return frame
        self.fail('No frame from helper')

    def test_ambiguous_selection_and_reused_source_are_not_guessed(self):
        with mock.patch.object(self.module, 'list_windows', return_value=[SOURCE, {**SOURCE, 'window_id': 43}]):
            with self.assertRaisesRegex(RuntimeError, '多个'):
                self.module.find_window('Fixture')
            self.assertEqual(self.module.find_window('Fixture', window_id=42), SOURCE)


if __name__ == '__main__':
    unittest.main()
