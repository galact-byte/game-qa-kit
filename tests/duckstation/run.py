"""Explicit isolated emulator experiment; never sends system keyboard or mouse input."""
from __future__ import annotations
import argparse
import ctypes as C
from ctypes import wintypes as W
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from gameqakit.desktop import DesktopProcess
from gameqakit import win32
from PIL import Image

ROOT = RUNTIME = GAME = None
k = C.WinDLL('kernel32', use_last_error=True)
k.VirtualAllocEx.argtypes = [W.HANDLE, C.c_void_p, C.c_size_t, W.DWORD, W.DWORD]
k.VirtualAllocEx.restype = C.c_void_p
k.VirtualFreeEx.argtypes = [W.HANDLE, C.c_void_p, C.c_size_t, W.DWORD]
k.WriteProcessMemory.argtypes = [W.HANDLE, C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)]
k.GetModuleHandleW.argtypes = [W.LPCWSTR]
k.GetModuleHandleW.restype = W.HMODULE
k.GetModuleHandleExW.argtypes = [W.DWORD, C.c_void_p, C.POINTER(W.HMODULE)]
k.GetProcAddress.argtypes = [W.HMODULE, C.c_char_p]
k.GetProcAddress.restype = C.c_void_p
k.GetModuleFileNameW.argtypes = [W.HMODULE, W.LPWSTR, W.DWORD]
k.CreateToolhelp32Snapshot.argtypes = [W.DWORD, W.DWORD]
k.CreateToolhelp32Snapshot.restype = W.HANDLE
k.CreateRemoteThread.argtypes = [W.HANDLE, C.c_void_p, C.c_size_t, C.c_void_p, C.c_void_p, W.DWORD, C.POINTER(W.DWORD)]
k.CreateRemoteThread.restype = W.HANDLE
k.GetTickCount64.restype = C.c_uint64
k.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]
k.CloseHandle.argtypes = [W.HANDLE]
k.GetExitCodeThread.argtypes = [W.HANDLE, C.POINTER(W.DWORD)]

class ModuleEntry(C.Structure):
    _fields_ = [('size', W.DWORD), ('module_id', W.DWORD), ('pid', W.DWORD), ('global_uses', W.DWORD),
                ('uses', W.DWORD), ('base', C.c_void_p), ('bytes', W.DWORD), ('module', W.HMODULE),
                ('name', W.WCHAR * 256), ('path', W.WCHAR * 260)]

k.Module32FirstW.argtypes = [W.HANDLE, C.POINTER(ModuleEntry)]
k.Module32NextW.argtypes = [W.HANDLE, C.POINTER(ModuleEntry)]


def remote_load_address(pid):
    address = k.GetProcAddress(k.GetModuleHandleW('kernel32.dll'), b'LoadLibraryW')
    local_module = W.HMODULE()
    if not address or not k.GetModuleHandleExW(6, address, C.byref(local_module)):
        raise C.WinError(C.get_last_error())
    name = C.create_unicode_buffer(32768)
    if not k.GetModuleFileNameW(local_module, name, len(name)):
        raise C.WinError(C.get_last_error())
    target_name = Path(name.value).name.casefold()
    snapshot = k.CreateToolhelp32Snapshot(0x18, pid)
    if snapshot == W.HANDLE(-1).value:
        raise C.WinError(C.get_last_error())
    try:
        row = ModuleEntry()
        row.size = C.sizeof(row)
        ok = k.Module32FirstW(snapshot, C.byref(row))
        while ok:
            if row.name.casefold() == target_name:
                return row.base + address - local_module.value
            ok = k.Module32NextW(snapshot, C.byref(row))
    finally:
        k.CloseHandle(snapshot)
    raise RuntimeError('Remote loader module not found')


