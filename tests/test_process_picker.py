"""进程列表与 profile 自动填充；不操作真实键鼠。"""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PyQt6.QtWidgets import QApplication

from gameqakit import gui, profile, win32

TARGET = {"pid": 321, "name": "Game.exe", "exe_path": "E:/games/sample/Game.exe",
          "title": "示例游戏", "hwnd": 555, "client_size": [640, 480]}


class ProcessPickerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def test_selecting_process_fills_identity_without_guessing_save_location(self):
        with mock.patch.object(win32, "list_process_targets", return_value=[TARGET], create=True):
            dialog = gui.ProfileDialog()
            self.addCleanup(dialog.close)
            self.assertEqual(dialog.cb_process.count(), 2)
            dialog.cb_process.setCurrentIndex(1)
            p = dialog.result_profile()
        self.assertEqual(p.proc, "Game")
        self.assertEqual(p.exe_path, "E:/games/sample/Game.exe")
        self.assertEqual(p.target_pid, 321)
        self.assertEqual(p.client_size, [640, 480])
        self.assertIsNone(p.title_substr)
        self.assertIsNone(p.savedata_dir)
        self.assertIn("PID 321", dialog.cb_process.itemText(1))
        self.assertTrue(p.name)

    def test_new_manual_profile_requires_explicit_qa_data_directory(self):
        with mock.patch.object(win32, "list_process_targets", return_value=[TARGET]):
            dialog = gui.ProfileDialog()
            self.addCleanup(dialog.close)
            dialog.cb_process.setCurrentIndex(1)
            with mock.patch.object(gui.QMessageBox, "warning") as warning:
                dialog._on_ok()
                self.assertEqual(dialog.result(), 0)
                warning.assert_called_once()
                self.assertIn("QA", warning.call_args.args[2])
            dialog.e_data.setText("E:/qa/manual-test")
            dialog._on_ok()
            self.assertEqual(dialog.result(), 1)

    def test_new_manual_profile_does_not_silently_overwrite_existing_name(self):
        with tempfile.TemporaryDirectory() as folder, \
             mock.patch.object(profile, "_PROFILES_DIR", Path(folder)), \
             mock.patch.object(win32, "list_process_targets", return_value=[TARGET]):
            profile.save_profile(profile.GameProfile(name="sample", proc="old"))
            dialog = gui.ProfileDialog()
            self.addCleanup(dialog.close)
            dialog.cb_process.setCurrentIndex(1)
            self.assertEqual(dialog.e_name.text(), "sample-2")

    def test_selecting_process_does_not_replace_user_data_fields(self):
        old = profile.GameProfile(name="existing", proc="old", savedata_dir="E:/saves",
                                  save_globs=["*.sav"], data_dir="E:/qa", regions={"text": [0, 0, 10, 10]},
                                  client_size=[100, 100], notes="保留备注")
        with mock.patch.object(win32, "list_process_targets", return_value=[TARGET], create=True):
            dialog = gui.ProfileDialog(existing=old)
            self.addCleanup(dialog.close)
            dialog.cb_process.setCurrentIndex(1)
            p = dialog.result_profile()
        self.assertEqual(p.name, "existing")
        self.assertEqual(p.savedata_dir, "E:/saves")
        self.assertEqual(p.save_globs, ["*.sav"])
        self.assertEqual(p.data_dir, "E:/qa")
        self.assertEqual(p.regions, {"text": [0, 0, 10, 10]})
        self.assertEqual(p.notes, "保留备注")
        self.assertEqual(p.client_size, [640, 480])

    def test_refresh_handles_empty_list_and_process_exit(self):
        with mock.patch.object(win32, "list_process_targets", side_effect=[[TARGET], []], create=True):
            dialog = gui.ProfileDialog()
            self.addCleanup(dialog.close)
            dialog.cb_process.setCurrentIndex(1)
            dialog._refresh_processes()
            p = dialog.result_profile()
        self.assertEqual(dialog.cb_process.count(), 1)
        self.assertIsNone(p.target_pid)
        self.assertIn("未找到", dialog.lb_process_hint.text())
        self.assertEqual(p.exe_path, TARGET["exe_path"])

    def test_manual_process_edit_clears_selected_identity(self):
        with mock.patch.object(win32, "list_process_targets", return_value=[TARGET], create=True):
            dialog = gui.ProfileDialog()
            self.addCleanup(dialog.close)
            dialog.cb_process.setCurrentIndex(1)
            dialog.e_proc.setText("DifferentGame")
            p = dialog.result_profile()
        self.assertIsNone(p.target_pid)
        self.assertIsNone(p.exe_path)

    def test_profile_persists_executable_but_not_session_pid(self):
        p = profile.GameProfile(name="x", proc="Game", exe_path=TARGET["exe_path"], target_pid=321)
        saved = p.to_dict()
        self.assertEqual(saved["exe_path"], TARGET["exe_path"])
        self.assertNotIn("target_pid", saved)
        restored = profile.from_dict(saved)
        self.assertEqual(restored.exe_path, p.exe_path)
        self.assertIsNone(restored.target_pid)

    def test_legacy_profile_still_loads_without_executable_path(self):
        p = profile.from_dict({"name": "x", "proc": "Game"})
        self.assertIsNone(p.exe_path)
        self.assertIsNone(p.target_pid)


