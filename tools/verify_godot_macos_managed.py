"""macOS 原生受管 Godot：实际 MCP、遮挡证据、焦点及创建者生命周期验收。"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import time

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gameqakit.sessions import read_json, write_json
from gameqakit.process_identity import _pid_path
from tests.test_godot_bridge import fixture_pack
from tools.verify_macos_product import Client, wait_value


class ManagedClient(Client):
    def raw(self, name, arguments):
        self.serial += 1
        request = {'jsonrpc': '2.0', 'id': self.serial, 'method': 'tools/call',
                   'params': {'name': name, 'arguments': arguments}}
        self.process.stdin.write(json.dumps(request) + '\n'); self.process.stdin.flush()
        assert self.selector.select(12), name
        row = json.loads(self.process.stdout.readline())
        self.calls.append({'request': request, 'response': row, 'at': time.time()})
        assert row['id'] == self.serial, row
        return row

    def call(self, name, **arguments):
        row = self.raw(name, arguments)
        assert 'error' not in row, row
        result = row['result']
        assert not result.get('isError'), result
        return json.loads(result['content'][0]['text'])

    def reject(self, name, **arguments):
        row = self.raw(name, arguments)
        assert 'error' in row, row
        return row['error']['message']


def pixels(path, expected_x):
    with Image.open(path) as raw:
        image = raw.convert('RGB')
        assert image.size == (320, 240), image.size
        assert image.getpixel((expected_x, 105)) == (255, 128, 32), (path, expected_x)
        assert image.getpixel((expected_x - 1, 105)) != (255, 128, 32), (path, expected_x)
    return {'path': str(path), 'sha256': hashlib.sha256(Path(path).read_bytes()).hexdigest(), 'x': expected_x}


def await_state(client, directory, expected):
    def read():
        row = client.call('game_status', directory=str(directory))
        if row['state'] in ('failed', 'cleanup_failed', 'stopped') and row['state'] != expected:
            raise RuntimeError(row)
        return row
    return wait_value(read, lambda r: r['state'] == expected and
                      (expected not in ('failed', 'cleanup_failed', 'stopped') or not r['worker_alive']), timeout=25)


def await_interrupted(client, directory, ready):
    def read():
        paths = [_pid_path(ready[k]) for k in ('pid', 'worker_pid', 'host_supervisor_pid', 'focus_monitor_pid')]
        return paths, client.call('game_status', directory=str(directory))

    # 内层目标已消失时，创建者持有的外层监督器仍可能正在完成回收。
    _, final = wait_value(read, lambda row: not any(row[0]) and row[1]['worker_alive'] is False, timeout=8)
    assert final['state'] == 'interrupted_unverified' and final['can_stop'] is False, final
    return final


def verify_interruptions(owner, observer, session, bridge):
    evidence = {}
    # 第二个实际 MCP 客户端可暂停正在执行的动作，但不能取得创建者的停止权。
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(owner.reject, 'control_action', action='move', direction='right',
                                  frames=120, command_id='pause-active', confirm=True, timeout=3)
        held = wait_value(lambda: read_json(session / 'fixture-state.json'),
                          lambda r: r['right_pressed'], timeout=3)
        observer.call('control_pause')
        error = pending.result(timeout=4)
        assert 'manual_pause' in error, error
    released = wait_value(lambda: read_json(session / 'fixture-state.json'),
                          lambda r: not r['right_pressed'] and not r['left_pressed'], timeout=3)
    receipt = owner.call('control_result', command_id='pause-active')
    state = owner.call('control_status')
    assert receipt['status'] == 'failed' and receipt['error'] == 'manual_pause', receipt
    assert state['paused'] is True and state['held'] == [], state
    evidence['pause_active'] = {'held': held, 'released': released, 'receipt': receipt}

    resumed = owner.call('control_resume', command_id='resume-for-expiry', confirm=True, timeout=3)
    state = wait_value(lambda: owner.call('control_status'), lambda r: r['frame'] >= resumed['frame'], timeout=3)
    # 模拟控制端不再续租：只提交现有协议请求，保留产品的原始 1.5 秒租约。
    request = {'protocol': 1, 'session_id': state['session_id'], 'id': 'lease-active',
               'payload': {'action': 'move', 'direction': 'left', 'frames': 120},
               'after_frame': state['frame'], 'pause_id': state['pause_id'], 'created_at': time.time()}
    write_json(bridge / 'requests/lease-active.json', request)
    write_json(bridge / 'request.json', request)
    lease = read_json(bridge / 'lease.json')
    held = wait_value(lambda: read_json(session / 'fixture-state.json'), lambda r: r['left_pressed'], timeout=3)
    state = wait_value(lambda: owner.call('control_status'), lambda r: r['paused'] is True, timeout=3)
    released = wait_value(lambda: read_json(session / 'fixture-state.json'),
                          lambda r: not r['right_pressed'] and not r['left_pressed'], timeout=3)
    receipt = owner.call('control_result', command_id='lease-active')
    assert state['held'] == [] and state['error'] == 'lease_expired', state
    assert receipt['status'] == 'failed' and receipt['error'] == 'lease_expired', receipt
    assert read_json(bridge / 'lease.json') == lease
    evidence['lease_active'] = {'lease': lease, 'held': held, 'released': released, 'receipt': receipt}

    owner.call('control_resume', command_id='resume-for-unbind', confirm=True, timeout=3)
    unbound = owner.call('game_unbind')
    assert unbound['bound'] is False and unbound['release_confirmed'] is True, unbound
    assert '无活动目标' in owner.reject('control_resume', command_id='after-unbind', confirm=True)
    assert owner.call('game_status')['bound'] is False
    rebound = owner.call('game_bind', directory=str(bridge), session_id=state['session_id'], confirm=True)
    assert rebound['can_control'] is True and rebound['paused'] is True and rebound['held'] == [], rebound
    evidence['unbind'] = {'unbound': unbound, 'rebound': rebound}
    return evidence


def run_case(root, components, probe, executable=None, ending='stop'):
    root.mkdir(parents=True, exist_ok=False)
    source = root / 'sample'; source.mkdir(); fixture_pack(source / 'fixture.pck', no_focus=True)
    session = root / 'session'
    profile = {'name': 'mac-managed', 'proc': 'godot-runtime', 'data_dir': str(root / 'qa')}
    write_json(root / 'profile.json', profile)
    env = {**os.environ, 'GAMEQAKIT_PROFILE': str(root / 'profile.json'),
           'GAMEQAKIT_PROFILES': str(root / 'profiles'), 'GAMEQA_MACOS_MANAGED_VALIDATION': '1'}
    command = [str(executable), '--mcp'] if executable else [sys.executable, '-B', '-m', 'gameqakit.mcp_server']
    result = {'ok': False, 'ending': ending, 'mode': 'frozen' if executable else 'source', 'actions': [], 'proofs': []}
    owner = other = cover = None
    try:
        owner = ManagedClient(command, env, root)
        tools = {t['name'] for t in owner.rpc('tools/list', {})['tools']}
        assert {'game_inspect', 'game_prepare', 'game_start', 'control_action'} <= tools
        assert not {'click', 'key', 'background_start'}.intersection(tools)
        inspected = owner.call('game_inspect', path=str(source), artifacts=str(components), destination=str(session))
        assert inspected['launch_readiness'] == 'ready', inspected
        plan = inspected['plan']
        owner.call('game_prepare', plan=plan, request_id='prepare', confirm=True)
        await_state(owner, session, 'prepared')
        duplicate = owner.call('game_prepare', plan=plan, request_id='prepare', confirm=True)
        assert duplicate['state'] == 'prepared', duplicate
        if ending == 'cancel':
            owner.call('game_stop', directory=str(session), request_id='cancel-before-start', confirm=True)
            final = await_state(owner, session, 'stopped')
            assert not (session / 'bridge/connection.json').exists() and 'pid' not in final, final
            result.update(final=final, ok=True)
            return result
        if ending == 'tamper':
            with (session / 'runtime/fixture.pck').open('ab') as stream: stream.write(b'!')
        owner.call('game_start', directory=str(session), fingerprint=plan['fingerprint'], request_id='start', confirm=True)
        if ending == 'tamper':
            final = await_state(owner, session, 'failed')
            assert not (session / 'bridge/connection.json').exists() and 'pid' not in final, final
            result.update(final=final, ok=True)
            return result
        ready = await_state(owner, session, 'ready_paused'); result['ready'] = ready
        assert ready['paused'] is True and ready['held'] == [], ready
        assert not (session / 'bridge/requests').exists()
        assert read_json(session / 'fixture-state.json')['x'] == 60
        result['startup'] = pixels(ready['screenshot'], 60)
        bridge = Path(ready['bridge_directory'])
        owner.call('game_bind', directory=str(bridge), session_id=ready['session_id'], confirm=True)
        sibling = root / 'other'; sibling.mkdir()
        other = ManagedClient(command, env, sibling)
        external = other.call('game_bind', directory=str(bridge), session_id=ready['session_id'], confirm=True)
        assert external['can_control'] is False and external['can_stop'] is False, external
        assert '创建者' in other.reject('game_stop', directory=str(session), request_id='not-owner', confirm=True)
        assert '控制权' in other.reject('control_resume', command_id='not-controller', confirm=True)
        cover = subprocess.Popen([str(probe), 'cover', str(root), str(ready['pid'])],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        wait_value(lambda: read_json(root / 'cover-ready.json'), lambda r: 'window' in r)
        for phase in ('before-actions', 'after-actions'):
            subprocess.run([str(probe), 'proof', str(root), str(ready['pid'])], check=True, timeout=10)
            proof = read_json(root / 'proof.json'); assert proof['ok'], proof
            result['proofs'].append(proof)
            for name in ('proof.json', 'desktop-covered.png', 'target-covered.png'):
                (root / name).rename(root / (phase + '-' + name))
            if phase == 'after-actions': break
            prior = owner.call('control_status')['observation_seq']
            wait_value(lambda: owner.call('control_status'), lambda r: r['observation_seq'] > prior, timeout=3)
            resumed = owner.call('control_resume', command_id='resume', confirm=True, timeout=3)
            result['actions'].append(resumed)
            wait_value(lambda: owner.call('control_status'), lambda r: r['frame'] >= resumed['frame'], timeout=3)
            for identifier, kind, expected_x, options in (
                    ('right', 'move', 100, {'direction': 'right'}),
                    ('left', 'move', 60, {'direction': 'left'}), ('wait', 'wait', 60, {})):
                start = time.monotonic()
                receipt = owner.call('control_action', action=kind, frames=10, command_id=identifier,
                                     confirm=True, timeout=3, **options)
                assert receipt['consumed'] is True and receipt['ok'] is True, receipt
                actual = read_json(session / 'fixture-state.json')
                assert actual['x'] == expected_x and not actual['right_pressed'] and not actual['left_pressed'], actual
                result['actions'].append({'receipt': receipt, 'image': pixels(receipt['screenshot'], expected_x),
                                          'fixture': actual, 'elapsed_ms': (time.monotonic()-start)*1000})
                again = owner.call('control_action', action=kind, frames=10, command_id=identifier,
                                   confirm=True, timeout=3, **options)
                assert again['screenshot_sha256'] == receipt['screenshot_sha256']
                assert read_json(session / 'fixture-state.json')['x'] == expected_x
            if ending == 'stop':
                result['interruptions'] = verify_interruptions(owner, other, session, bridge)
        if ending == 'worker-kill':
            os.kill(ready['worker_pid'], signal.SIGKILL)
            final = await_interrupted(owner, session, ready)
        elif ending == 'target-exit':
            os.kill(ready['pid'], signal.SIGKILL)
            final = await_state(owner, session, 'cleanup_failed')
            assert final['release_confirmed'] is False and final['target_exited'] is True, final
        elif ending == 'focus-loss':
            # 终止自有监视器，验证产品链停止输入并回收；不改变系统前台。
            os.kill(ready['focus_monitor_pid'], signal.SIGKILL)
            final = await_state(owner, session, 'cleanup_failed')
            assert 'focus_guard_failed' in str(final), final
        elif ending in ('creator-eof', 'creator-kill'):
            if ending == 'creator-kill': owner.process.kill()
            else: owner.process.stdin.close()
            owner.process.wait(timeout=10)
            wait_value(lambda: [_pid_path(ready[k]) for k in ('pid', 'worker_pid', 'host_supervisor_pid', 'focus_monitor_pid')],
                       lambda rows: not any(rows), timeout=8)
            final = read_json(session / 'session.json')
            result['interrupted_status'] = other.call('game_status', directory=str(session))
            assert result['interrupted_status']['can_stop'] is False
        else:
            owner.call('control_pause')
            wait_value(lambda: owner.call('control_status'), lambda r: r['paused'] is True and r['held'] == [], timeout=3)
            owner.call('game_stop', directory=str(session), request_id='stop', confirm=True)
            final = await_state(owner, session, 'stopped')
            assert final['release_confirmed'] and final['source_unchanged'] and final['runtime_source_unchanged'], final
            focus = read_json(bridge / 'focus-final.json')
            assert focus['stopped'] and not focus['target_activated'] and not focus['frontmost_changed'], focus
            result['focus_final'] = focus
        result['final'] = final
        wait_value(lambda: [_pid_path(ready[k]) for k in ('pid', 'worker_pid', 'host_supervisor_pid', 'focus_monitor_pid')],
                   lambda rows: not any(rows), timeout=8)
        result['reaped'] = True
        assert hashlib.sha256((source / 'fixture.pck').read_bytes()).hexdigest() == plan['source_digests']['fixture.pck']
        result['ok'] = True
        return result
    finally:
        if cover:
            (root / 'cover-stop').touch()
            try: cover.wait(timeout=3)
            except subprocess.TimeoutExpired: cover.kill(); cover.wait(timeout=3)
        for label, client in (('observer', other), ('creator', owner)):
            if client:
                if client.process.stdin.closed:
                    client.selector.close(); client.process.stdout.close(); client.log.close()
                else: client.close()
                write_json(root / (label + '-mcp.json'), client.calls)
        write_json(root / 'result.json', result)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--components', type=Path, required=True)
    parser.add_argument('--probe', type=Path, required=True)
    parser.add_argument('--executable', type=Path)
    args = parser.parse_args()
    assert sys.platform == 'darwin' and platform.machine() == 'arm64'
    args.out.mkdir(parents=True, exist_ok=False)
    result = {'ok': False, 'cases': []}
    try:
        endings = (('stop', 'creator-kill') if args.executable else
                   ('stop', 'cancel', 'tamper', 'focus-loss', 'target-exit', 'worker-kill', 'creator-eof', 'creator-kill'))
        for ending in endings:
            result['cases'].append(run_case(args.out / ending, args.components.resolve(), args.probe.resolve(),
                                            args.executable.resolve() if args.executable else None, ending))
        result['ok'] = True
    finally:
        write_json(args.out / 'result.json', result)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