def inject(process, dll):
    if not process.alive() or not str(process.cwd).startswith(str(ROOT)):
        raise RuntimeError('Refuse injection outside owned test runtime')
    raw = (str(dll.resolve()) + '\0').encode('utf-16-le')
    memory = k.VirtualAllocEx(process.process, None, len(raw), 0x3000, 4)
    if not memory:
        raise C.WinError(C.get_last_error())
    thread = None
    finished = False
    try:
        written = C.c_size_t()
        if not k.WriteProcessMemory(process.process, memory, raw, len(raw), C.byref(written)) or written.value != len(raw):
            raise C.WinError(C.get_last_error())
        tid = W.DWORD()
        thread = k.CreateRemoteThread(process.process, None, 0, remote_load_address(process.pid), memory, 0, C.byref(tid))
        if not thread:
            raise C.WinError(C.get_last_error())
        if k.WaitForSingleObject(thread, 5000) != 0:
            raise RuntimeError('Remote loader did not finish')
        finished = True
        exit_code = W.DWORD()
        if not k.GetExitCodeThread(thread, C.byref(exit_code)) or not exit_code.value:
            raise RuntimeError('LoadLibraryW failed')
    finally:
        if thread:
            k.CloseHandle(thread)
        if finished:
            k.VirtualFreeEx(process.process, memory, 0, 0x8000)


def read_state(folder):
    path = folder / 'probe-state.txt'
    try:
        text = path.read_text()
    except (FileNotFoundError, PermissionError):
        return {}
    return dict(line.split('=', 1) for line in text.splitlines() if '=' in line)


def wait_for(check, seconds, process):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not process.alive():
            raise RuntimeError('Owned emulator exited')
        result = check()
        if result:
            return result
        time.sleep(0.05)
    raise TimeoutError('Probe condition timeout')


class Commands:
    def __init__(self, folder, process):
        self.folder, self.process, self.seq = folder, process, 0

    def set(self, mask, lease=800):
        self.seq += 1
        tmp = self.folder / 'command.tmp'
        tmp.write_text(f'{self.seq} {k.GetTickCount64() + lease} {mask}\n')
        for attempt in range(20):
            try:
                tmp.replace(self.folder / 'probe-command.txt')
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.01)
        wait_for(lambda: int(read_state(self.folder).get('consumed_seq', 0)) >= self.seq, 3, self.process)
        return read_state(self.folder)

    def tap(self, button, hold=0.12):
        self.set(1 << button)
        time.sleep(hold)
        self.set(0)


def snapshot(commands, folder, name):
    screenshots = RUNTIME / 'screenshots'
    before = set(screenshots.glob('*.png'))
    commands.tap(15)
    def loaded_image():
        for path in screenshots.glob('*.png'):
            if path in before:
                continue
            try:
                with Image.open(path) as image:
                    image.load()
                    return image.convert('RGB')
            except (OSError, ValueError):
                continue
        return None
    raw = wait_for(loaded_image, 5, commands.process)
    output = folder / (name + '.png')
    extrema = raw.getextrema()
    if max(high - low for low, high in extrema) < 10:
        raise RuntimeError('Emulator screenshot is blank; stop input')
    raw.save(output)
    return {'path': str(output), 'sha256': hashlib.sha256(output.read_bytes()).hexdigest(), 'size': list(raw.size)}


def safety_checks(commands, folder):
    held = 1 << 12
    commands.set(held | (1 << 16))
    time.sleep(0.12)
    commands.set(held, lease=250)
    active = wait_for(lambda: (s if int((s := read_state(folder)).get('reported', 0)) == held else None),
                      2, commands.process)
    released = wait_for(lambda: (s if int((s := read_state(folder)).get('reported', -1)) == 0
                                and int(s.get('applied', -1)) == 0 else None), 2, commands.process)
    paused_first = snapshot(commands, folder, 'paused-first')
    time.sleep(0.4)
    paused_second = snapshot(commands, folder, 'paused-second')
    if paused_first['sha256'] != paused_second['sha256']:
        raise AssertionError('Game framebuffer changed during pause')
    commands.tap(16)
    running = commands.set(1 << 11, lease=250)
    wait_for(lambda: int(read_state(folder).get('reported', 0)) == (1 << 11), 2, commands.process)
    running_released = wait_for(lambda: (s if int((s := read_state(folder)).get('reported', -1)) == 0
                                        and int(s.get('applied', -1)) == 0 else None), 2, commands.process)
    resumed = snapshot(commands, folder, 'resumed-after-lease')
    return {'held_when_pausing': active, 'released_while_paused': released,
            'paused_screenshots': [paused_first, paused_second],
            'running_command': running, 'released_while_running': running_released,
            'resumed': resumed}


