"""在全新独立桌面副本中验收 Wolf 原生桥，不启动或写入源游戏。"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gameqakit.control import BridgeClient, write_connection
from gameqakit.desktop import DesktopProcess
from gameqakit.sessions import read_text, write_json
from gameqakit import win32


def inventory(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def wait(check, seconds, process):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not process.alive():
            import ctypes
            from ctypes import wintypes
            code = wintypes.DWORD()
            ctypes.WinDLL('kernel32').GetExitCodeProcess(process.process, ctypes.byref(code))
            raise RuntimeError('owned_game_exited:' + hex(code.value))
        if value := check():
            return value
        time.sleep(.02)
    raise TimeoutError('condition_not_reached')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--dll', type=Path, required=True)
    parser.add_argument('--injector', type=Path, required=True)
    parser.add_argument('--locale-dir', type=Path, help='已核验的本机 Locale Emulator 目录，显式启用 CP932')
    parser.add_argument('--locale-host', type=Path, help='WolfLocaleHost.exe 路径')
    parser.add_argument('--steps', type=Path)
    parser.add_argument('--safety', action='store_true')
    parser.add_argument('--mcp', action='store_true')
    parser.add_argument('--mcp-steps', type=Path, help='通过真实 MCP 子进程执行的正常输入步骤 JSON')
    args = parser.parse_args()
    dll, injector = args.dll.resolve(strict=True), args.injector.resolve(strict=True)
    spec = importlib.util.spec_from_file_location('wolf_prepare', ROOT / 'plugins/wolf/prepare.py')
    prepare = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prepare)
    source, workspace = args.source.resolve(strict=True), args.workspace.resolve()
    before = inventory(source)
    record = prepare.prepare(source, workspace)
    write_json(workspace / 'source-before.json', before)
    runtime = Path(record['runtime'])
    bridge = workspace / 'bridge'
    bridge.mkdir()
    session = uuid.uuid4().hex
    result = {'steps': [], 'session_id': session, 'dll_sha256': hashlib.sha256(dll.read_bytes()).hexdigest(),
              'injector_sha256': hashlib.sha256(injector.read_bytes()).hexdigest()}
    process = None
    stop = threading.Event()
    trace = []
    tracer = None
    try:
        if args.locale_dir:
            from plugins.wolf.launch import LocaleProcess
            context = LocaleProcess(runtime / 'Game.exe', bridge=bridge, session=session, dll=dll,
                                    injector=injector, locale_dir=args.locale_dir, host_exe=args.locale_host)
        else:
            context = DesktopProcess([str(runtime / 'Game.exe')], cwd=runtime,
                                     env={'GAMEQAKIT_BRIDGE_DIR': str(bridge), 'GAMEQAKIT_SESSION_ID': session})
        with context as process:
            result['pid'], result['desktop'] = process.pid, process.desktop_name
            if args.locale_dir:
                result['locale_host_pid'] = process.host.pid
            else:
                meta = {'protocol': 1, 'session_id': session, 'pid': process.pid,
                        'owner_pid': os.getpid(), 'desktop': process.desktop_name,
                        'exe_path': process.argv[0], 'command_format': 'ini', 'control_transport': 'wolf-shm-v1'}
                write_connection(bridge, meta)
                loaded = subprocess.run([str(injector), str(process.pid), str(dll), str(bridge)],
                                        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=8)
                result['injector'] = {'returncode': loaded.returncode, 'stdout': loaded.stdout, 'stderr': loaded.stderr}
                assert loaded.returncode == 0, result['injector']
            stage_path = bridge / 'plugin-stage.txt'
            stage = wait(lambda: read_text(stage_path), 5, process)
            result['plugin_stage'] = stage
            assert stage == 'ready', stage
            result['locale'] = json.loads((bridge / 'locale-status.json').read_text(encoding='utf-8'))
            if args.locale_dir:
                assert result['locale']['ansi'] == 932, result['locale']
            process.run()
            client = BridgeClient(bridge, pid=process.pid, exe_path=process.argv[0])
            initial = wait(lambda: (s if (s := client.status()).get('connected') and s.get('input_ready') else None), 15, process)
            assert initial['paused'] and initial['held'] == [] and initial['frame'] == 0, initial
            time.sleep(2)
            result['initial'] = {'before': initial, 'after': client.status()}
            assert result['initial']['after']['frame'] == 0, result['initial']

            def sample():
                while not stop.wait(.025):
                    trace.append({**client.status(), 'foreground_is_target':
                                  win32.window_pid(win32.user32.GetForegroundWindow()) == process.pid})
            tracer = threading.Thread(target=sample, daemon=True)
            tracer.start()
            try:
                result['resume'] = client.resume(command_id='resume')
            except Exception:
                result['failure_windows'] = process.windows()
                for window in result['failure_windows']:
                    if min(window.get('client', [0, 0])) > 100:
                        try:
                            win32.capture(window['hwnd'], workspace / ('external-' + str(window['hwnd']) + '.png'))
                        except Exception as capture_error:
                            window['capture_error'] = repr(capture_error)
                raise
            write_json(workspace / 'result-partial.json', result)
            steps = json.loads(args.steps.read_text(encoding='utf-8')) if args.steps else [
                {'name': 'title-ready', 'action': 'wait', 'frames': 60},
                {'name': 'title-down', 'action': 'move', 'direction': 'down', 'frames': 1},
                {'name': 'title-up', 'action': 'move', 'direction': 'up', 'frames': 1}]
            for index, step in enumerate(steps):
                options = dict(step)
                label, action = options.pop('name', 'action-' + str(index)), options.pop('action')
                previous = client.status()
                item = client.action(action, command_id=label, **options)
                item['frame_delta'] = item['frame'] - previous['frame']
                result['steps'].append(item)
                write_json(workspace / 'result-partial.json', result)
                print(json.dumps({'id': label, 'frame_delta': item['frame_delta'],
                                  'frame': item['frame'], 'screenshot': item['screenshot']}), flush=True)
            if args.safety or args.mcp or args.mcp_steps:
                from wolf.protocol_checks import safety_checks, mcp_checks
                if args.safety:
                    result['safety'] = safety_checks(client, process, wait)
                if args.mcp or args.mcp_steps:
                    mcp_steps = json.loads(args.mcp_steps.read_text(encoding='utf-8')) if args.mcp_steps else []
                    result['mcp'] = mcp_checks(client, runtime / 'Game.exe', workspace, ROOT, mcp_steps)
            client.pause()
            result['paused'] = wait(lambda: (s if (s := client.status()).get('connected') and s['paused']
                                            and s['held'] == [] else None), 3, process)
            time.sleep(.4)
            assert client.status()['frame'] == result['paused']['frame']
            assert not any(s['foreground_is_target'] for s in trace)
            result['completed'] = True
    except Exception as error:
        result['error'] = repr(error)
        raise
    finally:
        stop.set()
        if tracer:
            tracer.join(timeout=2)
        write_json(workspace / 'status-trace.json', trace)
        result['process_reaped'] = not process.alive() if process else True
        after = inventory(source)
        write_json(workspace / 'source-after.json', after)
        result['source_changed'] = sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))
        write_json(workspace / 'runtime-after.json', inventory(runtime))
        write_json(workspace / 'result.json', result)
        if result['source_changed']:
            raise AssertionError('source_game_changed: ' + str(result['source_changed']))


if __name__ == '__main__':
    main()
