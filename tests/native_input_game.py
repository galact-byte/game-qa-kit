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


def assess_concurrency(actions, samples, witness, lifecycle, *, smoke=False):
    keyed = [row for row in actions if row.get('action') in ('move', 'confirm', 'cancel')]
    overlap, settings = set(), []
    for row in actions:
        result = row['result']
        start, end = result.get('started_monotonic_ms', 0), result.get('completed_monotonic_ms', 0)
        button = row.get('direction', 'down') if row.get('action') == 'move' else {'confirm': 'ok', 'cancel': 'cancel'}.get(row.get('action'))
        for sample in samples:
            if sample['label'] != row['label'] or not (start <= sample['ticks'] <= end):
                continue
            if not (sample['left'] and sample['foreground']):
                continue
            if button and start <= sample['ticks'] < start + row['duration_ms'] and sample['held'] == [button]:
                overlap.add(row['label'])
            if row['label'].startswith('04-settings') and row.get('action') == 'wait':
                settings.append(sample['ticks'])
    exposure = max(settings) - min(settings) if settings else 0
    checks = {
        'lifecycle_and_evidence': bool(lifecycle) and all(lifecycle.values()),
        'normal_input_and_release': len(keyed) == 4 and all(
            row['result'].get('status') == 'done' and row['result'].get('held') == [] for row in actions) and all(
            row['result'].get('press_consumed') and row['result'].get('release_consumed') for row in keyed),
        'target_never_foreground': bool(samples) and not any(row['target_foreground'] for row in samples),
        'no_software_keys_in_witness': witness['unexpected_keys'] == 0 and witness['right_presses'] == 0,
        'concurrent_keyed_actions': len(overlap) >= 3,
        'settings_left_exposure': exposure >= 5000,
        'mouse_participation': witness['motion'] >= 10 and witness['clicks'] >= 2,
        'keyboard_participation': witness['left_presses'] >= 1,
    }
    participation = {'concurrent_keyed_actions', 'settings_left_exposure', 'mouse_participation', 'keyboard_participation'}
    if not all(value for name, value in checks.items() if name not in participation):
        status = 'failed'
    elif smoke:
        status = 'smoke_passed_no_physical_claim'
    elif not all(checks.values()):
        status = 'incomplete_participation'
    else:
        status = 'checks_passed_pending_participant_confirmation_and_visual_review'
    return {'status': status, 'physical_verified': False, 'concurrency_checks': checks,
            'overlap_actions': len(overlap), 'settings_overlap_ms': exposure,
            'limitation': 'Requires participant confirmation and screenshot review; window events do not authenticate hardware provenance.'}


