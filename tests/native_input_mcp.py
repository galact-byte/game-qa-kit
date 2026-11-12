"""真实 stdio MCP 与自有 x86 窗口集成；包括控制端突然退出。"""
import argparse
import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gameqakit.sessions import write_json
from tests.native_input_safety import owned, connect
from tests.native_input_smoke import read, until


class Mcp:
    def __init__(self, directory, exe):
        self.directory = directory
        self.records = []
        self.responses = queue.Queue()
        profile = directory / 'mcp-profile.json'
        write_json(profile, {'name': 'native-input-owned', 'proc': exe.stem,
                             'exe_path': str(exe), 'data_dir': str(directory / 'mcp-data')})
        self.log = (directory / 'mcp-stderr.log').open('w', encoding='utf-8')
        self.child = subprocess.Popen([sys.executable, str(ROOT / 'gameqakit/mcp_server.py')],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log,
                                     text=True, encoding='utf-8', env={**os.environ, 'GAMEQAKIT_PROFILE': str(profile), 'PYTHONUTF8': '1'})
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        self.number = 0

    def _read(self):
        for line in self.child.stdout:
            self.responses.put(json.loads(line))

    def send(self, method, params):
        self.number += 1
        request = {'jsonrpc': '2.0', 'id': self.number, 'method': method, 'params': params}
        self.records.append({'request': request, 'sent_at': time.time()})
        self.child.stdin.write(json.dumps(request) + '\n')
        self.child.stdin.flush()

    def call(self, name, *, expect_error=False, **arguments):
        self.send('tools/call', {'name': name, 'arguments': {'directory': str(self.directory / 'bridge'), **arguments}})
        response = self.responses.get(timeout=6)
        assert response['id'] == self.number, response
        for item in response.get('result', {}).get('content', []):
            if item.get('type') == 'image':
                image = base64.b64decode(item.pop('data'), validate=True)
                item['bytes'], item['sha256'] = len(image), hashlib.sha256(image).hexdigest()
        self.records[-1]['response'] = response
        self.records[-1]['received_at'] = time.time()
        self.records[-1]['bridge_status'] = read(self.directory / 'bridge/status.json')
        if expect_error:
            assert 'error' in response, response
            return response['error']
        assert 'error' not in response, response
        return json.loads(response['result']['content'][0]['text'])

    def close(self):
        if self.child.poll() is None:
            self.child.stdin.close()
            try:
                self.child.wait(3)
            except subprocess.TimeoutExpired:
                self.child.kill()
                self.child.wait(3)
        self.reader.join(2)
        self.child.stdout.close()
        if not self.child.stdin.closed:
            self.child.stdin.close()
        self.log.close()
        write_json(self.directory / 'mcp-transcript.json', self.records)


@contextmanager
def mcp(directory, exe):
    client = Mcp(directory, exe)
    try:
        yield client
    finally:
        client.close()


def run(output, binaries):
    output.mkdir(parents=True, exist_ok=False)
    case = output / 'entry'
    with owned(case, binaries) as (host, exe, checks):
        bridge, meta = connect(case, host, exe)
        with mcp(case, exe) as rpc:
            status = rpc.call('control_status')
            checks['identity'] = status['connected'] and status['pid'] == meta['pid'] and status['timing_mode'] == 'duration_ms'
            resumed = rpc.call('control_resume', confirm=True, command_id='mcp-resume')
            published = rpc.records[-1]['bridge_status']
            checks['resume_published_before_reply'] = (not published['paused'] and
                                                       published['observation_seq'] >= resumed['observation_seq'])
            assert checks['resume_published_before_reply'], {'status': published, 'result': resumed}
            before = read(case / 'target-state.json')
            action = rpc.call('control_action', confirm=True, action='move', direction='right', duration_ms=120,
                              command_id='mcp-right', include_image=True)
            result = rpc.call('control_result', command_id='mcp-right', include_image=True)
            after = read(case / 'target-state.json')
            checks['normal_input'] = action['status'] == 'done' and action['press_consumed'] and action['release_consumed'] and after['presses'] == before['presses'] + 1 and after['releases'] == before['releases'] + 1 and not after['down']
            checks['visible_movement'] = after['position'] > before['position'] and action['screenshot_sha256'] != resumed['screenshot_sha256']
            checks['image_chain'] = action['id'] == result['id'] and action['screenshot_sha256'] == result['screenshot_sha256'] == hashlib.sha256(Path(result['screenshot']).read_bytes()).hexdigest()
            images = [part for part in rpc.records[-1]['response']['result']['content'] if part['type'] == 'image']
            checks['inline_image'] = len(images) == 1 and images[0]['sha256'] == result['screenshot_sha256']
            replay = rpc.call('control_action', confirm=True, action='move', direction='right', duration_ms=120, command_id='mcp-right')
            checks['replay_no_input'] = replay['screenshot_sha256'] == action['screenshot_sha256'] and read(case / 'target-state.json')['presses'] == after['presses']
            request_before = (case / 'bridge/request.json').read_bytes()
            rpc.call('control_action', expect_error=True, confirm=True, action='move', direction='right', frames=1, command_id='wrong-unit')
            checks['wrong_unit_rejected'] = (case / 'bridge/request.json').read_bytes() == request_before
            confirmed = rpc.call('control_action', confirm=True, action='confirm', duration_ms=120, command_id='mcp-confirm')
            checks['sample_confirm_z'] = confirmed['press_consumed'] and confirmed['release_consumed'] and read(case / 'target-state.json')['last_scan_code'] == 0x2c
            assert checks['sample_confirm_z'], read(case / 'target-state.json')
            cancelled = rpc.call('control_action', confirm=True, action='cancel', duration_ms=120, command_id='mcp-cancel')
            checks['sample_cancel_x'] = cancelled['press_consumed'] and cancelled['release_consumed'] and read(case / 'target-state.json')['last_scan_code'] == 0x2d
            assert checks['sample_cancel_x'], read(case / 'target-state.json')
            rpc.call('control_pause')
            until(lambda: bridge.status().get('paused') and bridge.status().get('held') == [])
            checks['pause'] = rpc.call('control_status')['paused'] and not read(case / 'target-state.json')['down']
            rpc.call('control_resume', confirm=True, command_id='mcp-resume-2')
            rpc.send('tools/call', {'name': 'control_action', 'arguments': {'directory': str(case / 'bridge'), 'action': 'move', 'direction': 'right', 'duration_ms': 1000, 'command_id': 'mcp-interrupted', 'confirm': True}})
            until(lambda: read(case / 'target-state.json')['down'], 1)
            rpc.child.kill()
            rpc.child.wait(3)
            until(lambda: bridge.status().get('error') == 'lease_expired', 3)
            state = bridge.status()
            checks['mcp_exit_release'] = state['connected'] and state['paused'] and state['held'] == [] and not read(case / 'target-state.json')['down']
    write_json(output / 'result.json', read(case / 'result.json'))
    print(json.dumps(read(output / 'result.json')))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--binaries', type=Path, required=True)
    args = parser.parse_args()
    run(args.output.resolve(), args.binaries.resolve())
