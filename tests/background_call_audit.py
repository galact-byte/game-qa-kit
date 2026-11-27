"""测试专用 WinDLL/user32 调用边界。不能观察 DLL 内部或其他进程的调用。"""
import ctypes
import json
import os
from pathlib import Path
import threading
import time
import traceback
from unittest import mock

FORBIDDEN = frozenset({
    'SetCursorPos', 'SetPhysicalCursorPos', 'SendInput', 'mouse_event', 'keybd_event',
    'SetForegroundWindow', 'SetActiveWindow', 'SetFocus', 'BringWindowToTop',
    'SwitchToThisWindow', 'SwitchDesktop', 'AttachThreadInput',
})


class AuditedFunction:
    def __init__(self, function, name, record):
        object.__setattr__(self, '_function', function)
        object.__setattr__(self, '_name', name)
        object.__setattr__(self, '_record', record)

    def __getattr__(self, name):
        return getattr(self._function, name)

    def __setattr__(self, name, value):
        setattr(self._function, name, value)

    def __call__(self, *args):
        error = ctypes.get_last_error()
        blocked = self._name in FORBIDDEN
        event = {'event': 'call', 'pid': os.getpid(), 'thread': threading.get_ident(),
                 'time_ns': time.monotonic_ns(), 'api': self._name, 'blocked': blocked}
        if blocked:
            frame = traceback.extract_stack(limit=3)[-2]
            event['caller'] = {'file': frame.filename, 'line': frame.lineno, 'function': frame.name}
        try:
            self._record(event)
        finally:
            ctypes.set_last_error(error)
        if blocked:
            raise AssertionError('禁止执行全局输入或置前台 API: ' + self._name)
        return self._function(*args)


class AuditedUser32:
    def __init__(self, dll, record):
        self._dll, self._record = dll, record
        self._functions = {}

    def __getattr__(self, name):
        if name.startswith('_'):
            return getattr(self._dll, name)
        if name not in self._functions:
            self._functions[name] = AuditedFunction(getattr(self._dll, name), name, self._record)
        return self._functions[name]

    def __getitem__(self, name):
        if not isinstance(name, str):
            raise AssertionError('测试审计不支持 ordinal API 查找')
        return self.__getattr__(name)


class Audit:
    """在全新测试工作进程导入产品模块前安装；不修改系统或产品配置。"""
    def __init__(self, root):
        self.path = Path(root) / f'calls-{os.getpid()}.jsonl'
        self.stream = self.path.open('x', encoding='utf-8', buffering=1)
        self.lock = threading.Lock()
        original = ctypes.WinDLL

        def load(name, *args, **kwargs):
            dll = original(name, *args, **kwargs)
            leaf = str(name).replace('\\', '/').rsplit('/', 1)[-1].casefold()
            return AuditedUser32(dll, self.record) if leaf in ('user32', 'user32.dll') else dll

        self.patches = [mock.patch.object(ctypes, 'WinDLL', load),
                        mock.patch.object(ctypes.windll, '_dlltype', load)]

    def record(self, event):
        with self.lock:
            self.stream.write(json.dumps(event, ensure_ascii=False) + '\n')

    def __enter__(self):
        for patch in self.patches:
            patch.start()
        return self

    def __exit__(self, *_args):
        for patch in reversed(self.patches):
            patch.stop()
        self.stream.close()


def desktop_name():
    from ctypes import wintypes as W
    user = ctypes.WinDLL('user32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    user.GetThreadDesktop.argtypes = [W.DWORD]
    user.GetThreadDesktop.restype = W.HANDLE
    user.GetUserObjectInformationW.argtypes = [W.HANDLE, ctypes.c_int, W.LPVOID, W.DWORD, ctypes.POINTER(W.DWORD)]
    name, needed = ctypes.create_unicode_buffer(256), W.DWORD()
    handle = user.GetThreadDesktop(kernel.GetCurrentThreadId())
    if not handle or not user.GetUserObjectInformationW(handle, 2, name, ctypes.sizeof(name), ctypes.byref(needed)):
        raise ctypes.WinError(ctypes.get_last_error())
    return name.value
