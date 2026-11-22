"""X11 单窗口采集父端；原生库仅由隔离辅助进程加载。"""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
from pathlib import Path
import sys
import threading
import time

from PIL import Image
from .capture_session import CaptureEnded, CaptureFrame, CaptureSource, clamp_region
from .macos_capture import _Pipe, MAX_PIXELS

BACKEND = 'XComposite.NameWindowPixmap'


def parse_start_ticks(text):
    try:
        fields = text[text.rindex(')') + 1:].split()
        value = fields[19]
        if not value.isdecimal() or int(value) <= 0:
            raise ValueError()
        return value
    except (ValueError, IndexError) as exc:
        raise CaptureEnded('无法读取进程启动身份') from exc


def source_identity(source):
    if not isinstance(source, dict):
        raise CaptureEnded('无效窗口来源')
    for key in ('window_id', 'pid'):
        if type(source.get(key)) is not int or source[key] <= 0:
            raise CaptureEnded('无效窗口标识')
    for key in ('started_at', 'exe_path', 'display', 'connection_id'):
        if not isinstance(source.get(key), str) or not source[key] or len(source[key]) > 4096:
            raise CaptureEnded('无效窗口进程身份')
    return hashlib.sha256(json.dumps([source[k] for k in (
        'window_id', 'pid', 'started_at', 'exe_path', 'display', 'connection_id')],
        ensure_ascii=False).encode()).hexdigest()


def helper_command():
    from .platform_support import x11_capture_supported
    if not x11_capture_supported():
        raise CaptureEnded('窗口采集需要本地 X11 会话与 DISPLAY；Wayland 尚未验收')
    return [sys.executable, *([] if getattr(sys, 'frozen', False) else ['-B']), '-m', 'gameqakit.x11_helper']


def list_windows():
    pipe = _Pipe(3, helper_command())
    try:
        row, payload = pipe.request({'op': 'list'})
        rows = row.get('windows')
        if payload or not isinstance(rows, list) or len(rows) > 10000:
            raise CaptureEnded('无效窗口列表')
        for source in rows:
            source_identity(source)
            source['client_size'] = source.get('size')
        if not rows and row.get('unavailable'):
            raise CaptureEnded('无法绑定窗口：' + ', '.join(sorted(set(row['unavailable']))))
        return rows
    finally:
        pipe.close()


def find_window(proc, title_substr=None, *, exe_path=None, pid=None, window_id=None):
    rows = [r for r in list_windows()
            if (pid is None or r['pid'] == pid)
            and (window_id is None or r['window_id'] == window_id)
            and (not exe_path or r['exe_path'] == str(exe_path))
            and (exe_path or not proc or proc.casefold() in (r.get('name', '').casefold(), Path(r['exe_path']).stem.casefold()))
            and (not title_substr or title_substr.casefold() in r.get('title', '').casefold())]
    if len(rows) != 1:
        raise RuntimeError('找到多个匹配窗口，请指定窗口或标题' if rows else '未找到匹配窗口，请确认目标在本机 X11 会话运行')
    return rows[0]


class X11CaptureSession:
    def __init__(self, source, *, interval=2., request_timeout=3.):
        self.source = CaptureSource('linux', BACKEND, source_identity(source))
        if any(not math.isfinite(x) or x <= 0 for x in (interval, request_timeout)):
            raise ValueError('采集间隔和超时必须为正的有限秒数')
        self.target = dict(source)
        self.command = helper_command()
        self.request_timeout = request_timeout
        self.condition = threading.Condition()
        self.stop = threading.Event()
        self.closed = False
        self.frame = self.error = self.pipe = self.process = None
        self.worker = threading.Thread(target=self._run, args=(interval,), name='GameQA-X11Capture', daemon=True)
        self.worker.start()

    def _run(self, interval):
        try:
            with self.condition:
                if self.stop.is_set(): return
                self.pipe = _Pipe(self.request_timeout, self.command)
                self.process = self.pipe.process
            sequence = 0
            while not self.stop.is_set():
                begin = time.monotonic()
                row, payload = self.pipe.request({'op': 'capture', 'source': self.target})
                if source_identity(row.get('source')) != self.source.source_id:
                    raise CaptureEnded('目标窗口来源变化，请重新选择')
                serial = row.get('sequence')
                if type(serial) is not int or serial != sequence + 1:
                    raise CaptureEnded('原生采集返回无效序号')
                with Image.open(io.BytesIO(payload)) as encoded:
                    if (encoded.format != 'PNG' or encoded.width * encoded.height > MAX_PIXELS
                            or list(encoded.size) != row['source'].get('size')):
                        raise CaptureEnded('原生图像格式或尺寸无效')
                    image = encoded.convert('RGB')
                sequence = serial
                frame = CaptureFrame(image, self.source, sequence, row.get('captured_at'), row['source'].get('title', ''))
                with self.condition:
                    if self.stop.is_set(): break
                    self.frame = frame
                    self.condition.notify_all()
                self.stop.wait(max(.01, interval - (time.monotonic() - begin)))
        except Exception as exc:
            with self.condition:
                self.frame = None
                self.error = exc if isinstance(exc, CaptureEnded) else CaptureEnded(f'X11 采集终止：{exc}')
                self.condition.notify_all()
        finally:
            if self.pipe is not None:
                self.pipe.close()

    def read(self, *, timeout, stop_event):
        deadline = time.monotonic() + max(0, timeout)
        with self.condition:
            while True:
                if self.closed: raise CaptureEnded('采集会话已关闭')
                if stop_event.is_set(): return None
                if self.error: raise self.error
                if self.frame is not None:
                    result, self.frame = self.frame, None
                    return result
                remaining = deadline - time.monotonic()
                if remaining <= 0: return None
                self.condition.wait(min(.01, remaining))

    def close(self):
        with self.condition:
            self.closed = True
            self.stop.set()
            self.frame = None
            self.condition.notify_all()
            process = self.process
        if process is not None and process.poll() is None:
            try: process.terminate()
            except ProcessLookupError: pass
        self.worker.join(timeout=4)
        if self.worker.is_alive():
            raise RuntimeError('X11 采集线程未退出')


def list_process_targets():
    return list_windows()


def profile_target(profile):
    source = getattr(profile, 'target_source', None)
    if source is None:
        from .sessions import watch_status
        status = watch_status(profile)
        if status.get('running') and status.get('target_exe_path') == profile.exe_path:
            source = status.get('target_source')
    return {'exe_path': profile.exe_path, 'pid': profile.target_pid, **({'source': source} if source else {})}


def find_profile_window(profile):
    target = profile_target(profile)
    return dict(target['source']) if target.get('source') else find_window(
        profile.proc, profile.title_substr, exe_path=target['exe_path'], pid=target['pid'])


def window_info(source):
    return {'hwnd': None, **source, 'client_size': source['size'],
            'capture_source': CaptureSource('linux', BACKEND, source_identity(source)).metadata()}


def client_size(source):
    return tuple(source['size'])


def window_pid(source):
    source_identity(source)
    return source['pid']


def window_text(source):
    return source.get('title', '')


def _capture_once(source):
    session = X11CaptureSession(source)
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            frame = session.read(timeout=.05, stop_event=session.stop)
            if frame is not None: return frame
        raise CaptureEnded('等待单窗口截图超时')
    finally:
        session.close()


def grab_image(source):
    return _capture_once(source).image


def capture(source, out, region=None):
    image = grab_image(source)
    if region:
        x, y, w, h = clamp_region(region, *image.size)
        image = image.crop((x, y, x + w, y + h))
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(out, 'PNG')
    return image.size