class ProcessListTests(unittest.TestCase):
    def test_minimized_game_stays_selectable_without_restoring_it(self):
        def enumerate_windows(callback, _):
            callback(555, 0)
        def pid(_hwnd, value):
            value._obj.value = 321
            return 1
        with mock.patch.object(win32.user32, "EnumWindows", side_effect=enumerate_windows), \
             mock.patch.object(win32.user32, "IsWindowVisible", return_value=True), \
             mock.patch.object(win32.user32, "IsIconic", return_value=True), \
             mock.patch.object(win32.user32, "GetWindowThreadProcessId", side_effect=pid), \
             mock.patch.object(win32, "window_text", return_value="最小化的游戏"), \
             mock.patch.object(win32, "client_size", return_value=(0, 0)), \
             mock.patch.object(win32, "_pid_path", return_value="E:/games/Game.exe"):
            rows = win32.list_process_targets()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["pid"], 321)
        self.assertIsNone(rows[0]["client_size"])
        self.assertTrue(rows[0]["minimized"])


class TargetWindowTests(unittest.TestCase):
    def windows(self):
        rows = {101: (11, "E:/a/Game.exe", "短标题"),
                202: (22, "E:/b/Game.exe", "另一个很长的窗口标题")}
        def enum(callback, _):
            for hwnd in rows:
                callback(hwnd, 0)
        def pid(hwnd, pointer):
            pointer._obj.value = rows[int(hwnd)][0]
            return 1
        def text(hwnd, buffer, _length):
            buffer.value = rows[int(hwnd)][2]
            return len(buffer.value)
        return rows, enum, pid, text

    def test_path_is_strict_and_title_cannot_escape_target_filter(self):
        rows, enum, pid, text = self.windows()
        with mock.patch.object(win32.user32, "EnumWindows", side_effect=enum), \
             mock.patch.object(win32.user32, "IsWindowVisible", return_value=True), \
             mock.patch.object(win32.user32, "GetWindowThreadProcessId", side_effect=pid), \
             mock.patch.object(win32.user32, "GetWindowTextLengthW", return_value=50), \
             mock.patch.object(win32.user32, "GetWindowTextW", side_effect=text), \
             mock.patch.object(win32, "_pid_path", side_effect=lambda p: next(r[1] for r in rows.values() if r[0] == p), create=True):
            self.assertEqual(win32.find_window("Game", "很长", exe_path="E:/a/Game.exe"), 101)
            with self.assertRaises(RuntimeError):
                win32.find_window("Game", exe_path="E:/missing/Game.exe")
            with self.assertRaises(RuntimeError):
                win32.find_window("Game", exe_path="E:/a/Game.exe", pid=22)

    def test_duplicate_instances_require_pid_instead_of_guessing(self):
        rows, enum, pid, text = self.windows()
        with mock.patch.object(win32.user32, "EnumWindows", side_effect=enum), \
             mock.patch.object(win32.user32, "IsWindowVisible", return_value=True), \
             mock.patch.object(win32.user32, "GetWindowThreadProcessId", side_effect=pid), \
             mock.patch.object(win32.user32, "GetWindowTextLengthW", return_value=50), \
             mock.patch.object(win32.user32, "GetWindowTextW", side_effect=text), \
             mock.patch.object(win32, "_pid_path", return_value="E:/a/Game.exe", create=True):
            with self.assertRaisesRegex(RuntimeError, "多个"):
                win32.find_window("Game", exe_path="E:/a/Game.exe")
            self.assertEqual(win32.find_window("Game", exe_path="E:/a/Game.exe", pid=22), 202)


if __name__ == "__main__":
    unittest.main()
