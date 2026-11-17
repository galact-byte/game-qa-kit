"""将原生启动守卫拥有的目标接入既有 KAG 监督器。"""
from __future__ import annotations

import ctypes
from ctypes import wintypes as W
import hashlib
from pathlib import Path
import time

from gameqakit.desktop import DesktopProcess, k
from gameqakit.sessions import read_json
from gameqakit.win32 import _pid_path

k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
k.OpenProcess.restype = W.HANDLE


class GuardedTarget:
    def __init__(self, root: Path, host: Path, guard: Path):
        self.root = root.resolve()
        self.argv = [str(self.root / 'game/runtime.exe')]
        self.guard = guard.resolve()
        self.owner = DesktopProcess([str(host.resolve()), str(self.root), str(self.guard),
            hashlib.sha256(self.guard.read_bytes()).hexdigest()], cwd=self.root)
        self.pid = None
        self.process = None
        self.desktop = None
        self.desktop_name = self.owner.desktop_name

    def __enter__(self):
        try:
            self.owner.__enter__()
            self.desktop = self.owner.desktop
            self.owner.run()
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                try:
                    record = read_json(self.root / 'process.json')
                except (FileNotFoundError, ValueError):
                    record = None
                if record:
                    if (record.get('guard_ready') is not True or record.get('all_threads_suspended') is not True
                            or type(record.get('pid')) is not int or record['pid'] <= 0):
                        raise ValueError('原生守卫启动身份无效')
                    self.pid = record['pid']
                    if Path(_pid_path(self.pid)).resolve() != Path(self.argv[0]).resolve():
                        raise ValueError('原生守卫目标路径不匹配')
                    self.process = k.OpenProcess(0x00100000 | 0x1000, False, self.pid)
                    if not self.process:
                        raise ctypes.WinError(ctypes.get_last_error())
                    return self
                if not self.owner.alive():
                    raise RuntimeError('原生守卫在交接前退出')
                time.sleep(.01)
            raise TimeoutError('原生守卫启动超时')
        except BaseException:
            self.close()
            raise

    def run(self):
        if not self.process or not self.alive() or (self.root / 'resume').exists():
            raise RuntimeError('受保护目标尚未就绪或已恢复')
        (self.root / 'resume').touch(exist_ok=False)

    def alive(self):
        return bool(self.process and self.owner.alive() and k.WaitForSingleObject(self.process, 0) == 0x102)

    def windows(self):
        return DesktopProcess.windows(self)

    def close(self):
        if self.owner.process:
            (self.root / 'stop').touch(exist_ok=True)
            deadline = time.monotonic() + 3
            while self.owner.alive() and time.monotonic() < deadline:
                time.sleep(.01)
            self.owner.close()
        if self.process:
            k.WaitForSingleObject(self.process, 3000)
            k.CloseHandle(self.process)
            self.process = None
        self.desktop = None

    def __exit__(self, *_args):
        self.close()
