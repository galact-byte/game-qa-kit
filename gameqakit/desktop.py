"""Windows 独立桌面上的自有测试进程；不是虚拟机或安全沙箱。

不切换输入桌面。通过 Job Object 回收本次创建的进程树，不按进程名终止游戏。
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes as W
import os
from pathlib import Path
import subprocess
import uuid

u = ctypes.WinDLL('user32', use_last_error=True)
k = ctypes.WinDLL('kernel32', use_last_error=True)


class STARTUPINFO(ctypes.Structure):
    _fields_ = [('cb', W.DWORD), ('lpReserved', W.LPWSTR), ('lpDesktop', W.LPWSTR),
                ('lpTitle', W.LPWSTR), ('dwX', W.DWORD), ('dwY', W.DWORD),
                ('dwXSize', W.DWORD), ('dwYSize', W.DWORD), ('dwXCountChars', W.DWORD),
                ('dwYCountChars', W.DWORD), ('dwFillAttribute', W.DWORD), ('dwFlags', W.DWORD),
                ('wShowWindow', W.WORD), ('cbReserved2', W.WORD),
                ('lpReserved2', ctypes.POINTER(ctypes.c_byte)),
                ('hStdInput', W.HANDLE), ('hStdOutput', W.HANDLE), ('hStdError', W.HANDLE)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [('process', W.HANDLE), ('thread', W.HANDLE), ('pid', W.DWORD), ('tid', W.DWORD)]


class BASIC_LIMITS(ctypes.Structure):
    _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64),
                ('flags', W.DWORD), ('min_working_set', ctypes.c_size_t),
                ('max_working_set', ctypes.c_size_t), ('active_processes', W.DWORD),
                ('affinity', ctypes.c_size_t), ('priority', W.DWORD), ('scheduling', W.DWORD)]


class EXTENDED_LIMITS(ctypes.Structure):
    _fields_ = [('basic', BASIC_LIMITS), ('io', ctypes.c_uint64 * 6),
                ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
                ('peak_process_memory', ctypes.c_size_t), ('peak_job_memory', ctypes.c_size_t)]


u.CreateDesktopW.argtypes = [W.LPCWSTR, W.LPCWSTR, ctypes.c_void_p, W.DWORD, W.DWORD, ctypes.c_void_p]
u.CreateDesktopW.restype = W.HANDLE
u.CloseDesktop.argtypes = [W.HANDLE]
k.CreateJobObjectW.argtypes = [ctypes.c_void_p, W.LPCWSTR]
k.CreateJobObjectW.restype = W.HANDLE
k.SetInformationJobObject.argtypes = [W.HANDLE, ctypes.c_int, ctypes.c_void_p, W.DWORD]
k.AssignProcessToJobObject.argtypes = [W.HANDLE, W.HANDLE]
k.CreateProcessW.argtypes = [W.LPCWSTR, W.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, W.BOOL,
                             W.DWORD, ctypes.c_void_p, W.LPCWSTR,
                             ctypes.POINTER(STARTUPINFO), ctypes.POINTER(PROCESS_INFORMATION)]
k.ResumeThread.argtypes = [W.HANDLE]
k.ResumeThread.restype = W.DWORD
k.GetExitCodeProcess.argtypes = [W.HANDLE, ctypes.POINTER(W.DWORD)]
k.TerminateProcess.argtypes = [W.HANDLE, W.UINT]
k.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]
k.CloseHandle.argtypes = [W.HANDLE]


class DesktopProcess:
    def __init__(self, argv, *, cwd, env=None):
        if (not isinstance(argv, (list, tuple)) or not argv
                or any(not isinstance(arg, str) or '\0' in arg for arg in argv)):
            raise ValueError('argv 必须是非空的字符串数组')
        executable = Path(argv[0])
        if not executable.is_absolute() or not executable.is_file():
            raise ValueError('必须指定已有可执行文件的绝对路径')
        self.argv = list(argv)
        self.cwd = Path(cwd).resolve()
        if not self.cwd.is_dir():
            raise ValueError('工作目录不存在')
        self.env = os.environ.copy()
        for key, value in (env or {}).items():
            if (not isinstance(key, str) or not key or '=' in key or '\0' in key
                    or not isinstance(value, str) or '\0' in value):
                raise ValueError('环境变量名称或内容无效')
            self.env[key] = value
        self.desktop_name = 'gameqakit-' + uuid.uuid4().hex[:16]
        self.env['GAMEQAKIT_DESKTOP_NAME'] = self.desktop_name
        self.desktop = None
        self.job = None
        self.process = None
        self.thread = None
        self.pid = None

    def __enter__(self):
        if self.desktop or self.pid is not None:
            raise RuntimeError('测试进程实例不可重复启动')
        try:
            self.desktop = u.CreateDesktopW(self.desktop_name, None, None, 0, 0x01FF, None)
            if not self.desktop:
                raise ctypes.WinError(ctypes.get_last_error())
            self.job = k.CreateJobObjectW(None, None)
            if not self.job:
                raise ctypes.WinError(ctypes.get_last_error())
            limits = EXTENDED_LIMITS()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not k.SetInformationJobObject(self.job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise ctypes.WinError(ctypes.get_last_error())
            si = STARTUPINFO()
            si.cb = ctypes.sizeof(si)
            si.lpDesktop = self.desktop_name
            pi = PROCESS_INFORMATION()
            command = ctypes.create_unicode_buffer(subprocess.list2cmdline(self.argv))
            environment = ctypes.create_unicode_buffer('\0'.join(
                key + '=' + value for key, value in sorted(self.env.items(), key=lambda row: row[0].casefold())) + '\0')
            # Python 的 subprocess.STARTUPINFO 不传递 lpDesktop，必须用真实原生结构。
            if not k.CreateProcessW(self.argv[0], command, None, None, False, 0x0404,
                                    environment, str(self.cwd), ctypes.byref(si), ctypes.byref(pi)):
                raise ctypes.WinError(ctypes.get_last_error())
            self.process, self.thread, self.pid = pi.process, pi.thread, pi.pid
            if not k.AssignProcessToJobObject(self.job, self.process):
                raise ctypes.WinError(ctypes.get_last_error())
            return self
        except Exception:
            self.close()
            raise

    def run(self):
        if not self.thread:
            raise RuntimeError('进程尚未创建、已运行或已关闭')
        if k.ResumeThread(self.thread) == 0xFFFFFFFF:
            raise ctypes.WinError(ctypes.get_last_error())
        k.CloseHandle(self.thread)
        self.thread = None

    def alive(self):
        if not self.process:
            return False
        code = W.DWORD()
        return bool(k.GetExitCodeProcess(self.process, ctypes.byref(code))) and code.value == 259

    def windows(self):
        from .win32 import WNDENUMPROC, window_pid, window_info
        windows = []

        @WNDENUMPROC
        def collect(hwnd, _unused):
            if window_pid(hwnd) == self.pid:
                windows.append(window_info(hwnd))
            return True

        u.EnumDesktopWindows.argtypes = [W.HANDLE, WNDENUMPROC, W.LPARAM]
        ctypes.set_last_error(0)
        if not u.EnumDesktopWindows(self.desktop, collect, 0):
            error = ctypes.get_last_error()
            # 新桌面还没有窗口时会返回零，但没有系统错误。
            if error:
                raise ctypes.WinError(error)
        return windows

    def close(self):
        if self.process and self.alive():
            k.TerminateProcess(self.process, 0)
        if self.job:
            k.CloseHandle(self.job)
            self.job = None
        if self.process:
            k.WaitForSingleObject(self.process, 5000)
            k.CloseHandle(self.process)
            self.process = None
        if self.thread:
            k.CloseHandle(self.thread)
            self.thread = None
        if self.desktop:
            u.CloseDesktop(self.desktop)
            self.desktop = None

    def __exit__(self, *_args):
        self.close()
