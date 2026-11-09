"""在已有隔离会话中验证协议失败路径及真实 MCP stdio 入口。"""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

from gameqakit.sessions import write_json


def safety_checks(client, process, wait):
    evidence = {}
    directory = client.directory
    meta = client._connection()

    def state(**expected):
        value = client.status()
        return value if value.get('connected') and all(value.get(k) == v for k, v in expected.items()) else None

    def released():
        return state(paused=True, held=[], native_buttons=0, device_buttons=0, applied=0)

    def frozen():
        before = wait(released, 3, process)
        time.sleep(0.4)
        after = client.status()
        assert after['connected'] and after['frame'] == before['frame'], (before, after)
        return {'before': before, 'after': after}

    first = client.action('wait', frames=1, command_id='replay')
    repeated = client.action('wait', frames=1, command_id='replay')
    assert first['frame'] == repeated['frame'] and first['screenshot_sha256'] == repeated['screenshot_sha256']
    assert client.status()['frame'] == first['frame']
    evidence['replay'] = {'first': first, 'repeated': repeated}

    before = client.status()
    client._wire_write('pause', {'session_id': 'previous-session', 'id': 'late-pause'}, meta)
    client._wire_write('request', {'protocol': 1, 'session_id': 'previous-session', 'id': 'late-command',
                                 'pause_id': '', 'after_frame': before['frame'], 'created_at': time.time(),
                                 'payload': {'action': 'move', 'direction': 'down', 'frames': 120}}, meta)
    time.sleep(0.25)
    after = client.status()
    assert after['connected'] and not after['paused'] and after['frame'] == before['frame']
    assert after['native_buttons'] == 0 and after['device_buttons'] == 0
    assert not (directory / 'results/late-command.json').exists()
    evidence['foreign_session'] = {'before': before, 'after': after}
    client.action('wait', frames=1, command_id='after-foreign')

    interrupted = {}
    def hold():
        try:
            interrupted['unexpected_success'] = client.action('move', direction='down', frames=120,
                                                            command_id='interrupt', timeout=5)
        except RuntimeError as exc:
            interrupted['error'] = str(exc)
    thread = threading.Thread(target=hold, daemon=True)
    thread.start()
    evidence['held_before_pause'] = wait(lambda: state(held=['down'], native_buttons=64, device_buttons=4096),
                                         3, process)
    client.pause()
    thread.join(timeout=5)
    assert not thread.is_alive() and interrupted.get('error') == 'manual_pause', interrupted
    evidence['interrupted'] = interrupted
    evidence['manual_pause'] = frozen()

    client.resume(command_id='resume-after-pause')
    current = client.status()
    expiry = time.time() + 0.25
    client._wire_write('lease', {'session_id': meta['session_id'], 'expires_at': expiry}, meta)
    client._wire_write('request', {'protocol': 1, 'session_id': meta['session_id'], 'id': 'expired-hold',
                                 'pause_id': client._pause_id(meta), 'after_frame': current['frame'],
                                 'created_at': time.time(),
                                 'payload': {'action': 'move', 'direction': 'down', 'frames': 120}}, meta)
    evidence['held_before_expiry'] = wait(lambda: state(held=['down'], native_buttons=64, device_buttons=4096),
                                          3, process)
    evidence['lease_expired'] = wait(lambda: state(paused=True, error='lease_expired', held=[],
                                                  native_buttons=0, device_buttons=0, applied=0), 3, process)
    evidence['expiry_frozen'] = frozen()
    assert evidence['lease_expired']['updated_at'] >= expiry

    client.resume(command_id='resume-after-expiry')
    current = client.status()
    client._wire_write('lease', {'session_id': meta['session_id'], 'expires_at': time.time() + 1.5}, meta)
    client._wire_write('request', {'protocol': 1, 'session_id': meta['session_id'], 'id': 'old-generation',
                                 'pause_id': 'previous-generation', 'after_frame': current['frame'],
                                 'created_at': time.time(), 'payload': {'action': 'resume', 'frames': 1}}, meta)
    evidence['old_generation'] = wait(lambda: state(paused=True, error='stale_command', held=[],
                                                    native_buttons=0, device_buttons=0), 3, process)
    assert evidence['old_generation']['frame'] == current['frame']

    client.resume(command_id='resume-before-capture-failure')
    blocker = directory / 'screenshots/capture-failure.png'
    blocker.mkdir()
    try:
        try:
            client.action('wait', frames=1, command_id='capture-failure')
        except RuntimeError as exc:
            assert str(exc) == 'capture_save_failed', str(exc)
            evidence['capture_error'] = str(exc)
        else:
            raise AssertionError('Failed screenshot reported success')
        evidence['capture_failure'] = frozen()
        try:
            client.action('move', direction='down', frames=1, command_id='must-not-run')
        except RuntimeError:
            assert not (directory / 'requests/must-not-run.json').exists()
        else:
            raise AssertionError('Action accepted after capture failure')
    finally:
        blocker.rmdir()
        blocker.with_suffix('.png.tmp').unlink(missing_ok=True)
    client.resume(command_id='resume-after-safety')
    return evidence


