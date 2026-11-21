"""采集会话接入真实观察、分析与归档链；不操作桌面或游戏。"""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from gameqakit import watch


class Stream:
    """只替代原生取图，保留观察循环和证据归档。"""
    def __init__(self, reads, *, stop=None, close_error=False):
        self.reads = iter(reads)
        self.stop = stop
        self.close_error = close_error
        self.closed = 0

    def read(self, *, timeout, stop_event):
        assert 0 < timeout <= 0.05
        if self.stop:
            self.stop.set()
        item = next(self.reads)
        if isinstance(item, Exception):
            raise item
        return item

    def close(self):
        self.closed += 1
        if self.close_error:
            raise OSError("关闭采集失败")


class CaptureSessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.out = self.root / "session"
        self.state = self.root / "state"
        self.stop = threading.Event()

    def frame(self, sequence=1, text="请求超时", *, source_id="selected-window"):
        from gameqakit.capture_session import CaptureFrame, CaptureSource
        return CaptureFrame(Image.new("RGB", (80, 60), "gray"),
                            CaptureSource("linux", "fixture", source_id),
                            sequence, f"2026-01-01T00:00:0{sequence}+00:00", text)

    def run_stream(self, stream, **options):
        def event(row):
            if row.get("captured", 0) or row.get("capture_errors", 0):
                self.stop.set()
        return watch.watch(self.out, "unused", self.state, interval=0.01,
                           stop_event=self.stop, on_event=options.pop("on_event", event),
                           capture_session=stream, **options)

    def test_stream_frame_reaches_evidence_with_original_time_and_source(self):
        stream = Stream([self.frame()])
        result = self.run_stream(stream, region=[5, 6, 30, 20], downscale=2)
        row = watch.watch_events(self.state, self.root)["events"][0]
        self.assertIsNone(row["hwnd"])
        self.assertEqual(row["capture_source"], {"platform": "linux", "backend": "fixture",
                                              "source_id": "selected-window", "hwnd": None})
        self.assertEqual(row["captured_at"], "2026-01-01T00:00:01+00:00")
        self.assertEqual(row["source"], "fixture")
        self.assertEqual(row["capture_region"], [5, 6, 30, 20])
        with Image.open(row["image"]) as image:
            self.assertEqual(image.size, (30, 20))
        timeline = json.loads((self.out / "index.jsonl").read_text(encoding="utf-8"))
        with Image.open(self.out / timeline["file"]) as image:
            self.assertEqual(image.size, (15, 10))
        self.assertEqual(timeline["capture_source"], row["capture_source"])
        self.assertEqual(result["capture_status"], "closed")
        self.assertEqual(stream.closed, 1)

    def test_no_new_buffer_is_not_an_image_or_capture_failure(self):
        stream = Stream([None], stop=self.stop)
        result = self.run_stream(stream)
        self.assertEqual(result["captured"], 0)
        self.assertEqual(result["capture_errors"], 0)
        self.assertEqual(result["empty_polls"], 1)
        self.assertEqual(result["saved"], 0)
        self.assertEqual(stream.closed, 1)

    def test_repeated_sequence_does_not_masquerade_as_new_frame(self):
        frame = self.frame(text="")
        stream = Stream([frame, frame, self.frame(2, text="")])
        def event(row):
            if row.get("polls", 0) >= 3:
                self.stop.set()
        result = self.run_stream(stream, on_event=event)
        self.assertEqual(result["captured"], 2)
        self.assertEqual(result["duplicate_frames"], 1)
        self.assertEqual(result["saved"], 1)
        self.assertEqual(result["last_frame_sequence"], 2)
        self.assertEqual(result["last_frame_at"], "2026-01-01T00:00:02+00:00")

    def test_changed_source_ends_session_without_archiving_another_window(self):
        stream = Stream([self.frame(text=""), self.frame(2, source_id="another-window")])
        result = self.run_stream(stream, on_event=lambda row: None)
        self.assertFalse(result["ok"])
        self.assertEqual(result["captured"], 1)
        self.assertIn("来源", result["last_error"])
        self.assertEqual(stream.closed, 1)

    def test_terminal_stream_failure_stops_and_closes_session(self):
        from gameqakit.capture_session import CaptureEnded
        stream = Stream([CaptureEnded("窗口已关闭")])
        result = self.run_stream(stream, on_event=lambda row: None)
        self.assertFalse(result["ok"])
        self.assertEqual(result["capture_errors"], 1)
        self.assertIn("窗口已关闭", result["last_error"])
        self.assertEqual(stream.closed, 1)

    def test_status_write_failure_still_releases_stream(self):
        stream = Stream([self.frame()])
        real_write = watch.write_json
        def write(path, row):
            if Path(path).name == "status.json" and row.get("captured", 0):
                raise OSError("写盘失败")
            real_write(path, row)
        with mock.patch.object(watch, "write_json", side_effect=write):
            with self.assertRaisesRegex(OSError, "写盘失败"):
                self.run_stream(stream)
        self.assertEqual(stream.closed, 1)
        self.assertFalse(any(t.name == "GameQA-Analysis" for t in threading.enumerate()))

    def test_close_failure_is_reported_and_analysis_thread_is_drained(self):
        result = self.run_stream(Stream([self.frame()], close_error=True))
        self.assertFalse(result["ok"])
        self.assertFalse(result["running"])
        self.assertEqual(result["capture_status"], "close_failed")
        self.assertIn("关闭采集失败", result["last_error"])
        self.assertFalse(any(t.name == "GameQA-Analysis" for t in threading.enumerate()))

    def test_precancelled_watch_releases_owned_stream_without_reading(self):
        self.stop.set()
        stream = Stream([])
        result = self.run_stream(stream)
        self.assertEqual(result["polls"], 0)
        self.assertEqual(stream.closed, 1)

    def test_initial_status_failure_still_releases_stream(self):
        stream = Stream([])
        with mock.patch.object(watch, "write_json", side_effect=OSError("状态初始化失败")):
            with self.assertRaisesRegex(OSError, "状态初始化失败"):
                self.run_stream(stream)
        self.assertEqual(stream.closed, 1)
        self.assertFalse(any(t.name == "GameQA-Analysis" for t in threading.enumerate()))

    def test_invalid_backend_frame_ends_without_saving(self):
        stream = Stream([object()])
        result = self.run_stream(stream)
        self.assertFalse(result["ok"])
        self.assertEqual(result["captured"], 0)
        self.assertEqual(result["saved"], 0)
        self.assertIn("无效帧", result["last_error"])
        self.assertEqual(stream.closed, 1)

    def test_stop_request_ends_empty_stream(self):
        stream = Stream([None])
        def event(row):
            if row.get("empty_polls", 0):
                (self.out / "stop.request").touch()
        result = self.run_stream(stream, on_event=event)
        self.assertEqual(result["polls"], 1)
        self.assertEqual(result["captured"], 0)
        self.assertEqual(stream.closed, 1)

    def test_frame_contract_rejects_invalid_provenance(self):
        from gameqakit.capture_session import CaptureFrame, CaptureSource
        with self.assertRaises(ValueError):
            CaptureSource("linux", "fixture", "")
        source = CaptureSource("linux", "fixture", "window")
        for sequence, stamp in ((-1, "2026-01-01T00:00:01+00:00"),
                                (True, "2026-01-01T00:00:01+00:00"),
                                (1, "2026-01-01T00:00:01"), (1, "invalid")):
            with self.subTest(sequence=sequence, stamp=stamp), self.assertRaises(ValueError):
                CaptureFrame(Image.new("RGB", (2, 2)), source, sequence, stamp)

    def test_default_windows_session_refuses_target_switch(self):
        stop = threading.Event()
        def event(row):
            if row.get("polls", 0) >= 3:
                stop.set()
        with mock.patch.object(watch.sys, "platform", "win32"), \
             mock.patch.object(watch, "find_window", side_effect=[123, 456, 456]), \
             mock.patch.object(watch, "grab_image", return_value=Image.new("RGB", (80, 60), "gray")), \
             mock.patch.object(watch, "window_text", return_value=""):
            result = watch.watch(self.out, "test", self.state, interval=0.01,
                                 stop_event=stop, on_event=event, analysis_mode="off")
        self.assertFalse(result["ok"])
        self.assertEqual(result["captured"], 1)
        rows = (self.out / "index.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual([json.loads(row)["hwnd"] for row in rows], [123])


if __name__ == "__main__":
    unittest.main()
