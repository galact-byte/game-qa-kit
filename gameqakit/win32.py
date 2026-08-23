#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Win32 窗口查找与后台截图（引擎无关）。

故意保持 DPI 不感知：多数老游戏 DPI 不感知，系统高缩放下按原生尺寸渲染再放大。
工具若设 DPI 感知，PrintWindow 会把原生画面画到过大画布产生黑边且坐标错位。
保持不感知：得到干净的虚拟化客户区尺寸；SendInput 绝对坐标在同一虚拟屏坐标系归一化。
"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

HANDLE = wintypes.HANDLE
HWND = wintypes.HWND
HDC = wintypes.HDC
HGDIOBJ = wintypes.HANDLE
HBITMAP = wintypes.HANDLE

# 64 位下必须显式声明句柄型 restype/argtypes，否则 HANDLE 被截断成 32 位而失效
user32.PrintWindow.restype = wintypes.BOOL
user32.PrintWindow.argtypes = [HWND, HDC, wintypes.UINT]
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.IsWindowVisible.argtypes = [HWND]
user32.IsIconic.argtypes = [HWND]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowThreadProcessId.argtypes = [HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextLengthW.argtypes = [HWND]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClientRect.argtypes = [HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.argtypes = [HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetForegroundWindow.restype = HWND
user32.GetDC.restype = HDC
user32.GetDC.argtypes = [HWND]
user32.ReleaseDC.argtypes = [HWND, HDC]

gdi32.CreateCompatibleDC.restype = HDC
gdi32.CreateCompatibleDC.argtypes = [HDC]
gdi32.CreateCompatibleBitmap.restype = HBITMAP
gdi32.CreateCompatibleBitmap.argtypes = [HDC, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.restype = HGDIOBJ
gdi32.SelectObject.argtypes = [HDC, HGDIOBJ]
gdi32.DeleteObject.argtypes = [HGDIOBJ]
gdi32.DeleteDC.argtypes = [HDC]

kernel32.OpenProcess.restype = HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.CloseHandle.argtypes = [HANDLE]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.QueryFullProcessImageNameW.argtypes = [
    HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]

PW_RENDERFULLCONTENT = 0x00000002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
BLANK_FRAME_RATIO = 0.999  # 超此比例纯黑/纯白判为无效帧


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


gdi32.GetDIBits.restype = ctypes.c_int
gdi32.GetDIBits.argtypes = [HDC, HBITMAP, wintypes.UINT, wintypes.UINT,
                            ctypes.c_void_p, ctypes.POINTER(BITMAPINFO), wintypes.UINT]


def _pid_name(pid: int) -> str:
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(32768)
        if not kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return ""
        return os.path.basename(buf.value)
    finally:
        kernel32.CloseHandle(h)


def find_window(proc: str, title_substr: str | None = None) -> int:
    """按进程名（不含 .exe）找可见顶层窗口句柄；title_substr 兜底。"""
    proc_l = proc.lower().removesuffix(".exe")
    found: list[tuple[int, str, int]] = []

    def cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        name = _pid_name(pid.value).lower().removesuffix(".exe")
        length = user32.GetWindowTextLengthW(hwnd)
        tbuf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, tbuf, length + 1)
        title = tbuf.value
        ok = (name == proc_l) if proc_l else False
        if title_substr:
            ok = ok or (title_substr in title)
        if ok and (length > 0 or name == proc_l):
            found.append((int(hwnd), title, pid.value))
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    if not found:
        raise RuntimeError(f"找不到进程 {proc} 的可见窗口（游戏没开？）")
    found.sort(key=lambda t: len(t[1]), reverse=True)
    return found[0][0]


def client_size(hwnd: int) -> tuple[int, int]:
    rect = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    return rect.right - rect.left, rect.bottom - rect.top


def clamp_region(region, w: int, h: int) -> tuple[int, int, int, int]:
    """把 (x,y,rw,rh) 约束到客户区 [0,w)×[0,h)：左上越界报错，右下越界收缩。"""
    x, y, rw, rh = (int(v) for v in region)
    if rw <= 0 or rh <= 0:
        raise RuntimeError(f"裁剪区域宽高必须为正：{rw}x{rh}")
    if not (0 <= x < w and 0 <= y < h):
        raise RuntimeError(f"裁剪区域左上角 ({x},{y}) 超出客户区 {w}x{h}")
    return x, y, min(rw, w - x), min(rh, h - y)


def _frame_blank_ratio(raw: bytes, w: int, h: int) -> float:
    total = w * h
    if total <= 0:
        return 1.0
    step = max(1, total // 20000)
    black = white = sampled = 0
    for i in range(0, total, step):
        off = i * 4
        b, g, r = raw[off], raw[off + 1], raw[off + 2]
        if b == 0 and g == 0 and r == 0:
            black += 1
        elif b == 255 and g == 255 and r == 255:
            white += 1
        sampled += 1
    return max(black, white) / sampled if sampled else 1.0


def grab_image(hwnd: int):
    """PrintWindow 抓整帧客户区，返回 PIL.Image(RGB)。最小化/黑帧/失败一律抛错。"""
    from PIL import Image
    if user32.IsIconic(hwnd):
        raise RuntimeError("游戏窗口已最小化，无法截图；请先把窗口恢复出来（不必置于前台）。")
    w, h = client_size(hwnd)
    if w <= 0 or h <= 0:
        raise RuntimeError(f"客户区尺寸异常 {w}x{h}（窗口最小化或未就绪？）")
    hwnd_dc = user32.GetDC(hwnd)
    mem_dc = gdi32.CreateCompatibleDC(hwnd_dc)
    bmp = gdi32.CreateCompatibleBitmap(hwnd_dc, w, h)
    try:
        gdi32.SelectObject(mem_dc, bmp)
        ok = bool(user32.PrintWindow(hwnd, mem_dc, PW_RENDERFULLCONTENT))
        if not ok:
            ok = bool(user32.PrintWindow(hwnd, mem_dc, 0))
        if not ok:
            raise RuntimeError("PrintWindow 截图失败：窗口未就绪或不支持后台渲染，未落盘。")
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -h
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0
        buffer = (ctypes.c_char * (w * h * 4))()
        if gdi32.GetDIBits(mem_dc, bmp, 0, h, buffer, ctypes.byref(bmi), 0) == 0:
            raise RuntimeError("GetDIBits 读取像素失败，截图无效，未落盘。")
        raw = bytes(buffer)
    finally:
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(hwnd, hwnd_dc)
    ratio = _frame_blank_ratio(raw, w, h)
    if ratio >= BLANK_FRAME_RATIO:
        raise RuntimeError(f"截图疑似无效帧（{ratio * 100:.1f}% 纯黑/白），拒绝落盘。")
    return Image.frombuffer("RGB", (w, h), raw, "raw", "BGRX", 0, 1)


def capture(hwnd: int, out_path: Path, region=None) -> tuple[int, int]:
    """后台截图存 PNG，返回落盘 (w,h)。region=(x,y,w,h) 只存该矩形，省视觉 token。"""
    img = grab_image(hwnd)
    w, h = img.size
    if region:
        x, y, rw, rh = clamp_region(region, w, h)
        img = img.crop((x, y, x + rw, y + rh))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(out_path), "PNG")
    return img.size


def window_info(hwnd: int) -> dict:
    w, h = client_size(hwnd)
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    fg = int(user32.GetForegroundWindow() or 0)
    return {"hwnd": hwnd, "client": [w, h],
            "window_rect": [rect.left, rect.top, rect.right, rect.bottom],
            "minimized": bool(user32.IsIconic(hwnd)),
            "foreground_hwnd": fg, "occluded_by_foreground": fg != hwnd}
