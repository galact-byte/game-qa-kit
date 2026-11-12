"""采集、异步筛选和证据归档的行为测试，不操作真实键鼠。"""
from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from gameqakit import watch


def wait_until(predicate, timeout=3):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("等待条件超时")


class WatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.out = self.root / "watch" / "session"
        self.state = self.root / "state"

    def run_frames(self, texts, *, ocr=None, frames=None, **options):
        stop = threading.Event()
        images = frames or [Image.new("RGB", (160, 90), "gray") for _ in texts]
        index = 0

        def grab(_hwnd):
            nonlocal index
            image = images[min(index, len(images) - 1)]
            index += 1
            return image.copy()

        def native(_hwnd):
            return texts[min(index - 1, len(texts) - 1)]

        def event(obj):
            if obj.get("kind") == "stats" and (obj.get("captured", 0) >= len(texts)
                                                 or obj.get("capture_errors", 0)):
                stop.set()

        with mock.patch.object(watch, "find_window", return_value=123), \
             mock.patch.object(watch, "grab_image", side_effect=grab), \
             mock.patch.object(watch, "window_text", side_effect=native, create=True), \
             mock.patch("gameqakit.analysis.load_ocr", return_value=ocr):
            return watch.watch(self.out, "test", self.state, interval=0.01,
                               stop_event=stop, on_event=event, **options)

    def test_invalid_options_do_not_create_session(self):
        stopped = threading.Event()
        stopped.set()
        for kwargs in ({"interval": 0}, {"interval": float("nan")},
                       {"interval": float("inf")}, {"threshold": -1},
                       {"threshold": 65}, {"downscale": 0},
                       {"region": [0, 0, 1]}, {"analysis_mode": "unknown"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                watch.watch(self.out, "test", self.state, stop_event=stopped,
                            on_event=lambda _: None, **kwargs)
            self.assertFalse(self.out.exists())

    def test_native_error_is_archived_once_with_original_pixels(self):
        result = self.run_frames(["FileNotFoundError: missing.dat"] * 3)
        rows = watch.watch_events(self.state, self.out.parent)
        self.assertEqual(len(rows["events"]), 1)
        item = rows["events"][0]
        self.assertEqual(item["findings"][0]["category"], "FILE_NOT_FOUND")
        self.assertEqual(item["source"], "Win32")
        self.assertEqual(item["hwnd"], 123)
        self.assertEqual(item["capture_region"], [0, 0, 160, 90])
        self.assertTrue(Path(item["image"]).is_file())
        with Image.open(item["image"]) as image:
            self.assertEqual(image.size, (160, 90))
        self.assertEqual(result["captured"], 3)
        self.assertEqual(result["suspects"], 1)
        self.assertFalse(result["running"])

    def test_text_change_is_analyzed_even_if_dhash_does_not_change(self):
        result = self.run_frames(["普通画面", "请求超时", "文件不存在"])
        rows = watch.watch_events(self.state, self.out.parent)["events"]
        self.assertEqual([row["findings"][0]["category"] for row in rows],
                         ["NETWORK_TIMEOUT", "FILE_NOT_FOUND"])
        self.assertEqual(result["saved"], 1)
        self.assertEqual(result["analyzed"], 3)

    def test_error_can_reappear_after_clear(self):
        self.run_frames(["请求超时", "正常", "请求超时"])
        rows = watch.watch_events(self.state, self.out.parent)["events"]
        self.assertEqual(len(rows), 2)

    def test_ocr_kana_is_suspicion_not_confirmed_defect(self):
        def ocr(_image):
            return "これはテストです"
        self.run_frames(["Game"], ocr=ocr, analysis_mode="ocr")
        item = watch.watch_events(self.state, self.out.parent)["events"][0]
        self.assertEqual(item["findings"][0]["category"], "POSSIBLE_JAPANESE")
        self.assertEqual(item["verdict"], "待复核")
        self.assertEqual(item["ocr_text"], "これはテストです")
        self.assertEqual(item["source"], "Win32+OCR")

    def test_missing_optional_ocr_keeps_recording_and_reports_degradation(self):
        with mock.patch("gameqakit.analysis.load_ocr", side_effect=ImportError("未安装 OCR")):
            # run_frames patches the loader, so run directly for this branch.
            stop = threading.Event()
            def event(obj):
                if obj.get("captured", 0):
                    stop.set()
            with mock.patch.object(watch, "find_window", return_value=123), \
                 mock.patch.object(watch, "grab_image", return_value=Image.new("RGB", (160, 90), "gray")), \
                 mock.patch.object(watch, "window_text", return_value="Game", create=True):
                result = watch.watch(self.out, "test", self.state, interval=0.01,
                                     analysis_mode="ocr", stop_event=stop, on_event=event)
        self.assertEqual(result["analysis_status"], "degraded")
        self.assertIn("未安装 OCR", result["analysis_warning"])
        self.assertEqual(result["saved"], 1)

    def test_slow_analysis_does_not_block_capture_and_backlog_is_visible(self):
        entered, release, stop = threading.Event(), threading.Event(), threading.Event()
        captured = []
        observed = []
        errors = []
        def ocr(_image):
            entered.set()
            release.wait(3)
            return ""
        def grab(_hwnd):
            captured.append(len(captured))
            return Image.new("RGB", (160, 90), (len(captured) % 255, 20, 20))
        def run():
            try:
                watch.watch(self.out, "test", self.state, interval=0.01,
                            analysis_mode="ocr", stop_event=stop,
                            on_event=lambda value: observed.append(value) if value.get("kind") == "stats" else None)
            except Exception as exc:
                errors.append(exc)
        with mock.patch.object(watch, "find_window", return_value=123), \
             mock.patch.object(watch, "grab_image", side_effect=grab), \
             mock.patch.object(watch, "window_text", return_value="Game", create=True), \
             mock.patch("gameqakit.analysis.load_ocr", return_value=ocr):
            thread = threading.Thread(target=run)
            thread.start()
            try:
                self.assertTrue(entered.wait(2))
                wait_until(lambda: observed and observed[-1].get("captured", 0) >= 10)
                status = observed[-1]
                self.assertGreater(status["dropped"], 0)
                self.assertLessEqual(status["queue_depth"], 4)
            finally:
                stop.set()
                release.set()
                thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])

    def test_capture_failures_are_visible_and_stop_is_interruptible(self):
        stop = threading.Event()
        timer = threading.Timer(1.5, stop.set)
        timer.start()
        self.addCleanup(timer.cancel)
        def event(obj):
            if obj.get("capture_errors", 0):
                stop.set()
        with mock.patch.object(watch, "find_window", side_effect=RuntimeError("窗口不可截图")):
            start = time.monotonic()
            result = watch.watch(self.out, "test", self.state, interval=30,
                                 stop_event=stop, on_event=event)
        self.assertLess(time.monotonic() - start, 1)
        self.assertEqual(result["capture_errors"], 1)
        self.assertIn("窗口不可截图", result["last_error"])

    def test_frame_names_do_not_collide_inside_one_second(self):
        frames = []
        for flip in (False, True, False):
            image = Image.new("RGB", (160, 90), "white")
            for x in range(80):
                for y in range(90):
                    image.putpixel((x if flip else 159 - x, y), (0, 0, 0))
            frames.append(image)
        result = self.run_frames(["Game"] * 3, frames=frames, threshold=0)
        self.assertEqual(result["saved"], 3)
        self.assertEqual(len(list(self.out.glob("watch_*.png"))), 3)

    def test_report_lists_suspects_and_still_reads_old_sessions(self):
        self.run_frames(["请求超时"])
        result = watch.watch_report(self.state, self.out.parent)
        text = Path(result["report"]).read_text(encoding="utf-8")
        self.assertIn("自动疑点", text)
        self.assertIn("NETWORK_TIMEOUT", text)
        self.assertIn("待复核", text)
        self.assertEqual(result["suspects"], 1)
        (self.out / "events").rename(self.out / "old_events")
        self.assertEqual(watch.watch_report(self.state, self.out.parent)["suspects"], 0)

    def test_event_query_has_bounded_pagination(self):
        self.run_frames(["请求超时", "文件不存在", "正常", "请求超时"])
        result = watch.watch_events(self.state, self.out.parent, limit=1, offset=1)
        self.assertEqual(result["total"], 3)
        self.assertEqual(len(result["events"]), 1)
        self.assertEqual(result["events"][0]["findings"][0]["category"], "FILE_NOT_FOUND")
        for options in ({"limit": 0}, {"limit": 101}, {"offset": -1}):
            with self.assertRaises(ValueError):
                watch.watch_events(self.state, self.out.parent, **options)


if __name__ == "__main__":
    unittest.main()
