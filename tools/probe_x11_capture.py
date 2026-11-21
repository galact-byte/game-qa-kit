"""在独立 Xvfb 中验证 XComposite 遮挡采集；不接入产品、不发送输入。"""
from __future__ import annotations

import argparse
import ctypes as C
import ctypes.util
import json
import os
from pathlib import Path
import platform
import select
import struct
import subprocess
import sys
import time
import zlib

BACKGROUND = 0x173042
COVER = 0x9B2044
MARKERS = (0x22AACC, 0xFFAA33, 0x55CC66, 0x22AACC)


class XError(C.Structure):
    _fields_ = [('type', C.c_int), ('display', C.c_void_p), ('resourceid', C.c_ulong),
                ('serial', C.c_ulong), ('error_code', C.c_ubyte),
                ('request_code', C.c_ubyte), ('minor_code', C.c_ubyte)]


class X11:
    def __init__(self):
        if sys.platform != 'linux' or not os.environ.get('DISPLAY'):
            raise RuntimeError('探针仅在显式设置 DISPLAY 的 Linux X11 环境运行')
        self.x = C.CDLL(ctypes.util.find_library('X11') or 'libX11.so.6')
        self.composite = C.CDLL(ctypes.util.find_library('Xcomposite') or 'libXcomposite.so.1')
        pointer, window, integer, uint = C.c_void_p, C.c_ulong, C.c_int, C.c_uint
        signatures = {
            'XOpenDisplay': (pointer, [C.c_char_p]),
            'XDefaultRootWindow': (window, [pointer]),
            'XDefaultScreen': (integer, [pointer]),
            'XDefaultDepth': (integer, [pointer, integer]),
            'XServerVendor': (C.c_char_p, [pointer]),
            'XCreateSimpleWindow': (window, [pointer, window, integer, integer, uint, uint, uint, window, window]),
            'XStoreName': (integer, [pointer, window, C.c_char_p]),
            'XMapRaised': (integer, [pointer, window]),
            'XResizeWindow': (integer, [pointer, window, uint, uint]),
            'XDestroyWindow': (integer, [pointer, window]),
            'XCreateGC': (pointer, [pointer, window, window, pointer]),
            'XSetForeground': (integer, [pointer, pointer, window]),
            'XFillRectangle': (integer, [pointer, window, pointer, integer, integer, uint, uint]),
            'XDrawString': (integer, [pointer, window, pointer, integer, integer, C.c_char_p, integer]),
            'XGetImage': (pointer, [pointer, window, integer, integer, uint, uint, window, integer]),
            'XGetPixel': (window, [pointer, integer, integer]),
            'XDestroyImage': (integer, [pointer]),
            'XFreePixmap': (integer, [pointer, window]),
            'XFreeGC': (integer, [pointer, pointer]),
            'XSync': (integer, [pointer, integer]),
            'XCloseDisplay': (integer, [pointer]),
        }
        for name, (restype, argtypes) in signatures.items():
            function = getattr(self.x, name)
            function.restype, function.argtypes = restype, argtypes
        for name, restype, argtypes in (
            ('XCompositeQueryExtension', integer, [pointer, C.POINTER(integer), C.POINTER(integer)]),
            ('XCompositeQueryVersion', integer, [pointer, C.POINTER(integer), C.POINTER(integer)]),
            ('XCompositeRedirectWindow', None, [pointer, window, integer]),
            ('XCompositeUnredirectWindow', None, [pointer, window, integer]),
            ('XCompositeNameWindowPixmap', window, [pointer, window]),
        ):
            function = getattr(self.composite, name)
            function.restype, function.argtypes = restype, argtypes
        self.errors = []
        callback_type = C.CFUNCTYPE(C.c_int, C.c_void_p, C.POINTER(XError))

        def on_error(display, event):
            error = event.contents
            self.errors.append({'code': error.error_code, 'request': error.request_code,
                                'minor': error.minor_code, 'resource': error.resourceid})
            return 0

        self.error_callback = callback_type(on_error)
        self.x.XSetErrorHandler.argtypes = [callback_type]
        self.x.XSetErrorHandler.restype = C.c_void_p
        self.x.XSetErrorHandler(self.error_callback)
        self.display = self.x.XOpenDisplay(None)
        if not self.display:
            raise RuntimeError('无法连接指定 X11 display')
        self.root = self.x.XDefaultRootWindow(self.display)
        screen = self.x.XDefaultScreen(self.display)
        if self.x.XDefaultDepth(self.display, screen) != 24:
            self.close()
            raise RuntimeError('本探针要求 Xvfb 24 位 TrueColor 测试屏幕')

    def sync(self):
        self.x.XSync(self.display, False)
        if self.errors:
            errors, self.errors = self.errors, []
            raise RuntimeError(f'X11 请求失败：{errors}')

    def close(self):
        if self.display:
            self.x.XCloseDisplay(self.display)
            self.display = None

    def rectangle(self, window, gc, x, y, width, height, color):
        self.x.XSetForeground(self.display, gc, color)
        self.x.XFillRectangle(self.display, window, gc, x, y, width, height)

    def image(self, drawable, width, height, *, x=0, y=0):
        image = self.x.XGetImage(self.display, drawable, x, y, width, height, C.c_ulong(-1).value, 2)
        if not image:
            raise RuntimeError('XGetImage 未返回图像')
        try:
            return [self.x.XGetPixel(image, column, row) & 0xFFFFFF
                    for row in range(height) for column in range(width)]
        finally:
            self.x.XDestroyImage(image)


