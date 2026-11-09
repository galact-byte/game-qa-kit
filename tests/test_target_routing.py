"""选定进程后的目标路由；输入与结束进程均替换 OS 边界，不操作真实程序。"""
from __future__ import annotations

import contextlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from gameqakit import inputs, mcp_server, profile, saves, sessions, win32
import test_process_picker as fixtures


class TargetRoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.p = profile.GameProfile(name="a", proc="Game", exe_path="E:/a/Game.exe",
                                     target_pid=11, data_dir=str(self.root))
        rows, enum, pid, text = fixtures.TargetWindowTests().windows()
        self.stack = self.enterContext(contextlib.ExitStack())
        for target, name, kwargs in (
            (win32.user32, "EnumWindows", {"side_effect": enum}),
            (win32.user32, "IsWindowVisible", {"return_value": True}),
            (win32.user32, "GetWindowThreadProcessId", {"side_effect": pid}),
            (win32.user32, "GetWindowTextLengthW", {"return_value": 50}),
            (win32.user32, "GetWindowTextW", {"side_effect": text}),
            (win32, "_pid_path", {"side_effect": lambda p: next(r[1] for r in rows.values() if r[0] == p)}),
            (mcp_server, "_PROFILE", {"new": self.p}),
        ):
            self.stack.enter_context(mock.patch.object(target, name, **kwargs))

    def call(self, name, arguments=None):
        response = mcp_server.handle("tools/call", {"name": name, "arguments": arguments or {}})
        return json.loads(response["content"][0]["text"])

    def test_capture_uses_selected_executable_not_longest_other_game_title(self):
        def capture(hwnd, path, region=None):
            Image.new("RGB", (320, 200), "green" if hwnd == 101 else "red").save(path)
            return (320, 200)
        with mock.patch.object(win32, "capture", side_effect=capture):
            result = self.call("capture", {"out": str(self.root / "capture.png")})
        self.assertEqual(result["hwnd"], 101)
        with Image.open(result["out"]) as image:
            self.assertEqual(image.getpixel((0, 0)), (0, 128, 0))

    def test_key_uses_same_selected_window_without_system_input(self):
        def foreground(hwnd):
            if hwnd != 101:
                raise RuntimeError("输入选中了另一个游戏")
            return {"confirmed": True, "fast_path": True, "waited_ms": 0, "prev": hwnd}
        with mock.patch.object(inputs, "client_size", return_value=(640, 480)), \
             mock.patch.object(inputs, "acquire_foreground", side_effect=foreground), \
             mock.patch.object(inputs, "_send_key", return_value=1), \
             mock.patch.object(inputs._t, "sleep"):
            result = self.call("key", {"vk": 13, "confirm": True, "force": True})
        self.assertTrue(result["foreground_confirmed"])

    def test_kill_selected_profile_targets_pid_not_all_game_executables(self):
        def run(command, **_kwargs):
            correct = command == ["taskkill", "/F", "/PID", "11"]
            return subprocess.CompletedProcess(command, 0 if correct else 5, stdout="", stderr="")
        with mock.patch.object(saves.subprocess, "run", side_effect=run):
            result = self.call("kill_game", {"confirm": True})
        self.assertTrue(result["killed"])
        self.assertEqual(result["pid"], 11)

    def test_mcp_can_list_candidates_read_only(self):
        with mock.patch.object(win32, "list_process_targets", return_value=[fixtures.TARGET]):
            rows = self.call("processes")
        self.assertEqual(rows["processes"][0]["pid"], 321)
        self.assertFalse(self.p.watch_dir.exists())

    def test_mcp_reuses_live_gui_session_pid_when_profile_has_multiple_instances(self):
        self.p.target_pid = None
        directory = self.root / "watch" / "session"
        sessions.write_json(self.p.state_dir / "watch_current.json", {"dir": str(directory)})
        sessions.write_json(directory / "status.json", {"running": True, "watch": "started",
                            "out": str(directory), "target_pid": 11, "target_exe_path": self.p.exe_path})
        with sessions.SessionLease(self.p.state_dir / "watch.lock"):
            self.assertEqual(win32.profile_target(self.p)["pid"], 11)


if __name__ == "__main__":
    unittest.main()
