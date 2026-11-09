"""在已准备的隔离副本中验收 DuckStation 协议桥，不操作原模拟器。"""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import time
import threading
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gameqakit.control import BridgeClient, write_connection
from gameqakit.sessions import write_json

spec = importlib.util.spec_from_file_location('duckstation_probe_runner', ROOT / 'tests/duckstation/run.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def wait(check, seconds, process):
    return probe.wait_for(check, seconds, process)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--dll', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--steps', type=Path)
    parser.add_argument('--boot-wait', type=float, default=4)
    parser.add_argument('--safety', action='store_true', help='验证暂停、租约、会话与截图失败路径')
    parser.add_argument('--mcp', action='store_true', help='验证文件路径启动的真实 MCP stdio 服务')
    args = parser.parse_args()
    workspace = args.workspace.resolve(strict=True)
    record = json.loads((workspace / 'preparation.json').read_text(encoding='utf-8'))
    runtime, game = Path(record['runtime']), Path(record['game'])
    if runtime != workspace / 'runtime' or not (runtime / 'portable.txt').is_file():
        raise RuntimeError('拒绝未经准备的运行副本')
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    bridge = out / 'bridge'
    bridge.mkdir()
    session = uuid.uuid4().hex
    probe.ROOT = workspace
    result = {'steps': [], 'session_id': session}
    process = None
    trace = []
    stop = threading.Event()
    tracer = None
    try:
        with probe.DesktopProcess([str(runtime / 'duckstation-qt-x64-ReleaseLTCG.exe'), '-batch', '--', str(game)],
                                  cwd=runtime, env={'GAMEQAKIT_BRIDGE_DIR': str(bridge), 'GAMEQAKIT_SESSION_ID': session,
                                                    'GAMEQAKIT_PS1_DIR': str(out)}) as process:
            meta = {'protocol': 1, 'session_id': session, 'pid': process.pid,
                    'exe_path': process.argv[0], 'command_format': 'ini'}
            write_connection(bridge, meta)
            process.run()
            wait(lambda: process.windows(), 20, process)
            probe.inject(process, args.dll)
            client = BridgeClient(bridge, pid=process.pid, exe_path=process.argv[0])
            wait(lambda: client.status().get('connected') and client.status().get('input_ready'), 10, process)
            result['initial'] = client.status()
            def sample():
                while not stop.wait(0.03):
                    trace.append(client.status())
            tracer = threading.Thread(target=sample, daemon=True)
            tracer.start()
            time.sleep(args.boot_wait)
            result['resume'] = client.resume(command_id='resume', timeout=5)
            steps = json.loads(args.steps.read_text(encoding='utf-8')) if args.steps else []
            for index, step in enumerate(steps):
                options = dict(step)
                name = options.pop('name', 'action-' + str(index))
                action = options.pop('action')
                item = client.action(action, command_id=name, timeout=5, **options)
                result['steps'].append(item)
                print(json.dumps({'id': name, 'frame': item['frame'], 'screenshot': item['screenshot']}), flush=True)
            if args.safety or args.mcp:
                from duckstation.protocol_checks import safety_checks, mcp_checks
                if args.safety:
                    result['safety'] = safety_checks(client, process, wait)
                if args.mcp:
                    result['mcp'] = mcp_checks(client, Path(process.argv[0]), out, ROOT)
            client.pause()
            result['paused'] = wait(lambda: (s if (s := client.status()).get('connected') and s.get('paused')
                                             and s.get('held') == [] and s.get('native_buttons') == 0
                                             and s.get('device_buttons') == 0 else None), 3, process)
            result['foreground_is_target'] = probe.win32.window_pid(probe.win32.user32.GetForegroundWindow()) == process.pid
            assert not result['foreground_is_target']
            result['completed'] = True
    except Exception as exc:
        result['error'] = repr(exc)
        raise
    finally:
        stop.set()
        if tracer:
            tracer.join(timeout=2)
        write_json(out / 'status-trace.json', trace)
        result['process_reaped'] = not process.alive() if process else True
        if (runtime / 'duckstation.log').is_file():
            shutil.copy2(runtime / 'duckstation.log', out / 'duckstation.log')
        write_json(out / 'result.json', result)


if __name__ == '__main__':
    main()