def png(path, pixels, width, height):
    def chunk(kind, payload):
        return struct.pack('!I', len(payload)) + kind + payload + struct.pack('!I', zlib.crc32(kind + payload))
    rows = bytearray()
    for row in range(height):
        rows.append(0)
        for color in pixels[row * width:(row + 1) * width]:
            rows.extend(((color >> 16) & 255, (color >> 8) & 255, color & 255))
    header = struct.pack('!2I5B', width, height, 8, 2, 0, 0, 0)
    path.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header)
                     + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))


def fixture(kind):
    connection = X11()
    width, height = (320, 180) if kind == 'target' else (600, 420)
    position = 60 if kind == 'target' else 40
    window = connection.x.XCreateSimpleWindow(connection.display, connection.root, position, position,
                                               width, height, 0, 0, BACKGROUND)
    gc = connection.x.XCreateGC(connection.display, window, 0, None)
    connection.x.XStoreName(connection.display, window, f'gameqa-x11-{kind}'.encode())
    connection.x.XMapRaised(connection.display, window)
    counter, frozen = 1, False

    def draw():
        connection.rectangle(window, gc, 0, 0, width, height, COVER if kind == 'cover' else BACKGROUND)
        if kind == 'target':
            for (x, y), color in zip(((0, 0), (width - 16, 0), (0, height - 16),
                                     (width - 16, height - 16)), MARKERS):
                connection.rectangle(window, gc, x, y, 16, 16, color)
            for bit in range(16):
                connection.rectangle(window, gc, 20 + bit * 16, 28, 12, 20,
                                     0xFFFFFF if counter & (1 << bit) else 0)
        label = (f'TARGET tick={counter} pid={os.getpid()} {width}x{height}' if kind == 'target'
                 else 'COVER: target window is underneath').encode()
        connection.x.XSetForeground(connection.display, gc, 0xFFFFFF)
        connection.x.XDrawString(connection.display, window, gc, 20, 85 if kind == 'target' else 18,
                                 label, len(label))
        connection.sync()

    try:
        draw()
        print(json.dumps({'window': window, 'pid': os.getpid()}), flush=True)
        while True:
            if select.select([sys.stdin], [], [], 0.04)[0]:
                command = sys.stdin.readline().strip()
                if command in ('', 'quit'):
                    break
                if command == 'freeze':
                    frozen = True
                elif command == 'resize':
                    frozen = False
                    width, height = 400, 240
                    connection.x.XResizeWindow(connection.display, window, width, height)
                else:
                    raise ValueError('未知 fixture 命令')
                draw()
                print(json.dumps({'event': command, 'counter': counter}), flush=True)
            if not frozen and kind == 'target':
                counter += 1
                draw()
    finally:
        connection.x.XFreeGC(connection.display, gc)
        connection.x.XDestroyWindow(connection.display, window)
        connection.close()


def reply(child):
    if not select.select([child.stdout], [], [], 5)[0]:
        raise TimeoutError('fixture 回执超时')
    line = child.stdout.readline()
    if not line:
        raise RuntimeError(f'fixture 提前退出，返回码 {child.poll()}')
    return json.loads(line)


def command(child, text):
    child.stdin.write(text + '\n')
    child.stdin.flush()
    result = reply(child)
    if result.get('event') != text:
        raise RuntimeError('fixture 回执不匹配')
    return result


def require_progress(samples):
    counters = [row['counter'] for row in samples]
    if len(counters) < 3 or any(b <= a for a, b in zip(counters, counters[1:])):
        raise RuntimeError(f'采集计数未持续增长：{counters}')


