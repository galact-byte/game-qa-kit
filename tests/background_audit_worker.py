"""在自写测试进程执行脚本前安装 Python/WinDLL 审计；无系统级钩子。"""
import ctypes
from ctypes import wintypes as W
import os
from pathlib import Path
import runpy
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from background_call_audit import Audit, desktop_name


def main():
    root, script, *arguments = sys.argv[1:]
    with Audit(root) as audit:
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetProcessTimes.argtypes = [W.HANDLE, *([ctypes.POINTER(W.FILETIME)] * 4)]
        times = [W.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(W.HANDLE(-1), *(ctypes.byref(value) for value in times)):
            raise ctypes.WinError(ctypes.get_last_error())
        created = times[0].dwLowDateTime | (times[0].dwHighDateTime << 32)
        audit.record({'event': 'start', 'pid': os.getpid(), 'created_filetime': created,
                      'role': Path(script).stem, 'desktop': desktop_name()})
        sys.argv = [script, *arguments]
        runpy.run_path(script, run_name='__main__')
        audit.record({'event': 'complete', 'pid': os.getpid()})


if __name__ == '__main__':
    main()
