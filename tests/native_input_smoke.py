"""在自有 x86 窗口运行正式宿主与 DLL，检查守卫、消费、释放和后台画面。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gameqakit.control import BridgeClient
from gameqakit.sessions import read_json


def read(path):
    return read_json(path)


def until(predicate, seconds=15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            value = predicate()
            if value:
                return value
        except (OSError, ValueError):
            pass
        time.sleep(.02)
    raise AssertionError('bounded wait expired')


def run(output, binaries):
    output.mkdir(parents=True, exist_ok=False)
    (output / 'runtime').mkdir()
    (output / 'bridge').mkdir()
    (output / 'temp').mkdir()
    (output / 'delay-first-paint').touch()
    exe = output / 'runtime' / 'Target.exe'
    shutil.copy2(binaries / 'target.exe', exe)
    bridge = output / 'bridge'
    outside = output.parent / (output.name + '-outside.tmp')
    assert not outside.exists()
    dll = binaries / 'native-input.dll'
    command = [str(binaries / 'owned-host.exe'), str(output), str(dll), hashlib.sha256(dll.read_bytes()).hexdigest()]
    checks = {}
    with (output / 'host.log').open('w', encoding='utf-8') as log:
        host = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        try:
            def connection():
                if (bridge / 'fatal.json').exists():
                    raise AssertionError(read(bridge / 'fatal.json'))
                if host.poll() is not None:
                    raise AssertionError(f'host exited before connection: {host.returncode}')
                return read(bridge / 'connection.json')
            meta = until(connection)
            client = BridgeClient(bridge, pid=meta['pid'], exe_path=str(exe))
            until(lambda: client.status().get('observation_seq', 0) > 0)
            state = client.status()
            checks['startup_paused'] = state['connected'] and state['paused'] and state['held'] == []
            checks['guard'] = read(output / 'target-guard.json') == {'file_denied': True, 'registry_denied': True, 'crypto_ok': True}
            client.resume(command_id='resume-1')
            before = read(output / 'target-state.json')
            action = client.action('move', direction='right', duration_ms=120, command_id='right-1')
            after = read(output / 'target-state.json')
            checks['consumed_and_released'] = action['press_consumed'] and action['release_consumed'] and after['presses'] > before['presses'] and after['releases'] > before['releases'] and not after['down']
            checks['visible_movement'] = after['position'] > before['position'] and Path(action['screenshot']).is_file()
            checks['screenshot_digest'] = hashlib.sha256(Path(action['screenshot']).read_bytes()).hexdigest() == action['screenshot_sha256']
            client.pause()
            until(lambda: client.status().get('paused') is True and client.status().get('held') == [])
            checks['pause'] = True
            checks['outside_unchanged'] = not outside.exists()
            assert all(checks.values()), checks
        finally:
            (output / 'stop').touch()
            try:
                host.wait(5)
            except subprocess.TimeoutExpired:
                host.kill()
                host.wait(5)
        checks['host_exit'] = host.returncode == 0
    (output / 'result.json').write_text(json.dumps({'checks': checks}, ensure_ascii=False, indent=2), encoding='utf-8')
    assert all(checks.values()), checks
    print(json.dumps(checks))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--binaries', type=Path, required=True)
    options = parser.parse_args()
    run(options.output.resolve(), options.binaries.resolve())
