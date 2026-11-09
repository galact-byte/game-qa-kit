"""真实后台工作进程及 MCP 生命周期测试；目标是不存在的游戏，不发送输入。"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from gameqakit import profile, sessions, mcp_server

from test_monitoring import wait_until


def wait_worker_exit(pid):
    """MCP 创建的工作进程不在本测试进程的 Popen 表中；等待其日志句柄关闭。"""
    if os.name != "nt" or not pid:
        return
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x00100000, False, pid)
    if not handle:
        if ctypes.get_last_error() == 87:  # 已退出的 PID
            return
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if kernel.WaitForSingleObject(handle, 5000) != 0:
            raise TimeoutError(f"测试工作进程 {pid} 未在 5 秒内退出")
    finally:
        kernel.CloseHandle(handle)


@unittest.skipUnless(os.name == "nt", "Windows 文件共享语义")
class AtomicStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "status.json"
        sessions.write_json(self.path, {"sequence": 1})

    def test_short_lived_reader_does_not_break_atomic_status_update(self):
        reader = self.path.open("r", encoding="utf-8")
        release = threading.Timer(0.05, reader.close)
        release.start()
        try:
            sessions.write_json(self.path, {"sequence": 2})
        finally:
            reader.close()
            release.join()
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {"sequence": 2})
        self.assertFalse(list(self.path.parent.glob("*.part")))

    def test_short_exclusive_writer_does_not_break_state_query(self):
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                      ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.CreateFileW(str(self.path), 0x40000000, 0, None, 3, 0, None)
        self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
        release = threading.Timer(0.03, lambda: kernel.CloseHandle(handle))
        release.start()
        try:
            self.assertEqual(sessions.read_json(self.path), {'sequence': 1})
        finally:
            release.join()

    def test_short_replacement_gap_does_not_report_disconnected(self):
        previous = self.path.with_suffix('.old')
        self.path.rename(previous)
        release = threading.Timer(0.03, lambda: previous.rename(self.path))
        release.start()
        try:
            self.assertEqual(sessions.read_json(self.path), {'sequence': 1})
        finally:
            release.join()

    def test_missing_state_still_fails_after_read_retry_deadline(self):
        self.path.unlink()
        with self.assertRaises(FileNotFoundError):
            sessions.read_json(self.path)

    def test_persistent_reader_error_preserves_old_state_and_removes_temporary_file(self):
        with self.path.open("r", encoding="utf-8"):
            with self.assertRaises(PermissionError):
                sessions.write_json(self.path, {"sequence": 2})
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {"sequence": 1})
        self.assertFalse(list(self.path.parent.glob("*.part")))


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.p = profile.GameProfile(name="test", proc="GAMEQA_TEST_NO_SUCH_PROCESS",
                                     data_dir=str(self.root / "data"))
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.stop_worker)

    def stop_worker(self):
        if hasattr(sessions, "stop_watch"):
            worker_pid = sessions.watch_status(self.p).get("pid")
            sessions.stop_watch(self.p)
            wait_until(lambda: not sessions.watch_status(self.p)["running"], timeout=5)
            # 采集停止到解释器退出有很短的间隔；先回收测试拥有的子进程再删临时日志。
            for child in list(sessions._CHILDREN.values()):
                child.wait(timeout=5)
            wait_worker_exit(worker_pid)
            sessions.watch_status(self.p)

    def test_start_is_shared_and_duplicate_does_not_spawn_second_session(self):
        first = sessions.start_watch(self.p, interval=0.05)
        second = sessions.start_watch(self.p, interval=0.05)
        self.assertTrue(first["running"])
        self.assertEqual(first["out"], second["out"])
        self.assertTrue(second["already_running"])
        wait_until(lambda: sessions.watch_status(self.p).get("capture_errors", 0) >= 1)
        status = sessions.watch_status(self.p)
        self.assertIn("找不到", status["last_error"])
        self.assertEqual(len(list(self.p.watch_dir.iterdir())), 1)

    def test_stop_is_idempotent_and_restart_creates_new_session(self):
        first = sessions.start_watch(self.p, interval=30)
        requested = sessions.stop_watch(self.p)
        self.assertIn(requested["watch"], ("stopping", "stopped"))
        wait_until(lambda: not sessions.watch_status(self.p)["running"])
        again = sessions.stop_watch(self.p)
        self.assertFalse(again["running"])
        second = sessions.start_watch(self.p, interval=0.05, analysis_mode="off")
        self.assertNotEqual(first["out"], second["out"])
        self.assertTrue((Path(first["out"]) / "status.json").exists())

    def test_invalid_start_has_no_output_side_effects(self):
        for options in ({"interval": -1}, {"threshold": 100}, {"analysis_mode": "yolo"}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                sessions.start_watch(self.p, **options)
        self.assertFalse(self.p.data_path.exists())

    def test_status_is_visible_from_another_process(self):
        started = sessions.start_watch(self.p, interval=0.05)
        code = ("import json; from gameqakit import profile, sessions; "
                f"p=profile.from_dict(json.loads({json.dumps(self.p.to_dict())!r})); "
                "print(json.dumps(sessions.watch_status(p)))")
        result = subprocess.run([sys.executable, "-c", code], capture_output=True,
                                text=True, encoding="utf-8", timeout=5, check=True)
        status = json.loads(result.stdout)
        self.assertEqual(status["out"], started["out"])
        self.assertTrue(status["running"])

    def test_lock_is_released_when_owner_process_exits(self):
        path = self.root / "test.lock"
        code = ("import os; from gameqakit.sessions import SessionLease; "
                f"lease=SessionLease({str(path)!r}); lease.__enter__(); os._exit(0)")
        subprocess.run([sys.executable, "-c", code], check=True, timeout=5)
        with sessions.SessionLease(path):
            with self.assertRaises(RuntimeError):
                with sessions.SessionLease(path):
                    pass
        with sessions.SessionLease(path):
            pass

    def test_stale_running_status_is_not_reported_as_live(self):
        directory = self.p.watch_dir / "crashed"
        sessions.write_json(self.p.state_dir / "watch_current.json", {"dir": str(directory)})
        sessions.write_json(directory / "status.json", {"running": True, "out": str(directory), "watch": "started"})
        result = sessions.watch_status(self.p)
        self.assertFalse(result["running"])
        self.assertEqual(result["watch"], "interrupted")

    def test_mcp_lifecycle_uses_same_session_and_keeps_input_confirmation(self):
        with mock.patch.object(mcp_server, "_PROFILE", self.p):
            def call(name, arguments=None):
                response = mcp_server.handle("tools/call", {"name": name, "arguments": arguments or {}})
                return json.loads(response["content"][0]["text"])
            started = call("watch_start", {"interval": 0.05})
            self.assertEqual(call("watch_status")["out"], started["out"])
            self.assertEqual(call("watch_events")["events"], [])
            with self.assertRaises(ValueError):
                call("click", {"cx": 10, "cy": 10})
            call("watch_stop")
            wait_until(lambda: not call("watch_status")["running"])

    def test_mcp_stdio_stays_valid_json_and_shutdown_does_not_orphan_worker(self):
        config = self.root / "profile.json"
        config.write_text(json.dumps(self.p.to_dict()), encoding="utf-8")
        env = dict(os.environ, GAMEQAKIT_PROFILE=str(config), PYTHONIOENCODING="utf-8")
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "watch_start", "arguments": {"interval": 0.05}}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "watch_status"}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "watch_stop"}},
        ]
        result = subprocess.run([sys.executable, "-m", "gameqakit.mcp_server"],
                                input="\n".join(json.dumps(m) for m in messages) + "\n",
                                capture_output=True, text=True, encoding="utf-8", env=env, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        responses = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([r["id"] for r in responses], [1, 2, 3, 4])
        self.assertTrue(all("result" in r for r in responses), responses)
        wait_until(lambda: not sessions.watch_status(self.p)["running"])
        self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
