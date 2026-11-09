"""Qt 离屏交互测试；不向真实游戏发送输入。"""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
from PIL import Image

from gameqakit import gui, profile, sessions
from gameqakit.analysis import EventStore


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.p = profile.GameProfile(name="test", proc="GAMEQA_TEST_NO_SUCH_PROCESS",
                                     data_dir=str(self.root / "data"))
        self.app = gui.App()
        self.app.profile = self.p
        self.addCleanup(self.cleanup)

    def cleanup(self):
        sessions.stop_watch(self.p)
        end = time.monotonic() + 5
        while sessions.watch_status(self.p)["running"] and time.monotonic() < end:
            self.qt.processEvents()
            time.sleep(0.01)
        for child in list(sessions._CHILDREN.values()):
            child.wait(timeout=5)
        self.app.close()
        self.app.deleteLater()
        self.qt.processEvents()
        self.temp.cleanup()

    def pump_until(self, predicate):
        end = time.monotonic() + 5
        while time.monotonic() < end:
            self.qt.processEvents()
            if predicate():
                return
            time.sleep(0.01)
        self.fail("GUI 状态未更新")

    def test_gui_starts_shared_session_and_shows_real_counters(self):
        self.app.e_interval.setText("0.05")
        self.app._toggle_watch()
        self.pump_until(lambda: self.app._watch_running)
        status = sessions.watch_status(self.p)
        self.assertTrue(status["running"])
        self.pump_until(lambda: "采集" in self.app.lb_stats.text())
        self.app._toggle_watch()
        self.pump_until(lambda: not self.app._watch_running)
        self.assertFalse(sessions.watch_status(self.p)["running"])

    def test_new_profile_saves_selected_process_and_keeps_runtime_pid(self):
        selected = profile.GameProfile(name="selected-game", proc="Game", exe_path="E:/games/Game.exe",
                                       target_pid=321, data_dir=str(self.root / "selected-data"))
        directory = self.root / "profiles"
        with mock.patch.object(profile, "_PROFILES_DIR", directory), \
             mock.patch.object(gui, "ProfileDialog") as dialog:
            dialog.return_value.exec.return_value = 1
            dialog.return_value.result_profile.return_value = selected
            self.app._new_profile()
            saved = profile.load_profile("selected-game")
        self.assertEqual(saved.exe_path, selected.exe_path)
        self.assertIsNone(saved.target_pid)
        self.assertEqual(self.app.profile.target_pid, 321)
        self.assertIn("已创建 profile", self.app.txt.toPlainText())

    def test_profile_editing_is_blocked_while_capture_is_running(self):
        self.app._watch_running = True
        with mock.patch.object(gui, "ProfileDialog", side_effect=AssertionError("不应打开配置编辑器")):
            self.app._new_profile()
            self.app._edit_profile()
        self.assertIn("停止", self.app.txt.toPlainText())
        self.assertIs(self.app.profile, self.p)

    def test_capture_starting_during_profile_dialog_prevents_saving(self):
        def accept_while_capture_starts():
            self.app._watch_running = True
            return 1
        for action in (self.app._new_profile, self.app._edit_profile):
            with self.subTest(action=action.__name__):
                self.app._watch_running = False
                with mock.patch.object(gui, "ProfileDialog") as dialog, \
                     mock.patch.object(gui._profile, "load_profile", return_value=self.p), \
                     mock.patch.object(gui._profile, "save_profile") as save:
                    dialog.return_value.exec.side_effect = accept_while_capture_starts
                    dialog.return_value.result_profile.return_value = self.p
                    action()
                save.assert_not_called()
                self.assertIs(self.app.profile, self.p)

    def test_invalid_settings_show_error_without_starting_worker(self):
        self.app.e_interval.setText("0")
        self.app._toggle_watch()
        self.pump_until(lambda: "错误" in self.app.txt.toPlainText() or "无法" in self.app.txt.toPlainText())
        self.assertFalse(self.p.watch_dir.exists())

    def test_action_card_is_keyboard_operable(self):
        self.app.show()
        self.app.card_watch.setFocus()
        self.app.e_interval.setText("0.05")
        QTest.keyClick(self.app.card_watch, Qt.Key.Key_Return)
        self.pump_until(lambda: self.app._watch_running)
        QTest.keyClick(self.app.card_watch, Qt.Key.Key_Space)
        self.pump_until(lambda: not self.app._watch_running)

    def test_suspect_dialog_displays_evidence_and_empty_state(self):
        directory = self.p.watch_dir / "example"
        store = EventStore(directory)
        task = {"hwnd": 123, "captured_at": "2026-01-01T00:00:00", "native_text": "请求超时",
                "capture_region": [0, 0, 160, 90], "image": Image.new("RGB", (160, 90), "gray")}
        store.inspect(task)
        sessions.write_json(self.p.state_dir / "watch_current.json", {"dir": str(directory)})
        dialog = gui.SuspectDialog(self.p, self.app)
        self.addCleanup(dialog.close)
        self.assertEqual(dialog.items.count(), 1)
        self.assertIn("NETWORK_TIMEOUT", dialog.detail.toPlainText())
        self.assertIn("待复核", dialog.detail.toPlainText())
        self.assertFalse(dialog.preview.pixmap().isNull())
        empty = profile.GameProfile(name="empty", proc="x", data_dir=str(self.root / "empty"))
        other = gui.SuspectDialog(empty, self.app)
        self.addCleanup(other.close)
        self.assertEqual(other.items.count(), 0)
        self.assertIn("无", other.summary.text())


if __name__ == "__main__":
    unittest.main()
