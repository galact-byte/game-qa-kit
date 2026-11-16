"""独立取图工作进程；阻塞由宿主按三秒期限回收。"""
import ctypes
from ctypes import wintypes as W
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gameqakit.win32 import capture


def run(directory, ident, hwnd, destination, desktop_name):
    u = ctypes.WinDLL('user32', use_last_error=True)
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    u.OpenDesktopW.argtypes = [W.LPCWSTR, W.DWORD, W.BOOL, W.DWORD]
    u.OpenDesktopW.restype = W.HANDLE
    u.GetThreadDesktop.argtypes = [W.DWORD]
    u.GetThreadDesktop.restype = W.HANDLE
    u.SetThreadDesktop.argtypes = [W.HANDLE]
    u.SetThreadDesktop.restype = W.BOOL
    u.CloseDesktop.argtypes = [W.HANDLE]
    k.GetCurrentThreadId.restype = W.DWORD
    previous = u.GetThreadDesktop(k.GetCurrentThreadId())
    desktop = u.OpenDesktopW(desktop_name, 0, False, 0x01ff)
    result = {'id': ident, 'ok': False}
    try:
        if not desktop or not u.SetThreadDesktop(desktop):
            raise ctypes.WinError(ctypes.get_last_error())
        capture(hwnd, destination)
        result['ok'] = True
    except Exception as error:
        result['error'] = str(error)
    finally:
        if desktop:
            u.SetThreadDesktop(previous)
            u.CloseDesktop(desktop)
        temporary = directory / 'capture-result.json.part'
        temporary.write_text(json.dumps(result), encoding='utf-8')
        temporary.replace(directory / 'capture-result.json')
    return 0 if result['ok'] else 4


if __name__ == '__main__':
    raise SystemExit(run(Path(sys.argv[1]), sys.argv[2], int(sys.argv[3]), Path(sys.argv[4]), sys.argv[5]))
