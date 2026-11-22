"""自写 GLX/OpenGL X11 窗口，供独立 XComposite 探针使用。"""
from __future__ import annotations

import ctypes as C
import ctypes.util
import json
import os
import select
import sys

from probe_x11_capture import BACKGROUND, MARKERS, X11


class VisualInfo(C.Structure):
    _fields_ = [('visual', C.c_void_p), ('visualid', C.c_ulong), ('screen', C.c_int),
                ('depth', C.c_int), ('visual_class', C.c_int), ('red_mask', C.c_ulong),
                ('green_mask', C.c_ulong), ('blue_mask', C.c_ulong),
                ('colormap_size', C.c_int), ('bits_per_rgb', C.c_int)]


class WindowAttributes(C.Structure):
    _fields_ = [('background_pixmap', C.c_ulong), ('background_pixel', C.c_ulong),
                ('border_pixmap', C.c_ulong), ('border_pixel', C.c_ulong),
                ('bit_gravity', C.c_int), ('win_gravity', C.c_int), ('backing_store', C.c_int),
                ('backing_planes', C.c_ulong), ('backing_pixel', C.c_ulong), ('save_under', C.c_int),
                ('event_mask', C.c_long), ('do_not_propagate_mask', C.c_long),
                ('override_redirect', C.c_int), ('colormap', C.c_ulong), ('cursor', C.c_ulong)]


def fixture():
    x11 = X11()
    gl = C.CDLL(ctypes.util.find_library('GL') or 'libGL.so.1')
    pointer, integer, window = C.c_void_p, C.c_int, C.c_ulong
    for name, restype, args in (
        ('glXChooseVisual', C.POINTER(VisualInfo), [pointer, integer, C.POINTER(integer)]),
        ('glXCreateContext', pointer, [pointer, C.POINTER(VisualInfo), pointer, integer]),
        ('glXMakeCurrent', integer, [pointer, window, pointer]),
        ('glXSwapBuffers', None, [pointer, window]),
        ('glXDestroyContext', None, [pointer, pointer]),
        ('glXWaitX', None, []),
        ('glGetString', C.c_char_p, [C.c_uint]),
        ('glClearColor', None, [C.c_float] * 4),
        ('glClear', None, [C.c_uint]),
        ('glEnable', None, [C.c_uint]),
        ('glDisable', None, [C.c_uint]),
        ('glScissor', None, [integer] * 4),
        ('glViewport', None, [integer] * 4),
        ('glFinish', None, []),
    ):
        function = getattr(gl, name)
        function.restype, function.argtypes = restype, args
    for name, restype, args in (
        ('XCreateColormap', window, [pointer, window, pointer, integer]),
        ('XFreeColormap', integer, [pointer, window]),
        ('XCreateWindow', window, [pointer, window, integer, integer, C.c_uint, C.c_uint,
                                  C.c_uint, integer, C.c_uint, pointer, window,
                                  C.POINTER(WindowAttributes)]),
        ('XFree', integer, [pointer]),
    ):
        function = getattr(x11.x, name)
        function.restype, function.argtypes = restype, args
    visual, context, colormap, target = None, None, 0, 0
    width, height, counter, frozen = 320, 180, 1, False
    try:
        attributes = (integer * 10)(4, 5, 8, 8, 9, 8, 10, 8, 0, 0)
        visual = gl.glXChooseVisual(x11.display, x11.x.XDefaultScreen(x11.display), attributes)
        if not visual or visual.contents.depth != 24:
            raise RuntimeError('没有适合本实验的 24 位 GLX visual')
        colormap = x11.x.XCreateColormap(x11.display, x11.root, visual.contents.visual, 0)
        window_attributes = WindowAttributes(colormap=colormap)
        target = x11.x.XCreateWindow(x11.display, x11.root, 60, 60, width, height, 0,
                                    visual.contents.depth, 1, visual.contents.visual,
                                    (1 << 13) | (1 << 3), C.byref(window_attributes))
        x11.x.XStoreName(x11.display, target, b'gameqa-x11-opengl-target')
        x11.set_pid(target)
        x11.x.XMapRaised(x11.display, target)
        x11.sync()
        context = gl.glXCreateContext(x11.display, visual, None, True)
        if not context or not gl.glXMakeCurrent(x11.display, target, context):
            raise RuntimeError('无法建立 GLX 绘制上下文')
        graphics = {name: (gl.glGetString(token) or b'unknown').decode(errors='replace')
                    for name, token in (('vendor', 0x1F00), ('renderer', 0x1F01), ('version', 0x1F02))}
        gl.glDisable(0x0BD0)

        def clear(color):
            gl.glClearColor(((color >> 16) & 255) / 255, ((color >> 8) & 255) / 255,
                            (color & 255) / 255, 1)
            gl.glClear(0x4000)

        def rectangle(x, y, w, h, color):
            gl.glScissor(x, height - y - h, w, h)
            clear(color)

        def draw():
            gl.glViewport(0, 0, width, height)
            gl.glDisable(0x0C11)
            clear(BACKGROUND)
            gl.glEnable(0x0C11)
            for (x, y), color in zip(((0, 0), (width - 16, 0), (0, height - 16),
                                     (width - 16, height - 16)), MARKERS):
                rectangle(x, y, 16, 16, color)
            for bit in range(16):
                rectangle(20 + bit * 16, 28, 12, 20, 0xFFFFFF if counter & (1 << bit) else 0)
            gl.glDisable(0x0C11)
            gl.glXSwapBuffers(x11.display, target)
            gl.glFinish()
            x11.sync()

        draw()
        print(json.dumps({'window': target, 'pid': os.getpid(), 'graphics': graphics}), flush=True)
        while True:
            if select.select([sys.stdin], [], [], .04)[0]:
                event = sys.stdin.readline().strip()
                if event in ('', 'quit'):
                    break
                if event == 'freeze':
                    frozen = True
                elif event == 'resize':
                    frozen, width, height = False, 400, 240
                    x11.x.XResizeWindow(x11.display, target, width, height)
                    x11.sync()
                    gl.glXWaitX()
                else:
                    raise ValueError('未知 GLX 测试窗口命令')
                draw()
                print(json.dumps({'event': event, 'counter': counter}), flush=True)
            if not frozen:
                counter += 1
                draw()
    finally:
        if context:
            gl.glXMakeCurrent(x11.display, 0, None)
            gl.glXDestroyContext(x11.display, context)
        if target:
            x11.x.XDestroyWindow(x11.display, target)
        if colormap:
            x11.x.XFreeColormap(x11.display, colormap)
        if visual:
            x11.x.XFree(visual)
        x11.close()


if __name__ == '__main__':
    fixture()