def mcp_checks(client, runtime, out, root):
    profile = out / 'mcp-profile.json'
    write_json(profile, {'name': 'duckstation-isolated-test', 'proc': Path(runtime).stem,
                         'exe_path': str(runtime), 'data_dir': str(out / 'mcp-data')})
    calls = [('initialize', {}), ('tools/list', {})]
    for name, arguments in (
        ('engine_inspect', {'path': str(runtime)}),
        ('control_status', {}),
        ('control_action', {'action': 'wait', 'frames': 1, 'command_id': 'mcp-wait', 'confirm': True}),
        ('control_result', {'command_id': 'mcp-wait', 'include_image': True}),
        ('control_pause', {}),
    ):
        calls.append(('tools/call', {'name': name, 'arguments': {'directory': str(client.directory), **arguments}}))
    wire = ''.join(json.dumps({'jsonrpc': '2.0', 'id': i + 1, 'method': method, 'params': params}) + '\n'
                   for i, (method, params) in enumerate(calls))
    child = subprocess.run([sys.executable, str(root / 'gameqakit/mcp_server.py')],
                           input=wire, text=True, encoding='utf-8', capture_output=True, timeout=20,
                           env={**os.environ, 'GAMEQAKIT_PROFILE': str(profile)})
    (out / 'mcp-stderr.log').write_text(child.stderr, encoding='utf-8')
    assert child.returncode == 0, child.stderr
    responses = [json.loads(line) for line in child.stdout.splitlines()]
    assert len(responses) == len(calls), responses
    for response in responses:
        assert 'error' not in response, response
        for item in response['result'].get('content', []):
            if item['type'] == 'image':
                data = base64.b64decode(item.pop('data'), validate=True)
                item['bytes'] = len(data)
                item['sha256'] = hashlib.sha256(data).hexdigest()
    write_json(out / 'mcp-responses.json', responses)
    engine = json.loads(responses[2]['result']['content'][0]['text'])
    assert engine['adapter'] == 'duckstation' and not engine['control_verified']
    status = json.loads(responses[3]['result']['content'][0]['text'])
    assert status['connected'] and status['pid'] == client.pid
    action = json.loads(responses[4]['result']['content'][0]['text'])
    assert action['status'] == 'done' and action['consumed']
    result = json.loads(responses[5]['result']['content'][0]['text'])
    assert result['status'] == 'done' and result['id'] == action['id']
    assert result['screenshot_sha256'] == action['screenshot_sha256']
    path = Path(result['screenshot'])
    data = path.read_bytes()
    assert hashlib.sha256(data).hexdigest() == action['screenshot_sha256']
    images = [item for item in responses[5]['result']['content'] if item['type'] == 'image']
    if len(data) <= 1024 * 1024:
        assert len(images) == 1 and images[0]['sha256'] == action['screenshot_sha256']
        image = {**images[0], 'delivery': 'inline'}
    else:
        assert not images and result.get('image_omitted')
        image = {'delivery': 'path', 'bytes': len(data), 'sha256': result['screenshot_sha256'],
                 'reason': result['image_omitted']}
    return {'calls': len(calls), 'entry': str(root / 'gameqakit/mcp_server.py'),
            'action': action, 'image': image, 'process_reaped': True}
