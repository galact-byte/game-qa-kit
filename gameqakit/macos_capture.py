"""macOS 单窗口截图；仅管理本工具的原生辅助进程。"""
from __future__ import annotations

import io
import json
import math
import os
from pathlib import Path
import platform
import struct
import subprocess
import sys
import threading
import time

from PIL import Image

from .capture_session import CaptureEnded, CaptureFrame, CaptureSource, clamp_region

BACKEND = 'ScreenCaptureKit.SCScreenshotManager'
MAX_JSON = 65536
MAX_PNG = 64 * 1024 * 1024
MAX_PIXELS = 32 * 1024 * 1024


def helper_command():
    if sys.platform != 'darwin' or platform.machine() != 'arm64':
        raise RuntimeError('窗口采集需要 macOS Apple Silicon（arm64）')
    if tuple(map(int, platform.mac_ver()[0].split('.')[:1])) < (14,):
        raise RuntimeError('单窗口采集需要 macOS 14 或更新版本')
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
    path = root / 'plugins/macos_capture/GameQACapture'
    if not path.is_file():
        raise RuntimeError('缺少 macOS 原生采集组件，请使用完整的 arm64 应用包')
    return [str(path)]


def _identity(source):
    if not isinstance(source, dict):
        raise CaptureEnded('无效窗口来源')
    if any(type(source.get(key)) is not int or source[key] <= 0 for key in ('window_id', 'pid')):
        raise CaptureEnded('无效窗口标识')
    stamp = source.get('started_at')
    if not isinstance(stamp, str) or not stamp or len(stamp) > 80:
        raise CaptureEnded('无效进程启动身份')
    return f"{source['window_id']}:{source['pid']}:{stamp}"


class _Pipe:
    def __init__(self, timeout):
        self.timeout = timeout
        self.process = subprocess.Popen(helper_command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=None if os.environ.get('GAMEQAKIT_NATIVE_DIAGNOSTICS') == '1' else subprocess.DEVNULL, bufsize=0)
        self.expired = False

    def _exact(self, size):
        chunks = bytearray()
        while len(chunks) < size:
            data = self.process.stdout.read(size - len(chunks))
            if not data:
                raise CaptureEnded('原生采集消息截断或辅助进程退出')
            chunks.extend(data)
        return bytes(chunks)

    def request(self, request):
        def expire():
            self.expired = True
            if self.process.poll() is None:
                self.process.kill()
        timer = threading.Timer(self.timeout, expire)
        timer.daemon = True
        timer.start()
        try:
            data = json.dumps({'version': 1, **request}, ensure_ascii=False).encode('utf-8') + b'\n'
            if len(data) > 8192:
                raise CaptureEnded('原生请求过长')
            self.process.stdin.write(data)
            self.process.stdin.flush()
            length, = struct.unpack('>I', self._exact(4))
            if not 1 <= length <= MAX_JSON:
                raise CaptureEnded('原生消息长度超限')
            row = json.loads(self._exact(length).decode('utf-8'))
            if not isinstance(row, dict) or row.get('version') != 1:
                raise CaptureEnded('原生协议版本无效')
            size = row.get('payload_bytes')
            if type(size) is not int or not 0 <= size <= MAX_PNG:
                raise CaptureEnded('原生图像长度超限')
            if row.get('ok') is not True:
                reason = row.get('error', 'native_error')
                if reason == 'permission_denied':
                    raise CaptureEnded('未获屏幕录制权限，请在系统设置的隐私与安全性中允许本应用，再重新开启采集')
                raise CaptureEnded(f'原生采集终止：{str(reason)[:300]}')
            return row, self._exact(size)
        except Exception as exc:
            if self.expired:
                raise CaptureEnded('原生单窗口采集超时') from exc
            if isinstance(exc, CaptureEnded):
                raise
            raise CaptureEnded(f'无效原生采集消息：{exc}') from exc
        finally:
            timer.cancel()
            timer.join()

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process.wait(timeout=2)
        for stream in (self.process.stdin, self.process.stdout):
            stream.close()


def list_windows():
    pipe = _Pipe(3)
    try:
        row, payload = pipe.request({'op': 'list'})
        windows = row.get('windows')
        if not windows and row.get('unavailable'):
            reasons = sorted({str(item.get('reason', 'unknown')) for item in row['unavailable'] if isinstance(item, dict)})
            raise CaptureEnded('无法读取窗口进程身份：' + ', '.join(reasons))
        if payload or not isinstance(windows, list) or len(windows) > 10000:
            raise CaptureEnded('无效窗口列表')
        for source in windows:
            _identity(source)
            source['client_size'] = source.get('size')
        return windows
    finally:
        pipe.close()


def find_window(proc, title_substr=None, *, exe_path=None, pid=None, window_id=None):
    candidates = []
    for row in list_windows():
        if pid is not None and row['pid'] != pid:
            continue
        if window_id is not None and row['window_id'] != window_id:
            continue
        if exe_path and row.get('exe_path') != str(exe_path):
            continue
        if not exe_path and proc and proc.casefold() not in (row.get('name', '').casefold(), Path(row.get('exe_path', '')).stem.casefold()):
            continue
        if title_substr and title_substr.casefold() not in row.get('title', '').casefold():
            continue
        candidates.append(row)
    if len(candidates) != 1:
        raise RuntimeError('找到多个匹配窗口，请指定窗口或标题' if candidates else '未找到匹配窗口，请确认游戏正在运行')
    return candidates[0]


