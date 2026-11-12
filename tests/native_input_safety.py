"""正式原生宿主的自有窗口失败路径；只创建并回收本批进程和副本。"""
import argparse
from contextlib import contextmanager
import ctypes as C
from ctypes import wintypes as W
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
from gameqakit.sessions import write_json
from tests.native_input_smoke import read, until

k = C.WinDLL('kernel32', use_last_error=True)
k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
k.OpenProcess.restype = W.HANDLE
k.GetExitCodeProcess.argtypes = [W.HANDLE, C.POINTER(W.DWORD)]
k.CloseHandle.argtypes = [W.HANDLE]


def alive(pid):
    handle = k.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        code = W.DWORD()
        return bool(k.GetExitCodeProcess(handle, C.byref(code))) and code.value == 259
    finally:
        k.CloseHandle(handle)


@contextmanager
def owned(output, binaries, *, flags=()):
    output.mkdir(parents=True, exist_ok=False)
    for name in ('runtime', 'bridge', 'temp'):
        (output / name).mkdir()
    for name in flags:
        (output / name).touch()
    exe = output / 'runtime' / 'Target.exe'
    shutil.copy2(binaries / 'target.exe', exe)
    dll = binaries / 'native-input.dll'
    checks = {}
    failure = None
    with (output / 'host.log').open('w', encoding='utf-8') as log:
        host = subprocess.Popen([str(binaries / 'owned-host.exe'), str(output), str(dll),
                                 hashlib.sha256(dll.read_bytes()).hexdigest()], stdout=log, stderr=log)
        try:
            yield host, exe, checks
        except BaseException as error:
            failure = repr(error)
            raise
        finally:
            (output / 'stop').touch()
            try:
                host.wait(5)
            except subprocess.TimeoutExpired:
                host.kill()
                host.wait(5)
            result = read(output / 'host-result.json') if (output / 'host-result.json').exists() else {}
            connection = read(output / 'bridge/connection.json') if (output / 'bridge/connection.json').exists() else {}
            process_info = read(output / 'process.json') if (output / 'process.json').exists() else {}
            target_pid = result.get('pid', connection.get('pid', process_info.get('pid')))
            checks['host_exited'] = host.poll() is not None
            checks['target_exited'] = bool(target_pid) and not alive(target_pid)
            checks['outside_absent'] = not (output.parent / (output.name + '-outside.tmp')).exists()
            write_json(output / 'runtime-after.json', {'Target.exe': hashlib.sha256(exe.read_bytes()).hexdigest()})
            if checks['target_exited']:
                def cleanup():
                    try:
                        shutil.rmtree(output / 'runtime')
                        return True
                    except PermissionError:
                        return False
                until(cleanup, 3)
            checks['runtime_removed'] = not (output / 'runtime').exists()
            write_json(output / 'result.json', {'checks': checks, 'error': failure})
    assert all(checks.values()), checks


def connect(output, host, exe):
    bridge = output / 'bridge'
    def ready():
        if (bridge / 'fatal.json').exists():
            raise AssertionError(read(bridge / 'fatal.json'))
        assert host.poll() is None, 'host exited during startup'
        return read(bridge / 'connection.json')
    meta = until(ready)
    client = BridgeClient(bridge, pid=meta['pid'], exe_path=str(exe))
    until(lambda: client.status().get('observation_seq', 0) > 0)
    return client, meta


def begin_unrenewed(client, meta):
    client.resume(command_id='resume-1')
    state = client.status()
    pause_id = client._pause_id(meta)
    now = time.time()
    client._wire_write('lease', {'session_id': meta['session_id'], 'expires_at': now + 1.5}, meta)
    client._wire_write('request', {'protocol': 2, 'session_id': meta['session_id'], 'id': 'held-1',
                                  'payload': {'action': 'move', 'direction': 'right', 'duration_ms': 1000},
                                  'created_at': now, 'pause_id': pause_id,
                                  'after_observation_seq': state['observation_seq']}, meta)
    until(lambda: client.status().get('held') == ['right'], 1)


def release_unconfirmed(output, binaries):
    case = output / 'release-unconfirmed'
    with owned(case, binaries) as (host, exe, checks):
        client, meta = connect(case, host, exe)
        begin_unrenewed(client, meta)
        until(lambda: read(case / 'target-state.json')['down'], 1)
        (case / 'stop-poll').touch()
        client.pause()
        until(lambda: host.poll() is not None, 4)
        checks['unconfirmed_release_terminates_job'] = not alive(meta['pid'])
    return read(case / 'result.json')


