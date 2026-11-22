"""进程身份查询；macOS 使用现有 libproc 原生辅助组件，不依赖窗口或录屏权限。"""
import atexit
import os
from pathlib import Path
import sys
import threading

_pipe = None
_owner = None
_lock = threading.Lock()


def close():
    global _pipe
    if _pipe is not None:
        _pipe.close()
        _pipe = None


atexit.register(close)


def query(pid):
    global _pipe, _owner
    if type(pid) is not int or not 0 < pid < 2 ** 31:
        return None
    if sys.platform != 'darwin':
        raise RuntimeError('启动身份查询仅供 macOS 使用')
    from .macos_capture import _Pipe
    with _lock:
        try:
            if _pipe is None or _owner != os.getpid():
                _pipe = _Pipe(.75)
                _owner = os.getpid()
            row, payload = _pipe.request({'op': 'process_identity', 'pid': pid})
            identity = row.get('identity')
            if payload or not isinstance(identity, dict) or identity.get('pid') != pid:
                return None
            stamp = identity.get('started_at', '').split('.')
            if (len(stamp) != 2 or not all(part.isascii() and part.isdigit() for part in stamp)
                    or int(stamp[0]) <= 0 or not 0 <= int(stamp[1]) < 1000000
                    or not Path(identity.get('exe_path', '')).is_absolute()):
                return None
            return identity
        except (OSError, ValueError, RuntimeError):
            close()
            return None


def _pid_path(pid):
    if sys.platform == 'win32':
        from .win32 import _pid_path as windows_path
        return windows_path(pid)
    identity = query(pid)
    return identity['exe_path'] if identity else ''
