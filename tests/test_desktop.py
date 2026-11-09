"""原生隔离桌面必须真实生效，不能给 subprocess.STARTUPINFO 挂一个无效属性。"""
import ctypes
from ctypes import wintypes
import importlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest import mock
import unittest


@unittest.skipUnless(os.name == 'nt', '需要 Windows 桌面 API')
class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.assertIsNotNone(importlib.util.find_spec('gameqakit.desktop'), '尚无真正的隔离桌面启动器')
        self.desktop = importlib.import_module('gameqakit.desktop')

    def wait_file(self, path):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if path.exists():
                try:
                    return json.loads(path.read_text(encoding='utf-8'))
                except ValueError:
                    pass
            time.sleep(0.02)
        self.fail('隔离进程没有产生预期记录')

    def test_child_really_runs_on_new_desktop_and_is_suspended_until_ready(self):
        marker = self.root / 'identity.json'
        code = r'''
import ctypes,json,os,time
from ctypes import wintypes as W
from pathlib import Path
u=ctypes.WinDLL('user32',use_last_error=True); k=ctypes.WinDLL('kernel32')
u.GetThreadDesktop.argtypes=[W.DWORD];u.GetThreadDesktop.restype=W.HANDLE
u.GetUserObjectInformationW.argtypes=[W.HANDLE,ctypes.c_int,ctypes.c_void_p,W.DWORD,ctypes.POINTER(W.DWORD)]
name=ctypes.create_unicode_buffer(256); needed=W.DWORD()
desktop=u.GetThreadDesktop(k.GetCurrentThreadId())
if not u.GetUserObjectInformationW(desktop,2,name,ctypes.sizeof(name),ctypes.byref(needed)):raise ctypes.WinError(ctypes.get_last_error())
Path(os.environ['GAMEQA_TEST_MARKER']).write_text(json.dumps({'desktop':name.value,'pid':os.getpid(),'cwd':os.getcwd()}),encoding='utf-8')
time.sleep(30)
'''
        with self.desktop.DesktopProcess([sys.executable, '-c', code], cwd=self.root,
                                         env={'GAMEQA_TEST_MARKER': str(marker)}) as child:
            time.sleep(0.05)
            self.assertFalse(marker.exists())
            child.run()
            record = self.wait_file(marker)
            self.assertEqual(record['desktop'], child.desktop_name)
            self.assertNotEqual(record['desktop'].lower(), 'default')
            self.assertEqual(record['pid'], child.pid)
            self.assertEqual(Path(record['cwd']), self.root)
            with self.assertRaises(RuntimeError):
                child.run()
        self.assertFalse(child.alive())

    def test_close_terminates_owned_descendants_not_other_processes(self):
        marker = self.root / 'grandchild.json'
        script = self.root / 'grandchild.py'
        script.write_text('import json,os,time\nfrom pathlib import Path\n'
                          f'Path({str(marker)!r}).write_text(json.dumps({{"pid":os.getpid()}}))\n'
                          'time.sleep(30)\n', encoding='utf-8')
        code = f'import subprocess,sys,time;subprocess.Popen([sys.executable,{str(script)!r}]);time.sleep(30)'
        with self.desktop.DesktopProcess([sys.executable, '-c', code], cwd=self.root) as child:
            child.run()
            pid = self.wait_file(marker)['pid']
            k = ctypes.WinDLL('kernel32', use_last_error=True)
            k.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
            k.OpenProcess.restype=wintypes.HANDLE
            k.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
            k.CloseHandle.argtypes=[wintypes.HANDLE]
            handle = k.OpenProcess(0x100000,False,pid)
            self.assertTrue(handle)
        try:
            self.assertEqual(k.WaitForSingleObject(handle,3000),0)
        finally:
            k.CloseHandle(handle)
        self.assertGreater(os.getpid(), 0)

    def test_capture_contains_entire_client_without_title_bar(self):
        marker = self.root / 'painted.json'
        code = '''
import json,os,tkinter as tk
from pathlib import Path
root=tk.Tk(); root.title('GameQA capture client regression'); root.geometry('160x100+50+50')
canvas=tk.Canvas(root,width=160,height=100,highlightthickness=0,borderwidth=0)
canvas.pack(fill='both',expand=True)
canvas.create_rectangle(0,0,160,25,fill='#ff0000',outline='')
canvas.create_rectangle(0,25,160,75,fill='#0000ff',outline='')
canvas.create_rectangle(0,75,160,100,fill='#00ff00',outline='')
root.after(200,lambda: Path(os.environ['GAMEQA_TEST_MARKER']).write_text(json.dumps({'ready':True})))
root.mainloop()
'''
        from gameqakit.win32 import grab_image
        with self.desktop.DesktopProcess([sys.executable, '-c', code], cwd=self.root,
                                         env={'GAMEQA_TEST_MARKER': str(marker)}) as child:
            child.run()
            self.wait_file(marker)
            windows = [w for w in child.windows() if w['client'] == [160, 100]]
            self.assertEqual(len(windows), 1)
            image = grab_image(windows[0]['hwnd'])
            self.assertEqual(image.size, (160, 100))
            # 避开 DPI 虚拟化在顶边产生的插值，精确校验上下客户区色块。
            self.assertEqual(image.getpixel((80, 5)), (255, 0, 0))
            self.assertEqual(image.getpixel((80, 99)), (0, 255, 0))

    def test_new_desktop_without_windows_is_a_valid_empty_result(self):
        with self.desktop.DesktopProcess([sys.executable, '-c', 'pass'], cwd=self.root) as child:
            self.assertEqual(child.windows(), [])

    def test_desktop_window_enumeration_preserves_real_system_errors(self):
        def fail(*_args):
            ctypes.set_last_error(5)
            return False

        with self.desktop.DesktopProcess([sys.executable, '-c', 'pass'], cwd=self.root) as child:
            with mock.patch.object(self.desktop.u, 'EnumDesktopWindows', side_effect=fail):
                with self.assertRaises(OSError) as caught:
                    child.windows()
            self.assertEqual(caught.exception.winerror, 5)

    def test_invalid_executable_does_not_fall_back_to_main_desktop(self):
        with self.assertRaises((OSError, ValueError)):
            with self.desktop.DesktopProcess([str(self.root / 'missing.exe')], cwd=self.root):
                self.fail('不应启动成功')


if __name__ == '__main__':
    unittest.main()
