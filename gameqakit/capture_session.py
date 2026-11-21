"""被动单窗口采集边界；不持有游戏输入权或游戏进程生命周期。"""
from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass
from typing import Protocol

from PIL import Image


def clamp_region(region, w: int, h: int) -> tuple[int, int, int, int]:
    """把区域约束到图像像素边界，避免依赖平台窗口 API。"""
    x, y, rw, rh = (int(v) for v in region)
    if rw <= 0 or rh <= 0:
        raise RuntimeError(f"裁剪区域宽高必须为正：{rw}x{rh}")
    if not (0 <= x < w and 0 <= y < h):
        raise RuntimeError(f"裁剪区域左上角 ({x},{y}) 超出客户区 {w}x{h}")
    return x, y, min(rw, w - x), min(rh, h - y)


class CaptureEnded(RuntimeError):
    """窗口关闭、授权撤销或来源变化等不可自动换源恢复的情况。"""


@dataclass(frozen=True)
class CaptureSource:
    platform: str
    backend: str
    source_id: str
    hwnd: int | None = None

    def __post_init__(self):
        if any(not isinstance(value, str) or not value.strip()
               for value in (self.platform, self.backend, self.source_id)):
            raise ValueError("采集来源必须包含平台、后端及窗口标识")
        if self.hwnd is not None and (type(self.hwnd) is not int or self.hwnd <= 0):
            raise ValueError("HWND 必须为正整数或空值")

    def metadata(self):
        return asdict(self)


@dataclass(frozen=True)
class CaptureFrame:
    image: Image.Image
    source: CaptureSource
    sequence: int
    captured_at: str
    native_text: str = ""

    def __post_init__(self):
        if not isinstance(self.image, Image.Image) or min(self.image.size) <= 0:
            raise ValueError("采集帧必须包含非空图像")
        if not isinstance(self.source, CaptureSource):
            raise ValueError("采集帧缺少有效来源")
        if type(self.sequence) is not int or self.sequence < 0:
            raise ValueError("帧序号必须是非负整数")
        if not isinstance(self.captured_at, str):
            raise ValueError("采集时间必须是 ISO 日期时间")
        try:
            stamp = dt.datetime.fromisoformat(self.captured_at)
        except ValueError as exc:
            raise ValueError("采集时间必须是 ISO 日期时间") from exc
        if stamp.tzinfo is None:
            raise ValueError("采集时间必须包含时区")
        if not isinstance(self.native_text, str):
            raise ValueError("窗口文字必须为字符串")


class CaptureSession(Protocol):
    def read(self, *, timeout: float, stop_event) -> CaptureFrame | None:
        """新帧自带序号和源时间；暂时无新缓冲区返回 None，终止抛 CaptureEnded。

        流后端须在 timeout 或停止请求后返回；不得用旧缓存冒充新帧。
        返回图像的所有权交给观察层，后端不得继续修改它。
        """
        ...

    def close(self) -> None:
        """释放本会话的采集资源，不关闭用户游戏。"""
        ...


class WindowsCaptureSession:
    """保留同步 Win32 取图；系统截图调用自身的阻塞限制仍然存在。"""
    def __init__(self, select_window, grab_image, window_text):
        self.select_window = select_window
        self.grab_image = grab_image
        self.window_text = window_text
        self.source = None
        self.sequence = 0
        self.closed = False

    def read(self, *, timeout, stop_event):
        if self.closed:
            raise CaptureEnded("采集会话已关闭")
        if stop_event.is_set():
            return None
        try:
            hwnd = self.select_window()
        except Exception as exc:
            if self.source is not None:
                raise CaptureEnded(f"目标窗口不可用：{exc}") from exc
            raise
        if self.source is not None and hwnd != self.source.hwnd:
            raise CaptureEnded("目标窗口来源变化，请重新选择并开启会话")
        if self.source is None:
            self.source = CaptureSource("win32", "Win32", str(hwnd), hwnd)
        image = self.grab_image(hwnd)
        captured_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds")
        text = self.window_text(hwnd)
        self.sequence += 1
        return CaptureFrame(image, self.source, self.sequence, captured_at, text)

    def close(self):
        self.closed = True
