"""通过真实 stdio MCP 验证固定 x86 构建的标题、设置与返回；原数据只读。"""
import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from plugins.native_input.prepare import prepare, manifest
from gameqakit.sessions import write_json
from tests.native_input_mcp import mcp
from tests.native_input_safety import alive, connect
from tests.native_input_smoke import read, until


def run(source, output, binaries):
    record = prepare(source, output)
    dll = binaries / 'native-input.dll'
    exe = output / 'runtime/game.exe'
    checks, actions, foreground = {}, [], []
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.GetForegroundWindow.restype = wintypes.HWND
    stop_sampling = threading.Event()

    def sample():
        while not stop_sampling.is_set():
            foreground.append({'at': time.time(), 'window': int(user.GetForegroundWindow() or 0)})
            stop_sampling.wait(.02)

    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    failure = None
    host = None
    target_pid = None
    try:
        with (output / 'host.log').open('w', encoding='utf-8') as log:
            host = subprocess.Popen([str(binaries / 'native-host.exe'), str(output), str(dll),
                                     hashlib.sha256(dll.read_bytes()).hexdigest()], stdout=log, stderr=log)
            bridge, meta = connect(output, host, exe)
            target_pid = meta['pid']
            game_window = bridge.status()['window_id']
            with mcp(output, exe) as rpc:
                resumed = rpc.call('control_resume', confirm=True, command_id='menu-resume', include_image=True)
                actions.append({'label': '00-title', 'result': resumed})
                steps = [('01-down', 'move', 120, 'down'), ('02-down', 'move', 120, 'down'),
                         ('03-settings', 'confirm', 120, None), ('04-settings-ready', 'wait', 700, None),
                         ('05-return', 'cancel', 120, None), ('06-title-ready', 'wait', 700, None)]
                for label, action, duration, direction in steps:
                    args = {'action': action, 'duration_ms': duration, 'command_id': label,
                            'confirm': True, 'include_image': True}
                    if direction:
                        args['direction'] = direction
                    result = rpc.call('control_action', **args)
                    path = Path(result['screenshot'])
                    assert result['status'] == 'done' and result['held'] == [], result
                    assert hashlib.sha256(path.read_bytes()).hexdigest() == result['screenshot_sha256']
                    if action != 'wait':
                        assert result['press_consumed'] and result['release_consumed'], result
                    actions.append({'label': label, 'result': result})
                    write_json(output / 'actions.json', actions)
                rpc.call('control_pause')
                until(lambda: bridge.status().get('paused') and bridge.status().get('held') == [])
                checks['paused_and_released'] = True
                checks['fresh_observations'] = all(b['result']['observation_seq'] > a['result']['observation_seq']
                                                  for a, b in zip(actions, actions[1:]))
                checks['input_and_image_chain'] = len(actions) == 7
                checks['no_game_foreground'] = all(row['window'] != game_window for row in foreground)
                checks['visual_review_required'] = True
                assert all(checks.values()), checks
    except BaseException as error:
        failure = repr(error)
        raise
    finally:
        stop_sampling.set()
        sampler.join(2)
        write_json(output / 'foreground.json', foreground)
        (output / 'stop').touch()
        if host is not None:
            try:
                host.wait(5)
            except subprocess.TimeoutExpired:
                host.kill()
                host.wait(5)
            checks['host_exited'] = host.poll() is not None
        info = read(output / 'process.json') if (output / 'process.json').exists() else {}
        target_pid = target_pid or info.get('pid')
        checks['target_exited'] = bool(target_pid) and not alive(target_pid)
        source_after = manifest(source)
        external_after = manifest(Path(record['external_path']))
        write_json(output / 'source-after.json', source_after)
        write_json(output / 'external-after.json', external_after)
        checks['source_unchanged'] = source_after == json.loads((output / 'source-before.json').read_text(encoding='utf-8'))
        checks['external_unchanged'] = external_after == json.loads((output / 'external-before.json').read_text(encoding='utf-8'))['files']
        checks['outside_absent'] = not (output.parent / (output.name + '-outside.tmp')).exists()
        if checks['target_exited']:
            write_json(output / 'runtime-after.json', manifest(output / 'runtime'))
            def cleanup():
                try:
                    shutil.rmtree(output / 'runtime')
                    return True
                except PermissionError:
                    return False
            until(cleanup, 3)
        checks['runtime_removed'] = not (output / 'runtime').exists()
        write_json(output / 'result.json', {'checks': checks, 'error': failure, 'actions': actions,
                                           'physical_concurrency': 'not_tested', 'visual_review': 'pending'})
    assert all(checks.values()), checks
    print(json.dumps({'checks': checks, 'error': failure}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--binaries', type=Path, required=True)
    args = parser.parse_args()
    run(args.source.resolve(), args.output.resolve(), args.binaries.resolve())
