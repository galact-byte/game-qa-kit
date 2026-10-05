#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""game-qa-kit 离线核心测试：不需要游戏运行，全部可 mock。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gameqakit import win32, dedup, inputs, saves, profile  # noqa: E402


class RegionClampTests(unittest.TestCase):
    def test_within_bounds_kept(self):
        self.assertEqual(win32.clamp_region([10, 20, 100, 50], 1200, 720), (10, 20, 100, 50))

    def test_overflow_shrinks(self):
        self.assertEqual(win32.clamp_region([1150, 700, 200, 200], 1200, 720), (1150, 700, 50, 20))

    def test_origin_out_of_bounds_rejected(self):
        with self.assertRaises(RuntimeError):
            win32.clamp_region([1200, 0, 10, 10], 1200, 720)

    def test_nonpositive_rejected(self):
        with self.assertRaises(RuntimeError):
            win32.clamp_region([0, 0, 0, 10], 1200, 720)


class DedupTests(unittest.TestCase):
    def test_hamming(self):
        self.assertEqual(dedup.hamming(0b1010, 0b1000), 1)
        self.assertEqual(dedup.hamming(0xFF, 0x00), 8)

    def test_dhash_identical_images_zero_distance(self):
        from PIL import Image
        a = Image.new("RGB", (30, 30), (10, 20, 30))
        for x in range(30):
            a.putpixel((x, 15), (200, 200, 200))
        h1 = dedup.dhash_from_image(a)
        h2 = dedup.dhash_from_image(a.copy())
        self.assertEqual(dedup.hamming(h1, h2), 0)


class ClickTargetTests(unittest.TestCase):
    def test_out_of_range_rejected(self):
        with self.assertRaises(RuntimeError):
            inputs._validate_click_target(5000, 10, 1200, 720)

    def test_in_range_ok(self):
        inputs._validate_click_target(600, 360, 1200, 720)


class InputGuardTests(unittest.TestCase):
    """抢鼠标护栏 + 前台快路契约。"""

    def _last_input(self, tick):
        def fake(ptr):
            ptr._obj.dwTime = tick
            return 1
        return fake

    def tearDown(self):
        inputs._LAST_SELF_INPUT_TICK = 0

    def test_guard_disabled_when_zero(self):
        self.assertFalse(inputs.user_active(0))

    def test_recent_foreign_input_blocks(self):
        inputs._LAST_SELF_INPUT_TICK = 0
        with mock.patch.object(inputs.user32, "GetLastInputInfo", side_effect=self._last_input(10_000)), \
             mock.patch.object(inputs, "_now_tick", return_value=10_200):
            self.assertTrue(inputs.user_active(1200))

    def test_self_injected_not_counted(self):
        with mock.patch.object(inputs.user32, "GetLastInputInfo", side_effect=self._last_input(10_000)), \
             mock.patch.object(inputs, "_now_tick", return_value=10_200):
            inputs._LAST_SELF_INPUT_TICK = 10_050
            self.assertFalse(inputs.user_active(1200))

    def test_old_input_does_not_block(self):
        inputs._LAST_SELF_INPUT_TICK = 0
        with mock.patch.object(inputs.user32, "GetLastInputInfo", side_effect=self._last_input(1_000)), \
             mock.patch.object(inputs, "_now_tick", return_value=10_000):
            self.assertFalse(inputs.user_active(1200))

    def test_acquire_foreground_fast_path_skips_raise(self):
        HW = 4242
        raised = []
        with mock.patch.object(inputs.user32, "GetForegroundWindow", return_value=HW), \
             mock.patch.object(inputs, "_raise_topmost", side_effect=lambda h: raised.append(h) or 0):
            r = inputs.acquire_foreground(HW)
        self.assertTrue(r["confirmed"])
        self.assertTrue(r["fast_path"])
        self.assertEqual(raised, [])


class SaveSnapshotTests(unittest.TestCase):
    """存档快照/恢复的完整性契约。"""

    def _make(self):
        tmp = Path(tempfile.mkdtemp())
        savedata = tmp / "savedata"
        savedata.mkdir()
        (savedata / "data0.dat").write_bytes(b"slot0")
        (savedata / "save_str0.var").write_text("v0", encoding="utf-8")
        saves_dir = tmp / "saves"
        return tmp, savedata, saves_dir, ["data*.dat", "save_str*.var"]

    def test_snapshot_then_restore_roundtrip(self):
        _tmp, savedata, saves_dir, globs = self._make()
        saves.snapshot_save("s1", savedata, globs, saves_dir)
        (savedata / "data0.dat").write_bytes(b"CHANGED")
        saves.restore_save("s1", savedata, globs, saves_dir)
        self.assertEqual((savedata / "data0.dat").read_bytes(), b"slot0")

    def test_restore_rejects_tampered_snapshot(self):
        _tmp, savedata, saves_dir, globs = self._make()
        saves.snapshot_save("s1", savedata, globs, saves_dir)
        (saves_dir / "s1" / "data0.dat").write_bytes(b"tampered")  # 篡改快照但不改 manifest
        with self.assertRaises(RuntimeError):
            saves.restore_save("s1", savedata, globs, saves_dir)

    def test_restore_removes_files_absent_from_snapshot(self):
        _tmp, savedata, saves_dir, globs = self._make()
        saves.snapshot_save("s1", savedata, globs, saves_dir)
        (savedata / "data9.dat").write_bytes(b"later-slot")  # 快照后新增的槽
        saves.restore_save("s1", savedata, globs, saves_dir)
        self.assertFalse((savedata / "data9.dat").exists())


class ProfileTests(unittest.TestCase):
    def test_example_profile_loads_without_machine_paths(self):
        p = profile.load_profile("example")
        self.assertEqual(p.proc, "Game")
        self.assertIn("*.sav", p.save_globs)
        self.assertTrue(str(p.saves_dir).endswith("saves"))
        # 公开示例不能带本机绝对路径
        self.assertIsNone(p.savedata_dir)
        self.assertIsNone(p.data_dir)

    def test_derived_dirs_under_data_path(self):
        p = profile.GameProfile(name="x", proc="x", data_dir="/tmp/x")
        self.assertEqual(p.saves_dir, Path("/tmp/x/saves"))
        self.assertEqual(p.watch_dir, Path("/tmp/x/watch"))


if __name__ == "__main__":
    unittest.main()
