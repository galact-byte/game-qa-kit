"""仅服务同一启动进程、限定 HWND/PID 的本机窗口代理。可经 runas 启动。"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import ctypes as C
from ctypes import wintypes as W
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import threading
import time

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gameqakit import win32
from gameqakit.background import perform_action

k, u = win32.kernel32, win32.user32
a = C.WinDLL('advapi32', use_last_error=True)
s = C.WinDLL('shell32', use_last_error=True)
INVALID = C.c_void_p(-1).value
LIMIT = 8 * 1024 * 1024


class SECURITY_ATTRIBUTES(C.Structure):
    _fields_ = [('length', W.DWORD), ('descriptor', W.LPVOID), ('inherit', W.BOOL)]


class SHELLEXECUTEINFO(C.Structure):
    _fields_ = [('size', W.DWORD), ('mask', W.ULONG), ('hwnd', W.HWND), ('verb', W.LPCWSTR),
                ('file', W.LPCWSTR), ('parameters', W.LPCWSTR), ('directory', W.LPCWSTR),
                ('show', C.c_int), ('instance', W.HINSTANCE), ('idlist', W.LPVOID),
                ('class_name', W.LPCWSTR), ('class_key', W.HKEY), ('hotkey', W.DWORD),
                ('icon', W.HANDLE), ('process', W.HANDLE)]


def _signature(dll, name, args, result=W.BOOL):
    function = getattr(dll, name)
    function.argtypes, function.restype = args, result
    return function


_signature(k, 'CreateNamedPipeW', [W.LPCWSTR, W.DWORD, W.DWORD, W.DWORD, W.DWORD,
                                 W.DWORD, W.DWORD, C.POINTER(SECURITY_ATTRIBUTES)], W.HANDLE)
_signature(k, 'ConnectNamedPipe', [W.HANDLE, W.LPVOID])
_signature(k, 'DisconnectNamedPipe', [W.HANDLE])
_signature(k, 'GetNamedPipeClientProcessId', [W.HANDLE, C.POINTER(W.ULONG)])
_signature(k, 'GetNamedPipeServerProcessId', [W.HANDLE, C.POINTER(W.ULONG)])
_signature(k, 'WaitNamedPipeW', [W.LPCWSTR, W.DWORD])
_signature(k, 'CreateFileW', [W.LPCWSTR, W.DWORD, W.DWORD, W.LPVOID, W.DWORD,
                            W.DWORD, W.HANDLE], W.HANDLE)
_signature(k, 'ReadFile', [W.HANDLE, W.LPVOID, W.DWORD, C.POINTER(W.DWORD), W.LPVOID])
_signature(k, 'WriteFile', [W.HANDLE, W.LPVOID, W.DWORD, C.POINTER(W.DWORD), W.LPVOID])
_signature(k, 'PeekNamedPipe', [W.HANDLE, W.LPVOID, W.DWORD, W.LPVOID, C.POINTER(W.DWORD), W.LPVOID])
_signature(k, 'GetExitCodeProcess', [W.HANDLE, C.POINTER(W.DWORD)])
_signature(k, 'WaitForSingleObject', [W.HANDLE, W.DWORD], W.DWORD)
_signature(k, 'LocalFree', [W.HLOCAL], W.HLOCAL)
_signature(k, 'GetProcessTimes', [W.HANDLE, C.POINTER(W.FILETIME), C.POINTER(W.FILETIME),
                                C.POINTER(W.FILETIME), C.POINTER(W.FILETIME)])
_signature(a, 'OpenProcessToken', [W.HANDLE, W.DWORD, C.POINTER(W.HANDLE)])
_signature(a, 'GetTokenInformation', [W.HANDLE, C.c_int, W.LPVOID, W.DWORD, C.POINTER(W.DWORD)])
_signature(a, 'ConvertSidToStringSidW', [W.LPVOID, C.POINTER(W.LPWSTR)])
_signature(a, 'ConvertStringSecurityDescriptorToSecurityDescriptorW',
           [W.LPCWSTR, W.DWORD, C.POINTER(W.LPVOID), W.LPVOID])
_signature(s, 'ShellExecuteExW', [C.POINTER(SHELLEXECUTEINFO)])
_signature(u, 'IsWindow', [W.HWND])
_signature(u, 'PostMessageW', [W.HWND, W.UINT, W.WPARAM, W.LPARAM])
_signature(u, 'ClientToScreen', [W.HWND, C.POINTER(W.POINT)])
_signature(u, 'GetWindowDpiAwarenessContext', [W.HWND], W.HANDLE)
_signature(u, 'SetThreadDpiAwarenessContext', [W.HANDLE], W.HANDLE)


@contextmanager
def window_coordinates(hwnd):
    # 与目标的像素空间一致，避免高 DPI 客户区被虚拟化裁切；不改变进程全局设置。
    context = u.GetWindowDpiAwarenessContext(hwnd)
    if not context:
        raise fail()
    previous = u.SetThreadDpiAwarenessContext(context)
    if not previous:
        raise fail()
    try:
        yield
    finally:
        u.SetThreadDpiAwarenessContext(previous)


def fail():
    return C.WinError(C.get_last_error())


def alive(handle):
    value = W.DWORD()
    return bool(k.GetExitCodeProcess(handle, C.byref(value))) and value.value == 259


def process(pid):
    handle = k.OpenProcess(0x101000, False, pid)
    if not handle:
        raise fail()
    return handle


def user_sid(handle, information_class=1):
    token = W.HANDLE()
    if not a.OpenProcessToken(handle, 8, C.byref(token)):
        raise fail()
    try:
        needed = W.DWORD()
        a.GetTokenInformation(token, information_class, None, 0, C.byref(needed))
        buffer = C.create_string_buffer(needed.value)
        if not a.GetTokenInformation(token, information_class, buffer, needed, C.byref(needed)):
            raise fail()
        sid = C.cast(buffer, C.POINTER(W.LPVOID))[0]
        text = W.LPWSTR()
        if not a.ConvertSidToStringSidW(sid, C.byref(text)):
            raise fail()
        try:
            return text.value
        finally:
            k.LocalFree(C.cast(text, W.HLOCAL))
    finally:
        k.CloseHandle(token)


def created(handle):
    values = [W.FILETIME() for _ in range(4)]
    if not k.GetProcessTimes(handle, *(C.byref(value) for value in values)):
        raise fail()
    return (values[0].dwHighDateTime << 32) | values[0].dwLowDateTime


def check_window(hwnd, pid):
    if not u.IsWindow(hwnd) or win32.window_pid(hwnd) != pid:
        raise RuntimeError('窗口已关闭或 HWND/PID 身份不匹配')
    if u.IsIconic(hwnd):
        raise RuntimeError('窗口已最小化；后台工具不自动恢复或置前台')
    with window_coordinates(hwnd):
        width, height = win32.client_size(hwnd)
    if not 1 <= width <= 8192 or not 1 <= height <= 8192 or width * height > 16777216:
        raise RuntimeError('窗口客户区尺寸无效或过大')
    return width, height


def launch(pipe, hwnd, pid, elevate):
    executable = Path(sys.executable)
    if getattr(sys, 'frozen', False):
        args = ['-m', 'gameqakit.background_agent']
        directory = executable.resolve().parent
    else:
        windowless = executable.with_name('pythonw.exe')
        if windowless.is_file():
            executable = windowless
        args = ['-B', '-X', 'utf8', str(Path(__file__).resolve())]
        directory = Path(__file__).resolve().parents[1]
    args += ['--pipe', pipe, '--hwnd', str(hwnd), '--pid', str(pid), '--parent', str(os.getpid())]
    if elevate:
        info = SHELLEXECUTEINFO()
        info.size, info.mask = C.sizeof(info), 0x40
        info.verb, info.file = 'runas', str(executable)
        info.parameters = subprocess.list2cmdline(args)
        info.directory, info.show = str(directory), 0
        if not s.ShellExecuteExW(C.byref(info)):
            if C.get_last_error() == 1223:
                raise RuntimeError('老师取消了 UAC 同意，后台代理未启动')
            raise fail()
        _signature(k, 'GetProcessId', [W.HANDLE], W.DWORD)
        return info.process, int(k.GetProcessId(info.process)), None
    child = subprocess.Popen([str(executable), *args], cwd=directory,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, creationflags=0x08000000)
    return process(child.pid), child.pid, child


def _read(handle, size, deadline):
    chunks = bytearray()
    while len(chunks) < size:
        if time.monotonic() >= deadline:
            raise TimeoutError('后台代理通信超时；禁止换编号重发输入')
        available = W.DWORD()
        if not k.PeekNamedPipe(handle, None, 0, None, C.byref(available), None):
            raise fail()
        if not available.value:
            time.sleep(.01)
            continue
        amount = min(available.value, size - len(chunks), 65536)
        buffer, count = C.create_string_buffer(amount), W.DWORD()
        if not k.ReadFile(handle, buffer, amount, C.byref(count), None):
            raise fail()
        chunks.extend(buffer.raw[:count.value])
    return bytes(chunks)


def read_packet(handle, timeout=25):
    deadline = time.monotonic() + timeout
    size = struct.unpack('<I', _read(handle, 4, deadline))[0]
    if not 1 <= size <= LIMIT:
        raise ValueError('代理消息大小无效')
    result = json.loads(_read(handle, size, deadline))
    if not isinstance(result, dict):
        raise ValueError('代理消息必须是对象')
    return result


def write_packet(handle, value):
    raw = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if len(raw) > LIMIT:
        raise ValueError('代理响应超过大小限制')
    data = struct.pack('<I', len(raw)) + raw
    count = W.DWORD()
    if not k.WriteFile(handle, data, len(data), C.byref(count), None) or count.value != len(data):
        raise fail()


def request(pipe, server_pid, value):
    if not k.WaitNamedPipeW(pipe, 500):
        raise fail()
    handle = k.CreateFileW(pipe, 0xc0000000, 0, None, 3, 0, None)
    if handle == INVALID:
        raise fail()
    try:
        owner = W.ULONG()
        if not k.GetNamedPipeServerProcessId(handle, C.byref(owner)) or owner.value != server_pid:
            raise RuntimeError('命名管道服务进程身份不匹配')
        write_packet(handle, value)
        return read_packet(handle)
    finally:
        k.CloseHandle(handle)


class Agent:
    def __init__(self, hwnd, pid, parent):
        self.hwnd, self.pid, self.parent_pid = hwnd, pid, parent
        self.parent = self.target = self.self_process = None
        try:
            self.parent = process(parent)
            self.target = process(pid)
            self.self_process = process(os.getpid())
            self.sid = user_sid(self.self_process)
            if user_sid(self.parent) != self.sid or user_sid(self.target) != self.sid:
                raise RuntimeError('只允许同用户的启动进程和目标游戏')
            self.integrity = {name: int(user_sid(handle, 25).rsplit('-', 1)[1]) for name, handle in
                              (('agent', self.self_process), ('parent', self.parent), ('target', self.target))}
            self.target_created = created(self.target)
            self.exe = win32._pid_path(pid)
            check_window(hwnd, pid)
        except BaseException:
            self.close()
            raise
        self.held, self.point = False, (0, 0)
        self.last_action = 0
        self.cache = {}
        self.stop_event = threading.Event()
        self.deadline = time.monotonic() + 900

    def close(self):
        for handle in (self.parent, self.target, self.self_process):
            if handle:
                k.CloseHandle(handle)
        self.parent = self.target = self.self_process = None

    def validate(self):
        if not alive(self.target) or created(self.target) != self.target_created:
            raise RuntimeError('绑定目标已退出，不跟随重启进程')
        return check_window(self.hwnd, self.pid)

    def post(self, message, wparam, x, y):
        self.validate()
        self.point = (x, y)
        if message == 0x20A:
            point = W.POINT(x, y)
            with window_coordinates(self.hwnd):
                converted = u.ClientToScreen(self.hwnd, C.byref(point))
            if not converted:
                raise fail()
            x, y = point.x, point.y
            if not -32768 <= x <= 32767 or not -32768 <= y <= 32767:
                raise ValueError('滚轮屏幕坐标超出 Windows 消息范围')
        lparam = (x & 0xffff) | ((y & 0xffff) << 16)
        with window_coordinates(self.hwnd):
            posted = u.PostMessageW(self.hwnd, message, wparam, lparam)
        if not posted:
            raise fail()
        if message in (0x201, 0x202):
            self.held = message == 0x201

    def release(self):
        if self.held and u.IsWindow(self.hwnd) and win32.window_pid(self.hwnd) == self.pid:
            try:
                self.post(0x202, 0, *self.point)
            except (OSError, RuntimeError):
                pass

    def watchdog(self):
        while not self.stop_event.wait(.1):
            if not alive(self.parent) or not alive(self.target) or time.monotonic() > self.deadline:
                self.release()
                os._exit(3)

    def dispatch(self, request):
        self.deadline = time.monotonic() + 25
        try:
            op = request.get('op')
            if op not in ('status', 'stop', 'capture', 'input') or set(request) != ({'op', 'action'} if op == 'input' else {'op'}):
                raise ValueError('代理不支持该操作或额外字段')
            if op == 'stop':
                self.release()
                self.stop_event.set()
                return {'running': False, 'ready': False, 'release_posted': not self.held}
            width, height = self.validate()
            if op == 'status':
                return {'running': True, 'ready': True, 'hwnd': self.hwnd, 'pid': self.pid,
                        'exe_path': self.exe, 'process_created': self.target_created, 'size': [width, height],
                        'agent_pid': os.getpid(), 'integrity_rids': self.integrity, 'held': self.held, 'capture_backend': 'PrintWindow',
                        'input_backend': 'PostMessage', 'coordinate_space': 'target_client_pixels',
                        'game_responded': None}
            if op == 'capture':
                with window_coordinates(self.hwnd):
                    image = win32.grab_image(self.hwnd)
                stream = io.BytesIO()
                image.save(stream, 'PNG')
                return {'ok': True, 'hwnd': self.hwnd, 'pid': self.pid, 'size': list(image.size),
                        'backend': 'PrintWindow', 'png': base64.b64encode(stream.getvalue()).decode('ascii')}
            action = request['action']
            from gameqakit.background import validate_action
            action = validate_action(action, width, height)
            ident = action.get('command_id')
            import re
            if not isinstance(ident, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', ident):
                raise ValueError('command_id 无效')
            if ident in self.cache:
                original, result = self.cache[ident]
                if original != action:
                    raise ValueError('同 command_id 不允许替换动作')
                return result
            if len(self.cache) >= 512:
                raise RuntimeError('本代理已用满 512 个命令编号，请停止并创建新代理')
            result = {'error': '输入未完成；同编号不重发，请先核对画面'}
            self.cache[ident] = (action, result)
            time.sleep(max(0, self.last_action + action['interval_ms'] / 1000 - time.monotonic()))
            try:
                result = {**perform_action(action, width, height, self.post), 'hwnd': self.hwnd,
                          'pid': self.pid, 'command_id': ident}
            except Exception as error:
                result = {'error': str(error), 'command_id': ident}
            finally:
                self.last_action = time.monotonic()
            self.cache[ident] = (action, result)
            return result
        finally:
            self.deadline = time.monotonic() + 900

    def serve(self, pipe):
        descriptor = W.LPVOID()
        # Medium IL 客户端可写；DACL 仍只接受当前用户，服务端再核对精确启动 PID。
        sddl = 'D:P(A;;GA;;;' + self.sid + ')S:(ML;;NW;;;ME)'
        if not a.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, C.byref(descriptor), None):
            raise fail()
        security = SECURITY_ATTRIBUTES(C.sizeof(SECURITY_ATTRIBUTES), descriptor, False)
        try:
            handle = k.CreateNamedPipeW(pipe, 0x80003, 8, 1, 65536, 65536, 500, C.byref(security))
        finally:
            k.LocalFree(descriptor)
        if handle == INVALID:
            raise fail()
        threading.Thread(target=self.watchdog, daemon=True).start()
        try:
            while not self.stop_event.is_set():
                if not k.ConnectNamedPipe(handle, None) and C.get_last_error() != 535:
                    raise fail()
                try:
                    owner = W.ULONG()
                    if not k.GetNamedPipeClientProcessId(handle, C.byref(owner)) or owner.value != self.parent_pid:
                        continue
                    try:
                        result = self.dispatch(read_packet(handle, timeout=3))
                    except Exception as error:
                        result = {'error': str(error)}
                    write_packet(handle, result)
                    # 等待客户端读取，避免 DisconnectNamedPipe 丢弃还在缓冲区的响应。
                    _signature(k, 'FlushFileBuffers', [W.HANDLE])
                    k.FlushFileBuffers(handle)
                finally:
                    k.DisconnectNamedPipe(handle)
        finally:
            self.stop_event.set()
            self.release()
            k.CloseHandle(handle)
            self.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pipe', required=True)
    parser.add_argument('--hwnd', type=int, required=True)
    parser.add_argument('--pid', type=int, required=True)
    parser.add_argument('--parent', type=int, required=True)
    args = parser.parse_args()
    import re
    if not re.fullmatch(r'\\\\\.\\pipe\\gameqa-bg-[a-f0-9]{32}', args.pipe):
        raise ValueError('管道名称无效')
    Agent(args.hwnd, args.pid, args.parent).serve(args.pipe)


if __name__ == '__main__':
    main()
