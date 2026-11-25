"""仅用于隔离桌面的自写消息消费窗口。"""
import ctypes as C
from ctypes import wintypes as W
import json
from pathlib import Path
import sys
import time

u, g, k = (C.WinDLL(name, use_last_error=True) for name in ('user32', 'gdi32', 'kernel32'))
u.SetThreadDpiAwarenessContext.argtypes = [W.HANDLE]
u.SetThreadDpiAwarenessContext.restype = W.HANDLE
if not u.SetThreadDpiAwarenessContext(C.c_void_p(-4)):
    raise C.WinError(C.get_last_error())
PROC = C.WINFUNCTYPE(W.LPARAM, W.HWND, W.UINT, W.WPARAM, W.LPARAM)


class CLASS(C.Structure):
    _fields_ = [('style', W.UINT), ('proc', PROC), ('extra', C.c_int), ('window_extra', C.c_int),
                ('instance', W.HINSTANCE), ('icon', W.HICON), ('cursor', W.HANDLE),
                ('brush', W.HBRUSH), ('menu', W.LPCWSTR), ('name', W.LPCWSTR)]


class PAINT(C.Structure):
    _fields_ = [('dc', W.HDC), ('erase', W.BOOL), ('rect', W.RECT), ('restore', W.BOOL),
                ('update', W.BOOL), ('reserved', C.c_byte * 32)]


k.GetModuleHandleW.restype = W.HINSTANCE
u.DefWindowProcW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
u.DefWindowProcW.restype = W.LPARAM
u.CreateWindowExW.argtypes = [W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD, C.c_int, C.c_int,
                              C.c_int, C.c_int, W.HWND, W.HMENU, W.HINSTANCE, W.LPVOID]
u.CreateWindowExW.restype = W.HWND
u.RegisterClassW.argtypes = [C.POINTER(CLASS)]
u.BeginPaint.argtypes = [W.HWND, C.POINTER(PAINT)]
u.BeginPaint.restype = W.HDC
u.EndPaint.argtypes = [W.HWND, C.POINTER(PAINT)]
u.FillRect.argtypes = [W.HDC, C.POINTER(W.RECT), W.HBRUSH]
u.InvalidateRect.argtypes = [W.HWND, C.POINTER(W.RECT), W.BOOL]
u.UpdateWindow.argtypes = [W.HWND]
u.ShowWindow.argtypes = [W.HWND, C.c_int]
u.GetClientRect.argtypes = [W.HWND, C.POINTER(W.RECT)]
u.ScreenToClient.argtypes = [W.HWND, C.POINTER(W.POINT)]
g.CreateSolidBrush.argtypes = [W.DWORD]
g.CreateSolidBrush.restype = W.HBRUSH
g.DeleteObject.argtypes = [W.HANDLE]
clicked = False
events = Path(sys.argv[1]).with_suffix('.jsonl')


@PROC
def dispatch(hwnd, message, wparam, lparam):
    global clicked
    if message in (0x200, 0x201, 0x202, 0x20A):
        x, y = C.c_short(lparam & 0xffff).value, C.c_short((lparam >> 16) & 0xffff).value
        if message == 0x20A:
            point = W.POINT(x, y)
            u.ScreenToClient(hwnd, C.byref(point))
            x, y = point.x, point.y
        with events.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'message': message, 'wparam': wparam, 'point': [x, y],
                                     'time': time.monotonic()}) + '\n')
    if message == 0x202:
        clicked = not clicked
        u.InvalidateRect(hwnd, None, True)
        u.UpdateWindow(hwnd)
        return 0
    if message == 0xF:
        paint, rect = PAINT(), W.RECT()
        dc = u.BeginPaint(hwnd, C.byref(paint))
        u.GetClientRect(hwnd, C.byref(rect))
        brush = g.CreateSolidBrush(0x992244 if clicked else 0x228844)
        u.FillRect(dc, C.byref(rect), brush)
        g.DeleteObject(brush)
        rect.right //= 2
        brush = g.CreateSolidBrush(0x3388bb)
        u.FillRect(dc, C.byref(rect), brush)
        g.DeleteObject(brush)
        u.EndPaint(hwnd, C.byref(paint))
        return 0
    return u.DefWindowProcW(hwnd, message, wparam, lparam)


instance = k.GetModuleHandleW(None)
kind = CLASS(0, dispatch, 0, 0, instance, None, None, None, None, 'GameQaBackgroundFixture')
if not u.RegisterClassW(C.byref(kind)):
    raise C.WinError(C.get_last_error())
hwnd = u.CreateWindowExW(0x08000080, kind.name, kind.name, 0x00cf0000, -10000, -10000, 360, 280,
                         None, None, instance, None)
if not hwnd:
    raise C.WinError(C.get_last_error())
u.ShowWindow(hwnd, 4)
u.UpdateWindow(hwnd)
import os
client = W.RECT()
u.GetClientRect(hwnd, C.byref(client))
Path(sys.argv[1]).write_text(json.dumps({'hwnd': int(hwnd), 'pid': os.getpid(),
                                      'size': [client.right, client.bottom]}), encoding='utf-8')
message = W.MSG()
while u.GetMessageW(C.byref(message), None, 0, 0) > 0:
    u.TranslateMessage(C.byref(message))
    u.DispatchMessageW(C.byref(message))