def main():
    global ROOT, RUNTIME, GAME
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--game', type=Path, required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--inject', action='store_true')
    parser.add_argument('--steps', type=Path)
    parser.add_argument('--boot-wait', type=float, default=24)
    parser.add_argument('--safety', action='store_true')
    args = parser.parse_args()
    ROOT = args.workspace.resolve()
    RUNTIME = ROOT / 'runtime'
    GAME = args.game.resolve(strict=True)
    if not (ROOT / 'source-baseline.json').is_file() or not (RUNTIME / 'portable.txt').is_file():
        parser.error('Use an explicitly prepared, isolated workspace')
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    foreground = int(win32.user32.GetForegroundWindow() or 0)
    result = {'injected': args.inject, 'foreground_before': foreground, 'actions': []}
    process = None
    try:
        with DesktopProcess([str(RUNTIME / 'duckstation-qt-x64-ReleaseLTCG.exe'), '-batch', '--', str(GAME)],
                            cwd=RUNTIME, env={'GAMEQAKIT_PS1_DIR': str(out)}) as process:
            result.update(pid=process.pid, desktop=process.desktop_name)
            process.run()
            windows = wait_for(lambda: [w for w in process.windows() if min(w['client']) > 150], 20, process)
            result['windows'] = windows
            if not args.inject:
                time.sleep(12)
                windows = process.windows()
                result['windows'] = windows
                for index, window in enumerate(windows):
                    if min(window['client']) > 150:
                        try:
                            path = out / f'window-{index}.png'
                            win32.capture(window['hwnd'], path)
                            result['actions'].append({'capture': str(path), 'window': window})
                        except Exception as exc:
                            result['actions'].append({'capture_error': str(exc), 'window': window})
            else:
                inject(process, ROOT / 'GameQaPs1Probe.dll')
                wait_for(lambda: read_state(out).get('phase') in ('running', 'failed'), 20, process)
                state = read_state(out)
                if state.get('phase') != 'running':
                    raise RuntimeError(state)
                commands = Commands(out, process)
                commands.set(0)
                time.sleep(args.boot_wait)
                result['actions'].append({'snapshot': snapshot(commands, out, 'initial'), 'state': read_state(out)})
                steps = json.loads(args.steps.read_text(encoding='utf-8')) if args.steps else []
                for step in steps:
                    if 'button' in step:
                        commands.tap(step['button'], step.get('hold', 0.12))
                    time.sleep(step.get('wait', 0.8))
                    item = {'step': step, 'snapshot': snapshot(commands, out, step['name']), 'state': read_state(out)}
                    result['actions'].append(item)
                    print(json.dumps(item), flush=True)
                if args.safety:
                    result['safety'] = safety_checks(commands, out)
                commands.set(0)
                result['released'] = read_state(out)
            result['foreground_after'] = int(win32.user32.GetForegroundWindow() or 0)
            result['foreground_is_target'] = win32.window_pid(result['foreground_after']) == process.pid if result['foreground_after'] else False
            result['completed'] = True
    except Exception as exc:
        result['error'] = repr(exc)
        raise
    finally:
        log = RUNTIME / 'duckstation.log'
        if log.exists():
            shutil.copy2(log, out / 'duckstation.log')
        result['process_reaped'] = not process.alive() if process else True
        (out / 'result.json').write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
        print(json.dumps(result, indent=2, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