def run(source, output, binaries, *, witness=None, smoke=False):
    record = prepare(source, output)
    dll = binaries / 'native-input.dll'
    exe = output / 'runtime/game.exe'
    checks, actions, foreground = {}, [], []
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.GetForegroundWindow.restype = wintypes.HWND
    user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user.GetAncestor.restype = wintypes.HWND
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetTickCount64.restype = ctypes.c_ulonglong
    witness_handle = user.GetAncestor(witness.handle, 2) if witness else None
    samples, sampling_errors = [], []
    active_label = ''
    participating = threading.Event()
    stop_sampling = threading.Event()

    def sample():
        try:
            while not stop_sampling.is_set():
                current = int(user.GetForegroundWindow() or 0)
                foreground.append({'at': time.time(), 'window': current})
                if participating.is_set():
                    state = witness.snapshot()
                    ticks = kernel.GetTickCount64()
                    status = read(output / 'bridge/status.json')
                    samples.append({'label': active_label, 'ticks': ticks, 'held': status.get('held', []),
                                    'left': state['left'], 'foreground': current == witness_handle,
                                    'target_foreground': current == game_window})
                stop_sampling.wait(.02)
        except BaseException as error:
            sampling_errors.append(repr(error))

    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    failure = None
    host = None
    target_pid = None
    baseline_witness = {}
    outcome = None
    try:
        with (output / 'host.log').open('w', encoding='utf-8') as log:
            host = subprocess.Popen([str(binaries / 'native-host.exe'), str(output), str(dll),
                                     hashlib.sha256(dll.read_bytes()).hexdigest()], stdout=log, stderr=log)
            bridge, meta = connect(output, host, exe)
            target_pid = meta['pid']
            game_window = bridge.status()['window_id']
            if witness:
                witness.report_progress('waiting_for_participant')
                write_json(output / 'progress.json', {'status': 'waiting_for_participant'})
                if not smoke:
                    deadline = time.monotonic() + 120
                    while not witness.changed.wait(.1):
                        if witness.cancelled.is_set():
                            raise InterruptedError('participant_cancelled')
                        if host.poll() is not None or sampling_errors:
                            raise RuntimeError('host_or_sampler_exited_while_waiting')
                        if time.monotonic() >= deadline:
                            raise TimeoutError('participant_timeout')
                if witness.cancelled.is_set():
                    raise InterruptedError('participant_cancelled')
                baseline_witness = getattr(witness, 'baseline', None) or witness.snapshot()
                witness.report_progress('running')
                participating.set()
            with mcp(output, exe) as rpc:
                resumed = rpc.call('control_resume', confirm=True, command_id='menu-resume', include_image=True)
                actions.append({'label': '00-title', 'result': resumed})
                steps = [('01-down', 'move', 120, 'down'), ('02-down', 'move', 120, 'down'),
                         ('03-settings', 'confirm', 120, None), ('04-settings-ready', 'wait', 700, None),
                         ('05-return', 'cancel', 120, None), ('06-title-ready', 'wait', 700, None)]
                if witness:
                    steps[4:4] = [(f'04-settings-hold-{i:02d}', 'wait', 700, None) for i in range(10)]
                for index, (label, action, duration, direction) in enumerate(steps):
                    if sampling_errors:
                        raise RuntimeError(f'sampling_failed: {sampling_errors}')
                    if witness and witness.cancelled.is_set():
                        raise InterruptedError('participant_cancelled')
                    active_label = label
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
                    actions.append({'label': label, 'action': action, 'duration_ms': duration,
                                    'direction': direction, 'result': result})
                    write_json(output / 'actions.json', actions)
                    if witness:
                        witness.report_progress('running', rounds=index + 1)
                        write_json(output / 'progress.json', {'status': 'running', 'actions': index + 1, 'total': len(steps)})
                rpc.call('control_pause')
                until(lambda: bridge.status().get('paused') and bridge.status().get('held') == [])
                checks['paused_and_released'] = True
                checks['fresh_observations'] = all(b['result']['observation_seq'] > a['result']['observation_seq']
                                                  for a, b in zip(actions, actions[1:]))
                checks['input_and_image_chain'] = len(actions) == (17 if witness else 7)
                checks['sampler_completed'] = not sampling_errors
                checks['no_game_foreground'] = all(row['window'] != game_window for row in foreground)
                checks['visual_review_required'] = True
                assert all(checks.values()), checks
    except BaseException as error:
        failure = repr(error)
        if witness is None:
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
        checks['sampler_completed'] = not sampler.is_alive() and not sampling_errors
        outcome = {'checks': checks, 'error': failure, 'actions': actions,
                   'physical_concurrency': 'not_tested', 'visual_review': 'pending'}
        if witness:
            state = witness.snapshot()
            for name in ('motion', 'clicks'):
                state[name] -= baseline_witness.get(name, state[name])
            outcome.update(assess_concurrency(actions, samples, state, checks, smoke=smoke))
            if failure:
                outcome['status'] = ('cancelled' if failure == "InterruptedError('participant_cancelled')" else
                                     'incomplete_no_participant' if failure == "TimeoutError('participant_timeout')" else 'failed')
            outcome.update(witness=state, physical_concurrency=outcome['status'], sampling_errors=sampling_errors)
            write_json(output / 'samples.json', samples)
            artifacts = [Path(__file__), ROOT / 'tests/native_input_game_concurrent.py',
                         ROOT / 'tests/native_input_concurrent.py', ROOT / 'tests/native_input_mcp.py',
                         binaries / 'native-host.exe', dll]
            outcome['artifacts'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in artifacts}
        write_json(output / 'result.json', outcome)
    if witness is None:
        assert all(checks.values()), checks
    print(json.dumps({'checks': checks, 'error': failure, 'status': outcome.get('status')}, ensure_ascii=False))
    return outcome


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--binaries', type=Path, required=True)
    args = parser.parse_args()
    run(args.source.resolve(), args.output.resolve(), args.binaries.resolve())
