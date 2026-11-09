"""疑点规则、失败恢复与采集生命周期的回归测试。"""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from gameqakit import analysis, watch, sessions


class RuleTests(unittest.TestCase):
    def test_traditional_chinese_and_english_ui_are_not_automatically_defects(self):
        for text in ("諜報 難度 歡迎", "OK Cancel Menu", "正常游戏画面", "エ"):
            with self.subTest(text=text):
                self.assertEqual(analysis.classify(text), [])

    def test_kana_and_replacement_characters_are_candidates_with_evidence(self):
        self.assertEqual(analysis.classify("設定 メニュー")[0],
                         {"category": "POSSIBLE_JAPANESE", "evidence": "メニュー"})
        self.assertEqual(analysis.classify("文字�损坏")[0]["category"], "POSSIBLE_ENCODING")


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_status_write_failure_still_shuts_down_analysis_thread(self):
        created = []
        real = watch.FrameAnalyzer
        def create(*args):
            analyzer = real(*args)
            created.append(analyzer)
            return analyzer
        def write(path, value):
            if Path(path).name == "status.json" and value.get("captured", 0):
                raise OSError("测试磁盘写入失败")
            sessions.write_json(path, value)
        with mock.patch.object(watch, "FrameAnalyzer", side_effect=create), \
             mock.patch.object(watch, "write_json", side_effect=write), \
             mock.patch.object(watch, "find_window", return_value=123), \
             mock.patch.object(watch, "window_text", return_value="Game"), \
             mock.patch.object(watch, "grab_image", return_value=Image.new("RGB", (160, 90), "gray")):
            try:
                with self.assertRaises(OSError):
                    watch.watch(self.root / "session", "test", self.root / "state", on_event=lambda _: None)
                alive = created[0].thread.is_alive()
            finally:
                for analyzer in created:
                    analyzer.close()
        self.assertFalse(alive, "状态落盘失败后分析线程仍在运行")

    def test_failed_evidence_save_is_retried_even_on_static_frame(self):
        stop = threading.Event()
        timer = threading.Timer(2, stop.set)
        timer.start()
        self.addCleanup(timer.cancel)
        original_save = Image.Image.save
        failures = []
        def save(image, path, *args, **kwargs):
            if Path(path).name == "raw.png" and not failures:
                failures.append(True)
                raise OSError("模拟暂时写盘失败")
            return original_save(image, path, *args, **kwargs)
        def event(row):
            if row.get("kind") == "suspect":
                stop.set()
        with mock.patch.object(watch, "find_window", return_value=123), \
             mock.patch.object(watch, "window_text", return_value="请求超时"), \
             mock.patch.object(watch, "grab_image", return_value=Image.new("RGB", (160, 90), "gray")), \
             mock.patch.object(Image.Image, "save", new=save):
            result = watch.watch(self.root / "session", "test", self.root / "state",
                                 interval=0.01, stop_event=stop, on_event=event)
        self.assertEqual(result["analysis_errors"], 1)
        self.assertEqual(result["suspects"], 1)
        page = watch.watch_events(self.root / "state", self.root)
        self.assertEqual(page["total"], 1)

    def test_existing_session_is_not_overwritten(self):
        directory = self.root / "session"
        directory.mkdir()
        evidence = directory / "index.jsonl"
        evidence.write_text("original", encoding="utf-8")
        with self.assertRaises(ValueError):
            watch.watch(directory, "test", self.root / "state", on_event=lambda _: None)
        self.assertEqual(evidence.read_text(encoding="utf-8"), "original")

    def test_off_mode_preserves_frames_without_loading_analysis(self):
        stop = threading.Event()
        def event(row):
            if row.get("captured", 0):
                stop.set()
        with mock.patch.object(watch, "find_window", return_value=123), \
             mock.patch.object(watch, "window_text", return_value="请求超时"), \
             mock.patch.object(watch, "grab_image", return_value=Image.new("RGB", (160, 90), "gray")):
            result = watch.watch(self.root / "session", "test", self.root / "state", analysis_mode="off",
                                 stop_event=stop, on_event=event)
        self.assertEqual(result["saved"], 1)
        self.assertEqual(result["analyzed"], 0)
        self.assertEqual(result["analysis_status"], "disabled")


if __name__ == "__main__":
    unittest.main()
