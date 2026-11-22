"""公共入口的平台边界；尚未验收的原生功能给出明确错误。"""
from __future__ import annotations

import os
import platform
import sys


class UnsupportedPlatformError(RuntimeError):
    pass


def require_windows(feature="游戏窗口采集和后台控制"):
    if sys.platform != 'win32':
        raise UnsupportedPlatformError(
            f"{feature}目前仅支持 Windows；当前通用版可管理配置、存档快照和复核已有证据。")


def macos_capture_supported():
    return (sys.platform == 'darwin' and platform.machine() == 'arm64'
            and platform.mac_ver()[0].split('.')[0].isdigit()
            and int(platform.mac_ver()[0].split('.')[0]) >= 14)


def macos_managed_supported():
    return macos_capture_supported() and os.environ.get('GAMEQA_MACOS_MANAGED_VALIDATION') == '1'


def require_managed_game(backend):
    if sys.platform == 'win32':
        return
    if not macos_managed_supported() or backend != 'godot_authored':
        raise UnsupportedPlatformError('本平台仅接入 macOS 14+ arm64 的登记 Godot 自写样本；其他引擎受管启动尚未验收。')


def x11_capture_supported():
    return (sys.platform == 'linux' and os.environ.get('GAMEQA_X11_VALIDATION') == '1'
            and bool(os.environ.get('DISPLAY'))
            and os.environ.get('XDG_SESSION_TYPE', '').lower() != 'wayland'
            and not os.environ.get('WAYLAND_DISPLAY'))


def require_capture(feature='窗口采集'):
    if sys.platform != 'win32' and not macos_capture_supported() and not x11_capture_supported():
        raise UnsupportedPlatformError(f'{feature}需要 Windows 或 macOS 14 及更新版本的 Apple Silicon；Linux X11 尚在原生验收，Wayland 尚未接入原生采集。')


def info():
    return {
        'platform': sys.platform,
        'architecture': platform.machine(),
        'features': {'profiles': True, 'save_snapshots': True, 'evidence_review': True,
                     'window_capture': sys.platform == 'win32' or macos_capture_supported() or x11_capture_supported(),
                     'game_control': sys.platform == 'win32' or macos_managed_supported(),
                     'godot_managed': sys.platform == 'win32' or macos_managed_supported()},
    }


def _unsupported(*args, **kwargs):
    require_windows()


class _UnavailableWindows:
    find_window = grab_image = clamp_region = capture = window_text = staticmethod(_unsupported)
    find_profile_window = profile_target = list_process_targets = staticmethod(_unsupported)
    window_info = window_pid = client_size = _pid_path = staticmethod(_unsupported)


if sys.platform == 'win32':
    from . import win32 as window_backend
elif macos_capture_supported():
    from . import macos_capture as window_backend
elif x11_capture_supported():
    from . import x11_capture as window_backend
else:
    window_backend = _UnavailableWindows()
