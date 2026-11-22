"""隔离辅助进程专用 Xlib ABI、窗口身份和 XComposite 像素读取。"""
from __future__ import annotations

import ctypes as C
import ctypes.util
import hashlib
import os
from pathlib import Path
import re
import socket

from PIL import Image
from .capture_session import CaptureEnded
from .x11_capture import parse_start_ticks, source_identity

MAX_PIXELS = 32 * 1024 * 1024
MAX_BYTES = 128 * 1024 * 1024


def validate_layout(width, height, bits, stride, order, masks):
    if (any(type(x) is not int for x in (width, height, bits, stride, order))
            or min(width, height) <= 0 or width * height > MAX_PIXELS
            or bits not in (16, 24, 32) or order not in (0, 1)
            or stride < width * (bits // 8) or stride * height > MAX_BYTES):
        raise CaptureEnded('X11 图像布局或内存预算无效')
    occupied = 0
    for mask in masks:
        if type(mask) is not int or mask <= 0 or mask >= 1 << bits or occupied & mask:
            raise CaptureEnded('X11 RGB mask 无效')
        shifted = mask >> ((mask & -mask).bit_length() - 1)
        if shifted & (shifted + 1): raise CaptureEnded('X11 RGB mask 不连续')
        occupied |= mask
    if len(masks) != 3: raise CaptureEnded('X11 RGB mask 无效')


def decode_pixels(data, width, height, bits, stride, order, masks):
    validate_layout(width, height, bits, stride, order, masks)
    if len(data) != stride * height: raise CaptureEnded('X11 图像数据截断')
    if masks == (0xff0000, 0xff00, 0xff) and bits in (24, 32):
        mode = {(24, 0): 'BGR', (24, 1): 'RGB', (32, 0): 'BGRX', (32, 1): 'XRGB'}[bits, order]
        return Image.frombytes('RGB', (width, height), data, 'raw', mode, stride, 1)
    shifts = [(m & -m).bit_length() - 1 for m in masks]
    scales = [m >> s for m, s in zip(masks, shifts)]
    output = bytearray(width * height * 3)
    size = bits // 8
    for y in range(height):
        for x in range(width):
            offset = y * stride + x * size
            pixel = int.from_bytes(data[offset:offset + size], 'little' if order == 0 else 'big')
            i = (y * width + x) * 3
            output[i:i + 3] = bytes(((pixel & m) >> s) * 255 // scale for m, s, scale in zip(masks, shifts, scales))
    return Image.frombytes('RGB', (width, height), bytes(output))


class XImage(C.Structure):
    _fields_ = [('width', C.c_int), ('height', C.c_int), ('xoffset', C.c_int), ('format', C.c_int),
                ('data', C.c_void_p), ('byte_order', C.c_int), ('bitmap_unit', C.c_int),
                ('bitmap_bit_order', C.c_int), ('bitmap_pad', C.c_int), ('depth', C.c_int),
                ('bytes_per_line', C.c_int), ('bits_per_pixel', C.c_int),
                ('red_mask', C.c_ulong), ('green_mask', C.c_ulong), ('blue_mask', C.c_ulong),
                ('obdata', C.c_void_p), ('functions', C.c_void_p * 6)]


class Visual(C.Structure):
    _fields_ = [('ext_data', C.c_void_p), ('visualid', C.c_ulong), ('visual_class', C.c_int),
                ('red_mask', C.c_ulong), ('green_mask', C.c_ulong), ('blue_mask', C.c_ulong),
                ('bits_per_rgb', C.c_int), ('map_entries', C.c_int)]


def image_masks(image, attrs):
    masks = (image.red_mask, image.green_mask, image.blue_mask)
    if not any(masks):
        # 命名 pixmap 没有 visual，使用同次来源检查所读的窗口 TrueColor visual。
        if not attrs.visual: raise CaptureEnded('窗口缺少 TrueColor visual')
        visual = C.cast(attrs.visual, C.POINTER(Visual)).contents
        if visual.visual_class != 4: raise CaptureEnded('尚未接入非 TrueColor visual')
        masks = (visual.red_mask, visual.green_mask, visual.blue_mask)
    return masks


class Attributes(C.Structure):
    _fields_ = [(name, kind) for name, kind in (
        ('x', C.c_int), ('y', C.c_int), ('width', C.c_int), ('height', C.c_int),
        ('border_width', C.c_int), ('depth', C.c_int), ('visual', C.c_void_p), ('root', C.c_ulong),
        ('window_class', C.c_int), ('bit_gravity', C.c_int), ('win_gravity', C.c_int),
        ('backing_store', C.c_int), ('backing_planes', C.c_ulong), ('backing_pixel', C.c_ulong),
        ('save_under', C.c_int), ('colormap', C.c_ulong), ('map_installed', C.c_int), ('map_state', C.c_int),
        ('all_event_masks', C.c_long), ('your_event_mask', C.c_long), ('do_not_propagate_mask', C.c_long),
        ('override_redirect', C.c_int), ('screen', C.c_void_p))]


class XError(C.Structure):
    _fields_ = [('type', C.c_int), ('display', C.c_void_p), ('resourceid', C.c_ulong),
                ('serial', C.c_ulong), ('error_code', C.c_ubyte), ('request_code', C.c_ubyte), ('minor_code', C.c_ubyte)]


class Connection:
    def __init__(self):
        self.display = None
        self.pixmap = self.window = 0
        self.redirected = False
        self.size = None
        self.atoms = {}
        self.errors = []
        self.display_name = os.environ.get('DISPLAY', '')
        match = re.fullmatch(r'(?:unix)?:([0-9]+)(?:\.[0-9]+)?', self.display_name)
        if not match: raise CaptureEnded('只允许本机 X11 DISPLAY')
        self.socket_path = Path('/tmp/.X11-unix') / ('X' + match[1])
        self.connection_id = self.server_identity()
        try:
            self.x = C.CDLL(ctypes.util.find_library('X11') or 'libX11.so.6')
            self.composite = C.CDLL(ctypes.util.find_library('Xcomposite') or 'libXcomposite.so.1')
        except OSError as exc:
            raise CaptureEnded(f'缺少 X11 / Xcomposite 系统库：{exc}') from exc
        p, u, i = C.c_void_p, C.c_ulong, C.c_int
        for name, result, args in (
            ('XOpenDisplay', p, [C.c_char_p]), ('XCloseDisplay', i, [p]),
            ('XDefaultRootWindow', u, [p]), ('XSync', i, [p, i]),
            ('XInternAtom', u, [p, C.c_char_p, i]),
            ('XGetWindowProperty', i, [p, u, u, C.c_long, C.c_long, i, u, C.POINTER(u), C.POINTER(i), C.POINTER(u), C.POINTER(u), C.POINTER(p)]),
            ('XQueryTree', i, [p, u, C.POINTER(u), C.POINTER(u), C.POINTER(C.POINTER(u)), C.POINTER(C.c_uint)]),
            ('XGetWindowAttributes', i, [p, u, C.POINTER(Attributes)]),
            ('XFree', i, [p]), ('XFreePixmap', i, [p, u]),
            ('XGetImage', C.POINTER(XImage), [p, u, i, i, C.c_uint, C.c_uint, u, i]),
            ('XDestroyImage', i, [C.POINTER(XImage)])):
            fn = getattr(self.x, name); fn.restype, fn.argtypes = result, args
        for name, result, args in (
            ('XCompositeQueryExtension', i, [p, C.POINTER(i), C.POINTER(i)]),
            ('XCompositeQueryVersion', i, [p, C.POINTER(i), C.POINTER(i)]),
            ('XCompositeRedirectWindow', None, [p, u, i]),
            ('XCompositeUnredirectWindow', None, [p, u, i]),
            ('XCompositeNameWindowPixmap', u, [p, u])):
            fn = getattr(self.composite, name); fn.restype, fn.argtypes = result, args
        callback = C.CFUNCTYPE(C.c_int, p, C.POINTER(XError))
        def on_error(display, event):
            e = event.contents
            self.errors.append((e.error_code, e.request_code, e.minor_code))
            return 0
        self.error_callback = callback(on_error)
        self.x.XSetErrorHandler.argtypes = [callback]
        self.x.XSetErrorHandler.restype = p
        self.x.XSetErrorHandler(self.error_callback)
        self.display = self.x.XOpenDisplay(self.display_name.encode())
        if not self.display: raise CaptureEnded('无法连接 X11 DISPLAY，请检查会话与认证')
        try:
            event, error, major, minor = (i() for _ in range(4))
            if (not self.composite.XCompositeQueryExtension(self.display, C.byref(event), C.byref(error))
                    or not self.composite.XCompositeQueryVersion(self.display, C.byref(major), C.byref(minor))
                    or (major.value, minor.value) < (0, 2)):
                raise CaptureEnded('X server 缺少支持命名 pixmap 的 Composite 扩展')
            self.root = self.x.XDefaultRootWindow(self.display)
        except Exception:
            self.close()
            raise

    def server_identity(self):
        try:
            stat = self.socket_path.stat()
            boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
            return hashlib.sha256(f'{boot}:{stat.st_dev}:{stat.st_ino}:{stat.st_mtime_ns}'.encode()).hexdigest()
        except OSError as exc:
            raise CaptureEnded('无法绑定本机 X server 身份') from exc

    def sync(self):
        self.x.XSync(self.display, 0)
        if self.errors:
            errors, self.errors = self.errors, []
            raise CaptureEnded(f'X11 服务端拒绝请求：{errors}')

    def prop(self, window, name, kind=None):
        if name not in self.atoms: self.atoms[name] = self.x.XInternAtom(self.display, name.encode(), 0)
        actual, count, after, fmt, data = C.c_ulong(), C.c_ulong(), C.c_ulong(), C.c_int(), C.c_void_p()
        rc = self.x.XGetWindowProperty(self.display, window, self.atoms[name], 0, 4096, 0, 0,
                                      C.byref(actual), C.byref(fmt), C.byref(count), C.byref(after), C.byref(data))
        try:
            self.sync()
            if rc or after.value or count.value > 16384: raise CaptureEnded('X11 窗口属性超限')
            if kind and actual.value != kind: return None
            if not data.value: return None
            if fmt.value == 32:
                return list(C.cast(data, C.POINTER(C.c_ulong))[:count.value])
            if fmt.value == 8: return C.string_at(data, count.value)
            return None
        finally:
            if data.value: self.x.XFree(data)

    def attributes(self, window):
        attrs = Attributes()
        ok = self.x.XGetWindowAttributes(self.display, window, C.byref(attrs))
        self.sync()
        if not ok or attrs.map_state != 2 or attrs.window_class != 1:
            raise CaptureEnded('窗口已关闭、取消映射或无法采集')
        if min(attrs.width, attrs.height) <= 0 or attrs.width * attrs.height > MAX_PIXELS:
            raise CaptureEnded('窗口尺寸超过像素预算')
        return attrs

    def identity(self, window):
        attrs = self.attributes(window)
        pids = self.prop(window, '_NET_WM_PID', 6)
        if not pids or len(pids) != 1 or pids[0] <= 0: raise CaptureEnded('窗口缺少可信本机 PID')
        host = self.prop(window, 'WM_CLIENT_MACHINE', 31)
        if host and host.rstrip(b'\0').decode(errors='replace').casefold() not in {
                socket.gethostname().casefold(), socket.getfqdn().casefold(), 'localhost'}:
            raise CaptureEnded('拒绝远端窗口进程')
        pid = pids[0]
        proc = Path('/proc') / str(pid)
        try:
            if proc.stat().st_uid != os.getuid(): raise CaptureEnded('窗口不属于当前用户')
            stamp = parse_start_ticks((proc / 'stat').read_text())
            exe = str((proc / 'exe').resolve(strict=True))
            if parse_start_ticks((proc / 'stat').read_text()) != stamp:
                raise CaptureEnded('进程身份在读取期间变化')
        except OSError as exc:
            raise CaptureEnded('无法读取窗口进程身份') from exc
        title = self.prop(window, '_NET_WM_NAME') or self.prop(window, 'WM_NAME', 31) or b''
        if not isinstance(title, bytes): raise CaptureEnded('窗口标题属性无效')
        if self.server_identity() != self.connection_id: raise CaptureEnded('X server 身份变化')
        return {'window_id': window, 'pid': pid, 'started_at': stamp, 'exe_path': exe,
                'display': self.display_name, 'connection_id': self.connection_id,
                'title': title.decode('utf-8', 'replace')[:4096], 'name': Path(exe).stem,
                'size': [attrs.width, attrs.height]}

    def list_windows(self):
        rows, reasons, queue, seen = [], [], [self.root], set()
        while queue:
            window = queue.pop()
            if window in seen: continue
            seen.add(window)
            if len(seen) > 10000: raise CaptureEnded('X11 窗口树超限')
            root, parent, count, children = C.c_ulong(), C.c_ulong(), C.c_uint(), C.POINTER(C.c_ulong)()
            ok = self.x.XQueryTree(self.display, window, C.byref(root), C.byref(parent), C.byref(children), C.byref(count))
            try:
                self.sync()
                if not ok or count.value > 10000: continue
                queue.extend(children[:count.value])
            except CaptureEnded:
                continue
            finally:
                if children: self.x.XFree(children)
            if window == self.root: continue
            try: rows.append(self.identity(window))
            except CaptureEnded as exc: reasons.append(str(exc))
        return rows, sorted(set(reasons))[:20]

    def release_target(self):
        if self.pixmap:
            self.x.XFreePixmap(self.display, self.pixmap)
            self.pixmap = 0
        if self.redirected:
            self.composite.XCompositeUnredirectWindow(self.display, self.window, 0)
            self.redirected = False
        self.window = 0
        self.size = None

    def capture(self, expected):
        before = self.identity(expected['window_id'])
        if source_identity(before) != source_identity(expected):
            raise CaptureEnded('目标窗口来源变化，请重新选择')
        if self.window != before['window_id']:
            self.release_target()
            self.window = before['window_id']
            self.composite.XCompositeRedirectWindow(self.display, self.window, 0)
            self.sync()
            self.redirected = True
        # 每次重新命名：相同尺寸的 unmap/map 也会建立新 pixmap。
        if self.pixmap:
            self.x.XFreePixmap(self.display, self.pixmap)
            self.pixmap = 0
        self.pixmap = self.composite.XCompositeNameWindowPixmap(self.display, self.window)
        self.sync()
        width, height = before['size']
        pointer = self.x.XGetImage(self.display, self.pixmap, 0, 0, width, height, C.c_ulong(-1).value, 2)
        try:
            self.sync()
            if not pointer: raise CaptureEnded('XGetImage 未返回图像')
            raw = pointer.contents
            masks = image_masks(raw, self.attributes(self.window))
            if (raw.width, raw.height, raw.xoffset, raw.format) != (width, height, 0, 2):
                raise CaptureEnded('XImage 几何与来源不符')
            validate_layout(width, height, raw.bits_per_pixel, raw.bytes_per_line, raw.byte_order, masks)
            if not raw.data: raise CaptureEnded('XImage 缺少像素')
            image = decode_pixels(C.string_at(raw.data, raw.bytes_per_line * height), width, height,
                                  raw.bits_per_pixel, raw.bytes_per_line, raw.byte_order, masks)
        finally:
            if pointer: self.x.XDestroyImage(pointer)
        after = self.identity(self.window)
        if source_identity(after) != source_identity(before) or after['size'] != before['size']:
            raise CaptureEnded('截图期间窗口来源或尺寸变化')
        return image, after

    def close(self):
        if self.display:
            try:
                self.release_target()
                self.x.XSync(self.display, 0)
            finally:
                self.x.XCloseDisplay(self.display)
                self.display = None