class MacOSCaptureSession:
    def __init__(self, source, *, interval=2., request_timeout=3.):
        identity = _identity(source)
        self.native_source = dict(source)
        if not math.isfinite(interval) or interval <= 0 or not math.isfinite(request_timeout) or request_timeout <= 0:
            raise ValueError('采集间隔和超时必须为正的有限秒数')
        self.target = dict(source)
        self.source = CaptureSource('darwin', BACKEND, identity)
        self.pipe = _Pipe(request_timeout)
        self.process = self.pipe.process
        self.condition = threading.Condition()
        self.stop = threading.Event()
        self.closed = False
        self.frame = None
        self.error = None
        self.worker = threading.Thread(target=self._run, args=(interval,), name='GameQA-MacCapture', daemon=True)
        self.worker.start()

    def _run(self, interval):
        sequence = 0
        try:
            while not self.stop.is_set():
                started = time.monotonic()
                row, payload = self.pipe.request({'op': 'capture', 'source': self.target})
                if _identity(row.get('source')) != self.source.source_id:
                    raise CaptureEnded('目标窗口来源变化，请重新选择')
                serial = row.get('sequence')
                if type(serial) is not int or serial <= sequence:
                    raise CaptureEnded('原生采集返回重复或倒退的序号')
                with Image.open(io.BytesIO(payload)) as encoded:
                    if encoded.format != 'PNG' or encoded.width * encoded.height > MAX_PIXELS:
                        raise CaptureEnded('原生图像格式或像素数无效')
                    if list(encoded.size) != row['source'].get('size'):
                        raise CaptureEnded('原生图像尺寸与来源不符')
                    image = encoded.convert('RGB')
                frame = CaptureFrame(image, self.source, serial, row.get('captured_at'), row['source'].get('title', ''))
                sequence = serial
                with self.condition:
                    self.frame = frame
                    self.condition.notify_all()
                self.stop.wait(max(0, interval - (time.monotonic() - started)))
        except Exception as exc:
            with self.condition:
                self.error = exc if isinstance(exc, CaptureEnded) else CaptureEnded(f'原生帧无效：{exc}')
                self.frame = None
                self.condition.notify_all()
        finally:
            # Worker owns the pipes; close only terminates the process to unblock it.
            self.pipe.close()

    def read(self, *, timeout, stop_event):
        deadline = time.monotonic() + max(0, timeout)
        with self.condition:
            while True:
                if self.closed:
                    raise CaptureEnded('采集会话已关闭')
                if stop_event.is_set():
                    return None
                if self.error:
                    raise self.error
                if self.frame is not None:
                    frame, self.frame = self.frame, None
                    return frame
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self.condition.wait(min(.01, remaining))

    def close(self):
        with self.condition:
            self.closed = True
            self.stop.set()
            self.frame = None
            self.condition.notify_all()
        if self.process.poll() is None:
            try:
                self.process.terminate()
            except ProcessLookupError:
                pass
        self.worker.join(timeout=4)
        if self.worker.is_alive():
            raise RuntimeError('原生采集线程未退出')


def list_process_targets():
    return list_windows()


def profile_target(profile):
    source = getattr(profile, 'target_source', None)
    if source is None:
        from .sessions import watch_status
        status = watch_status(profile)
        if status.get('running') and status.get('target_exe_path') == profile.exe_path:
            source = status.get('target_source')
    return {'exe_path': profile.exe_path, 'pid': profile.target_pid,
            **({'source': source} if source else {})}


def find_profile_window(profile):
    target = profile_target(profile)
    if target.get('source'):
        return dict(target['source'])
    return find_window(profile.proc, profile.title_substr, exe_path=target['exe_path'], pid=target['pid'])


def window_info(source):
    _identity(source)
    return {'hwnd': None, **source, 'client_size': source.get('size'),
            'capture_source': CaptureSource('darwin', BACKEND, _identity(source)).metadata()}


def client_size(source):
    return tuple(source['size'])


def window_pid(source):
    _identity(source)
    return source['pid']


def _capture_once(source):
    session = MacOSCaptureSession(source, interval=2.)
    try:
        frame = None
        deadline = time.monotonic() + 4
        while frame is None and time.monotonic() < deadline:
            frame = session.read(timeout=.05, stop_event=session.stop)
        if frame is None:
            raise CaptureEnded('等待单窗口截图超时')
        return frame
    finally:
        session.close()


def grab_image(source):
    return _capture_once(source).image


def window_text(source):
    return source.get('title', '')


def capture(source, out, region=None):
    image = _capture_once(source).image
    if region:
        x, y, w, h = clamp_region(region, *image.size)
        image = image.crop((x, y, x + w, y + h))
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(out, 'PNG')
    return image.size