def fault_cases(output, binaries):
    summary = {}
    case = output / 'startup-black'
    with owned(case, binaries, flags=('black-frame',)) as (host, exe, checks):
        started = time.monotonic()
        # The bridge has a 15-second startup budget; observe host cleanup afterwards.
        until(lambda: host.poll() is not None, 20)
        elapsed = time.monotonic() - started
        fatal = read(case / 'bridge/fatal.json') if (case / 'bridge/fatal.json').exists() else {}
        host_result = read(case / 'host-result.json')
        rejected = (fatal.get('error') == 'startup_invalid_image' or
                    host_result['reason'] == 'startup_timeout')
        checks['black_rejected_before_ready'] = rejected and not (case / 'bridge/connection.json').exists()
        checks['startup_job_reaped_in_bound'] = elapsed < 20 and not host_result['connected']
        write_json(case / 'startup-timing.json', {'elapsed_seconds': elapsed, 'host_reason': host_result['reason'],
                                                'bridge_error': fatal.get('error')})
        checks['no_input'] = not (case / 'target-state.json').exists() or read(case / 'target-state.json')['presses'] == 0
    summary['startup-black'] = read(case / 'result.json')
    for fault in ('black-frame', 'stall-window', 'evidence-write'):
        case = output / fault
        with owned(case, binaries) as (host, exe, checks):
            client, meta = connect(case, host, exe)
            begin_unrenewed(client, meta)
            until(lambda: read(case / 'target-state.json')['down'], 1)
            if fault == 'evidence-write':
                until(lambda: not (case / 'bridge/status.json.tmp').exists(), 1)
                (case / 'bridge/status.json.tmp').mkdir()
            else:
                (case / fault).touch()
            until(lambda: host.poll() is not None, 5)
            checks['fault_terminates_job'] = not alive(meta['pid'])
            if fault == 'stall-window':
                checks['heartbeat_timeout'] = read(case / 'host-result.json')['reason'] == 'bridge_heartbeat_timeout'
            else:
                expected = 'invalid_capture_image' if fault == 'black-frame' else 'evidence_write_failed'
                checks['expected_failure'] = read(case / 'bridge/fatal.json')['error'] == expected
        summary[fault] = read(case / 'result.json')
    return summary


def run(output, binaries):
    output.mkdir(parents=True, exist_ok=False)
    summary = {}
    case = output / 'pause-held'
    with owned(case, binaries) as (host, exe, checks):
        client, meta = connect(case, host, exe)
        begin_unrenewed(client, meta)
        client.pause()
        until(lambda: read(case / 'target-state.json')['releases'] > 0, 2)
        until(lambda: client.status().get('paused') is True and client.status().get('held') == [], 1)
        state = client.status()
        checks['pause_releases_consumed_key'] = state['paused'] and state['held'] == [] and not read(case / 'target-state.json')['down']
    summary['pause-held'] = read(case / 'result.json')
    case = output / 'lease-expiry'
    with owned(case, binaries) as (host, exe, checks):
        client, meta = connect(case, host, exe)
        begin_unrenewed(client, meta)
        until(lambda: client.status().get('error') == 'lease_expired', 3)
        state = client.status()
        checks['lease_expires_and_releases'] = state['paused'] and state['held'] == [] and not read(case / 'target-state.json')['down']
    summary['lease-expiry'] = read(case / 'result.json')
    case = output / 'host-killed'
    with owned(case, binaries) as (host, exe, checks):
        client, meta = connect(case, host, exe)
        begin_unrenewed(client, meta)
        host.kill()
        host.wait(5)
        until(lambda: not alive(meta['pid']), 3)
        checks['job_kills_target_after_host_crash'] = not alive(meta['pid'])
    summary['host-killed'] = read(case / 'result.json')
    summary['release-unconfirmed'] = release_unconfirmed(output, binaries)
    summary.update(fault_cases(output, binaries))
    write_json(output / 'result.json', summary)
    print(json.dumps(summary))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--binaries', required=True, type=Path)
    args = parser.parse_args()
    run(args.output.resolve(), args.binaries.resolve())
