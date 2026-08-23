#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""向游戏发送输入（引擎无关，但默认低优先级）。

设计取向（本轮示例游戏巡检的教训）：
- 模型驱动游戏比人肉眼手玩慢且脆，输入通路只作辅助，主线是“人玩+盯屏+异步复核”。
- 很多游戏（如 HSP）只认前台真实 SendInput，必须置顶+移动真光标，天然会抢用户鼠标。
  因此默认带“抢鼠标护栏”：用户最近在操作则让路；带前台快路减少常态延迟；返回
  foreground_confirmed 让“点了没反应=没置顶=没点到”变得显式可判。
"""
from __future__ import annotations

import ctypes
import tempfile
import time as _t
from ctypes import wintypes
from pathlib import Path

from .win32 import user32, kernel32, client_size, capture, find_window
from .dedup import pixel_digest_from_file

ULONG_PTR = wintypes.WPARAM


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]
user32.GetLastInputInfo.restype = wintypes.BOOL
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
kernel32.GetTickCount.restype = wintypes.DWORD

SW_RESTORE = 9
HWND_TOPMOST = wintypes.HWND(-1)
HWND_NOTOPMOST = wintypes.HWND(-2)
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_SHOWWINDOW = 0x0040
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
KEYEVENTF_KEYUP = 0x0002

# 本工具自注入输入时刻，供护栏排除自注入（GetLastInputInfo 无法区分来源）
_LAST_SELF_INPUT_TICK = 0
_SELF_INPUT_TOLERANCE_MS = 400


def _now_tick() -> int:
    return int(kernel32.GetTickCount())


def _mark_self_input() -> None:
    global _LAST_SELF_INPUT_TICK
    _LAST_SELF_INPUT_TICK = _now_tick()


def user_active(guard_ms: int) -> bool:
    """最近 guard_ms 内是否有非本工具的真实用户输入。拿不到时间则不拦。"""
    if guard_ms <= 0:
        return False
    li = LASTINPUTINFO()
    li.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if not user32.GetLastInputInfo(ctypes.byref(li)):
        return False
    tick = int(li.dwTime)
    age = (_now_tick() - tick) & 0xFFFFFFFF
    if age >= guard_ms:
        return False
    if _LAST_SELF_INPUT_TICK and abs(tick - _LAST_SELF_INPUT_TICK) <= _SELF_INPUT_TOLERANCE_MS:
        return False
    return True


def _raise_topmost(hwnd: int) -> int:
    user32.ShowWindow(hwnd, SW_RESTORE)
    user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
    prev = user32.GetForegroundWindow()
    my = kernel32.GetCurrentThreadId()
    fpid = wintypes.DWORD()
    ft = user32.GetWindowThreadProcessId(prev, ctypes.byref(fpid))
    tpid = wintypes.DWORD()
    tt = user32.GetWindowThreadProcessId(hwnd, ctypes.byref(tpid))
    user32.AttachThreadInput(my, ft, True)
    user32.AttachThreadInput(my, tt, True)
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    user32.AttachThreadInput(my, ft, False)
    user32.AttachThreadInput(my, tt, False)
    return int(prev) if prev else 0


def acquire_foreground(hwnd: int, timeout_ms: int = 350, poll_ms: int = 15) -> dict:
    """确保游戏在前台。已在前台走快路（零等待）；否则置顶+轮询，最快确认即返回。"""
    if int(user32.GetForegroundWindow() or 0) == int(hwnd):
        return {"confirmed": True, "fast_path": True, "waited_ms": 0, "prev": int(hwnd)}
    prev = _raise_topmost(hwnd)
    waited = 0
    while waited <= timeout_ms:
        if int(user32.GetForegroundWindow() or 0) == int(hwnd):
            return {"confirmed": True, "fast_path": False, "waited_ms": waited, "prev": prev}
        _t.sleep(poll_ms / 1000)
        waited += poll_ms
    return {"confirmed": False, "fast_path": False, "waited_ms": waited, "prev": prev}


def _send_mouse(flags: int, nx: int = 0, ny: int = 0) -> int:
    inp = INPUT(type=0)
    inp.u.mi = MOUSEINPUT(nx, ny, 0, flags, 0, 0)
    return int(user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT)))


def _send_key(vk: int, up: bool) -> int:
    inp = INPUT(type=1)
    inp.u.ki = KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP if up else 0, 0, 0)
    return int(user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT)))


def _validate_click_target(cx: int, cy: int, w: int, h: int) -> None:
    if not (0 <= cx < w and 0 <= cy < h):
        raise RuntimeError(f"点击坐标 ({cx},{cy}) 超出客户区 {w}x{h}，拒绝点击。")


def _capture_to_client_point(hwnd: int, x: int, y: int) -> tuple[int, int]:
    """将 PrintWindow 截图坐标换算为 ClientToScreen 所需的客户区坐标（消除标题栏/边框偏移）。"""
    window = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(window))
    origin = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(origin))
    return x - (origin.x - window.left), y - (origin.y - window.top)


def _abs_norm(x: int, y: int) -> tuple[int, int]:
    W = user32.GetSystemMetrics(0)
    H = user32.GetSystemMetrics(1)
    return round(x * 65535 / (W - 1)), round(y * 65535 / (H - 1))


def _region_digest(hwnd: int, region) -> str:
    with tempfile.TemporaryDirectory(prefix="gqk-verify-") as td:
        shot = Path(td) / "region.png"
        capture(hwnd, shot, region=region)
        return pixel_digest_from_file(shot)


def click(cx: int, cy: int, proc: str, hold_ms: int = 90, verify_region=None,
          origin=None, guard_ms: int = 1200, force: bool = False,
          restore_topmost: bool = True) -> dict:
    """点击游戏客户区坐标（capture 像素坐标系）。origin=(ox,oy) 支持裁剪图相对坐标。"""
    hwnd = find_window(proc)
    w, h = client_size(hwnd)
    if origin is not None:
        cx = int(cx) + int(origin[0])
        cy = int(cy) + int(origin[1])
    _validate_click_target(cx, cy, w, h)
    if not force and user_active(guard_ms):
        raise RuntimeError(
            f"检测到你正在使用电脑（最近 {guard_ms}ms 内有真实键鼠输入），已跳过点击以免抢鼠标；"
            "确需执行请传 force=true。")
    region = None
    before = None
    if verify_region:
        from .win32 import clamp_region
        region = clamp_region(verify_region, w, h)
        before = _region_digest(hwnd, region)
    cur = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(cur))
    fg_state = None
    try:
        fg_state = acquire_foreground(hwnd)
        prev = fg_state["prev"]
        if not fg_state["confirmed"]:
            raise RuntimeError(
                f"游戏窗口未获得前台焦点（等待 {fg_state['waited_ms']}ms），已放弃点击——"
                "这通常就是“点了没反应”的真因（没置顶=没点到）。")
        client_x, client_y = _capture_to_client_point(hwnd, cx, cy)
        p = wintypes.POINT(x=client_x, y=client_y)
        user32.ClientToScreen(hwnd, ctypes.byref(p))
        nx, ny = _abs_norm(p.x, p.y)
        sent = _send_mouse(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, nx, ny)
        _t.sleep(0.05)
        sent += _send_mouse(MOUSEEVENTF_LEFTDOWN | MOUSEEVENTF_ABSOLUTE, nx, ny)
        _t.sleep(hold_ms / 1000)
        sent += _send_mouse(MOUSEEVENTF_LEFTUP | MOUSEEVENTF_ABSOLUTE, nx, ny)
        _mark_self_input()
        _t.sleep(0.08)
        if sent != 3:
            raise RuntimeError(f"SendInput 未完全送达（{sent}/3），点击结果不可信。")
        state_changed = None
        if region:
            state_changed = before != _region_digest(hwnd, region)
        return {"ok": True, "input_sent": True, "foreground_confirmed": True,
                "fast_path": fg_state["fast_path"], "acquire_ms": fg_state["waited_ms"],
                "state_changed": state_changed, "clicked_capture": [cx, cy],
                "clicked_client": [client_x, client_y], "prev_foreground": prev}
    finally:
        if restore_topmost and fg_state and not fg_state.get("fast_path"):
            user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0,
                                SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
        user32.SetCursorPos(cur.x, cur.y)


def press_key(vk: int, proc: str, verify_region=None, guard_ms: int = 1200,
              force: bool = False, restore_topmost: bool = True) -> dict:
    hwnd = find_window(proc)
    w, h = client_size(hwnd)
    if not force and user_active(guard_ms):
        raise RuntimeError(
            f"检测到你正在使用电脑（最近 {guard_ms}ms 内有真实键鼠输入），已跳过按键；force=true 强制。")
    region = None
    before = None
    if verify_region:
        from .win32 import clamp_region
        region = clamp_region(verify_region, w, h)
        before = _region_digest(hwnd, region)
    fg_state = None
    try:
        fg_state = acquire_foreground(hwnd)
        prev = fg_state["prev"]
        if not fg_state["confirmed"]:
            raise RuntimeError(f"游戏窗口未获得前台焦点（等待 {fg_state['waited_ms']}ms），已放弃按键。")
        sent = _send_key(vk, False)
        _t.sleep(0.05)
        sent += _send_key(vk, True)
        _mark_self_input()
        _t.sleep(0.08)
        if sent != 2:
            raise RuntimeError(f"SendInput 按键未完全送达（{sent}/2），结果不可信。")
        state_changed = None
        if region:
            state_changed = before != _region_digest(hwnd, region)
        return {"ok": True, "input_sent": True, "foreground_confirmed": True,
                "fast_path": fg_state["fast_path"], "acquire_ms": fg_state["waited_ms"],
                "state_changed": state_changed, "vk": vk, "prev_foreground": prev}
    finally:
        if restore_topmost and fg_state and not fg_state.get("fast_path"):
            user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0,
                                SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
