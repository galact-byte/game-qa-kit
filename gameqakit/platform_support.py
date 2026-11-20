"""公共入口的平台边界；尚未验收的原生功能给出明确错误。"""
from __future__ import annotations

import platform
import sys


class UnsupportedPlatformError(RuntimeError):
    pass


def require_windows(feature="游戏窗口采集和后台控制"):
    if sys.platform != 'win32':
        raise UnsupportedPlatformError(
            f"{feature}目前仅支持 Windows；当前通用版可管理配置、存档快照和复核已有证据。")


def info():
    return {
        'platform': sys.platform,
        'architecture': platform.machine(),
        'features': {'profiles': True, 'save_snapshots': True, 'evidence_review': True,
                     'window_capture': sys.platform == 'win32',
                     'game_control': sys.platform == 'win32'},
    }


def _unsupported(*args, **kwargs):
    require_windows()


class _UnavailableWindows:
    find_window = grab_image = clamp_region = capture = window_text = staticmethod(_unsupported)
    find_profile_window = profile_target = list_process_targets = staticmethod(_unsupported)
    window_info = window_pid = client_size = _pid_path = staticmethod(_unsupported)


if sys.platform == 'win32':
    from . import win32 as window_backend
else:
    window_backend = _UnavailableWindows()