def probe(output, renderer='cpu'):
    output.mkdir(parents=True, exist_ok=False)
    report = {'ok': False, 'platform': platform.platform(), 'display': os.environ.get('DISPLAY'),
              'runner_image': os.environ.get('ImageVersion'), 'commit': os.environ.get('GITHUB_SHA'),
              'samples': [], 'scope': f'Xvfb 24-bit X11, authored {renderer}-rendered fixture only'}
    children, logs = [], []
    connection, pixmap, redirected, target = None, 0, False, None
    try:
        connection = X11()
        report['server_vendor'] = connection.x.XServerVendor(connection.display).decode()
        event, error, major, minor = (C.c_int() for _ in range(4))
        if not connection.composite.XCompositeQueryExtension(connection.display, C.byref(event), C.byref(error)):
            raise RuntimeError('X11 服务没有 Composite 扩展')
        if not connection.composite.XCompositeQueryVersion(connection.display, C.byref(major), C.byref(minor)):
            raise RuntimeError('无法查询 Composite 版本')
        report['composite_version'] = [major.value, minor.value]
        if (major.value, minor.value) < (0, 2):
            raise RuntimeError('Composite 版本不支持 NameWindowPixmap')

        def start(kind):
            log = (output / f'{kind}.log').open('wb')
            logs.append(log)
            fixture_command = [sys.executable, '-B', str(Path(__file__).resolve()), '--fixture', kind]
            if kind == 'target' and renderer == 'opengl':
                fixture_command = [sys.executable, '-B', str(Path(__file__).with_name('probe_x11_gl_fixture.py'))]
            child = subprocess.Popen(fixture_command,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True)
            children.append(child)
            return child, reply(child)

        target, identity = start('target')
        report['target'] = identity
        window = identity['window']
        connection.composite.XCompositeRedirectWindow(connection.display, window, 0)
        connection.sync()
        redirected = True
        pixmap = connection.composite.XCompositeNameWindowPixmap(connection.display, window)
        connection.sync()
        time.sleep(0.12)

        def collect(phase, count, width=320, height=180, covered=False):
            rows = []
            for index in range(count):
                time.sleep(0.12)
                pixels = connection.image(pixmap, width, height)
                points = ((8, 8), (width - 8, 8), (8, height - 8), (width - 8, height - 8))
                if tuple(pixels[y * width + x] for x, y in points) != MARKERS:
                    raise RuntimeError('目标窗口标记不匹配')
                counter = 0
                for bit in range(16):
                    color = pixels[38 * width + 26 + bit * 16]
                    if color not in (0, 0xFFFFFF):
                        raise RuntimeError('计数条像素不完整')
                    counter |= int(color == 0xFFFFFF) << bit
                row = {'phase': phase, 'sequence': len(report['samples']) + 1,
                       'counter': counter, 'captured_monotonic_ns': time.monotonic_ns(),
                       'width': width, 'height': height}
                if covered:
                    root_pixels = connection.image(connection.root, width, height, x=60, y=60)
                    row['root_cover_fraction'] = sum(p == COVER for p in root_pixels) / len(root_pixels)
                    if row['root_cover_fraction'] != 1.0:
                        raise RuntimeError('桌面图像未证实目标区域完全被覆盖')
                report['samples'].append(row)
                rows.append(row)
                with (output / 'samples.jsonl').open('a', encoding='utf-8') as log:
                    log.write(json.dumps(row) + '\n')
                if index in (0, count - 1):
                    png(output / f'{phase}-{index:02d}.png', pixels, width, height)
                    if covered and index == 0:
                        png(output / f'{phase}-root.png', root_pixels, width, height)
            return rows

        visible = collect('visible', 5)
        require_progress(visible)
        cover, cover_identity = start('cover')
        report['cover'] = cover_identity
        if len({os.getpid(), identity['pid'], cover_identity['pid']}) != 3:
            raise RuntimeError('采集、目标及遮挡窗口必须是独立进程')
        time.sleep(0.12)
        covered = collect('covered', 8, covered=True)
        require_progress(covered)
        report['frozen_ack'] = command(target, 'freeze')
        frozen = collect('frozen', 4, covered=True)
        if len({row['counter'] for row in frozen}) != 1:
            raise RuntimeError('冻结对照窗口仍在更新')
        try:
            require_progress(frozen)
        except RuntimeError:
            report['stale_frame_control_rejected'] = True
        else:
            raise RuntimeError('增长判据错误接受冻结帧')
        command(target, 'resize')
        connection.x.XFreePixmap(connection.display, pixmap)
        pixmap = connection.composite.XCompositeNameWindowPixmap(connection.display, window)
        connection.sync()
        resized = collect('resized-covered', 5, 400, 240, covered=True)
        require_progress(resized)
        report['ok'] = True
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        if connection:
            try:
                if pixmap:
                    connection.x.XFreePixmap(connection.display, pixmap)
                if redirected:
                    connection.composite.XCompositeUnredirectWindow(connection.display, report['target']['window'], 0)
                connection.sync()
            except Exception as exc:
                report['ok'] = False
                report['x11_cleanup_error'] = str(exc)
            finally:
                connection.close()
        cleanup = []
        for child in reversed(children):
            forced = False
            try:
                child.communicate('quit\n', timeout=3)
            except (subprocess.TimeoutExpired, BrokenPipeError):
                forced = True
                child.kill()
                child.communicate(timeout=3)
            cleanup.append({'pid': child.pid, 'returncode': child.returncode, 'forced': forced})
        report['cleanup'] = cleanup
        if any(row['returncode'] != 0 or row['forced'] for row in cleanup):
            report['ok'] = False
            report['cleanup_error'] = 'fixture 未正常退出'
        for log in logs:
            log.close()
        (output / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', choices=('target', 'cover'))
    parser.add_argument('--output', type=Path)
    parser.add_argument('--renderer', choices=('cpu', 'opengl'), default='cpu')
    args = parser.parse_args()
    if args.fixture:
        fixture(args.fixture)
    elif args.output:
        raise SystemExit(probe(args.output, args.renderer))
    else:
        parser.error('需要 --output')
