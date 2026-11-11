"""v2 毫秒动作通过真实文件会话，保留 v1 帧语义。"""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from PIL import Image
from gameqakit.control import BridgeClient, write_connection
from gameqakit.sessions import read_json, write_json


class NativeProtocolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.meta = {'protocol': 2, 'pid': os.getpid(), 'exe_path': sys.executable,
                     'session_id': 'native-test', 'control_transport': 'files-v1',
                     'engine': 'native_input'}
        self.state = {**self.meta, 'updated_at': time.time(), 'observation_seq': 5,
                      'paused': False, 'held': [], 'timing_mode': 'duration_ms',
                      'pause_scope': 'input', 'simulation_pause': False, 'max_duration_ms': 1000,
                      'capabilities': ['move', 'confirm', 'cancel', 'wait'],
                      'buttons': ['up', 'down', 'left', 'right', 'ok', 'cancel'],
                      'input_ready': True}
        write_connection(self.root, self.meta)
        write_json(self.root / 'status.json', self.state)
        self.client = BridgeClient(self.root, pid=os.getpid(), exe_path=sys.executable)
        self.stop = threading.Event()
        self.workers = []
        self.errors = []
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.stop.set()
        for thread in self.workers:
            thread.join(2)
            self.assertFalse(thread.is_alive())
        self.assertEqual(self.errors, [])

    def plugin_once(self, mutation=None):
        def publish():
            try:
                while not self.stop.wait(.005):
                    path = self.root / 'request.json'
                    if not path.exists():
                        continue
                    request = read_json(path)
                    identifier = request['id']
                    keyed = request['payload']['action'] not in ('wait', 'resume')
                    shot = self.root / 'screenshots' / (identifier + '.png')
                    shot.parent.mkdir(exist_ok=True)
                    Image.new('RGB', (64, 32), (40, 80, 120)).save(shot)
                    result = {'protocol': 2, 'session_id': self.meta['session_id'], 'id': identifier,
                              'pause_id': request['pause_id'], 'ok': True, 'consumed': keyed,
                              'observation_seq': 6, 'screenshot': 'screenshots/' + shot.name,
                              'screenshot_sha256': hashlib.sha256(shot.read_bytes()).hexdigest(),
                              'timing_mode': 'duration_ms', 'pause_scope': 'input',
                              'simulation_pause': False, 'started_monotonic_ms': 1000,
                              'completed_monotonic_ms': 1160, 'press_consumed': keyed,
                              'release_consumed': keyed, 'held': [], 'paused': False}
                    if mutation:
                        mutation(result, shot)
                    write_json(self.root / 'results' / (identifier + '.json'), result)
                    return
            except Exception as exc:
                self.errors.append(str(exc))
        thread = threading.Thread(target=publish)
        self.workers.append(thread)
        thread.start()

    def request(self):
        return read_json(self.root / 'request.json')

    def test_time_action_has_observation_evidence_and_no_synthetic_game_frame(self):
        self.plugin_once()
        result = self.client.action('move', direction='right', duration_ms=120,
                                    command_id='time-move', timeout=1)
        self.assertEqual(result['status'], 'done')
        self.assertEqual(result['observation_seq'], 6)
        self.assertNotIn('frame', result)
        self.assertEqual(self.request()['after_observation_seq'], 5)
        self.assertEqual(self.request()['payload'], {'action': 'move', 'direction': 'right', 'duration_ms': 120})
        self.assertTrue((self.root / 'lease.json').is_file())

    def test_explicit_duration_and_only_approved_actions_before_publication(self):
        invalid = [('move', {'direction': 'right'}), ('wait', {'frames': 1}),
                   ('wait', {'duration_ms': 100, 'frames': 1}),
                   ('wait', {'duration_ms': True}), ('wait', {'duration_ms': 0}),
                   ('wait', {'duration_ms': 1001}), ('wait', {'duration_ms': 1.5}),
                   ('click', {'x': 1, 'y': 1, 'duration_ms': 100}),
                   ('press', {'button': 'ok', 'duration_ms': 100}),
                   ('release', {'duration_ms': 100}), ('move', {'direction': 'ok', 'duration_ms': 100})]
        for action, options in invalid:
            with self.subTest(action=action, options=options), self.assertRaises((ValueError, RuntimeError)):
                self.client.action(action, timeout=.05, **options)
        self.assertFalse((self.root / 'request.json').exists())
        self.assertFalse((self.root / 'requests').exists())

    def test_state_rejects_wrong_semantics_or_capabilities_without_input(self):
        variants = [{'timing_mode': 'game_frames'}, {'pause_scope': 'game_loop'},
                    {'simulation_pause': True}, {'observation_seq': True}, {'observation_seq': -1},
                    {'frame': 1}, {'max_duration_ms': 1001}, {'buttons': 'ok'},
                    {'held': ['unknown']}, {'capabilities': ['click']}, {'held': [None]}]
        for variant in variants:
            write_json(self.root / 'status.json', {**self.state, **variant})
            with self.subTest(variant=variant):
                self.assertFalse(self.client.status()['connected'])
        self.assertFalse((self.root / 'request.json').exists())

    def test_unverified_results_fail_and_request_pause(self):
        variants = [{'observation_seq': 5}, {'observation_seq': True}, {'held': ['right']},
                    {'timing_mode': 'game_frames'}, {'protocol': 1}, {'pause_id': 'other'},
                    {'press_consumed': False}, {'release_consumed': False},
                    {'started_monotonic_ms': 1200}, {'completed_monotonic_ms': True},
                    {'screenshot_sha256': '0' * 64}, {'frame': 1}]
        for index, change in enumerate(variants):
            (self.root / 'request.json').unlink(missing_ok=True)
            self.plugin_once(lambda row, shot: row.update(change))
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                self.client.action('confirm', duration_ms=100, command_id='bad-' + str(index), timeout=1)
            self.workers[-1].join(2)
            self.assertTrue((self.root / 'pause.json').exists())

    def test_wait_does_not_claim_key_consumption(self):
        self.plugin_once()
        result = self.client.action('wait', duration_ms=100, command_id='wait', timeout=1)
        self.assertFalse(result['consumed'])
        self.assertFalse(result['press_consumed'])

    def test_duplicate_is_not_sent_again_and_changed_duration_is_rejected(self):
        self.plugin_once()
        self.client.action('wait', duration_ms=100, command_id='once', timeout=1)
        before = self.request()
        self.assertEqual(self.client.action('wait', duration_ms=100, command_id='once', timeout=1)['status'], 'done')
        self.assertEqual(self.request(), before)
        with self.assertRaises(ValueError):
            self.client.action('wait', duration_ms=101, command_id='once', timeout=.05)

    def test_resume_has_no_time_field_and_pause_is_independent_of_action_lock(self):
        write_json(self.root / 'status.json', {**self.state, 'paused': True})
        self.plugin_once()
        self.client.resume(command_id='resume', timeout=1)
        self.assertEqual(self.request()['payload'], {'action': 'resume'})
        from gameqakit.sessions import SessionLease
        with SessionLease(self.root / 'host.lock'):
            self.assertEqual(self.client.pause()['state'], 'pause_requested')
        self.assertTrue(self.client.result('resume')['historical'])

    def test_timeout_requests_pause_and_query_does_not_repeat_action(self):
        with self.assertRaises(TimeoutError):
            self.client.action('wait', duration_ms=50, command_id='timeout', timeout=.05)
        before = self.request()
        self.assertTrue((self.root / 'pause.json').exists())
        self.assertEqual(self.client.result('timeout')['status'], 'pending')
        self.assertEqual(self.request(), before)

    def test_unapproved_transport_is_refused(self):
        write_connection(self.root, {**self.meta, 'control_transport': 'native-shm-v1'})
        self.assertFalse(self.client.status()['connected'])
        self.assertFalse((self.root / 'request.json').exists())

    def test_mcp_handler_passes_explicit_milliseconds_and_returns_image(self):
        from gameqakit import mcp_server as server
        self.plugin_once()
        self.assertEqual(server.TOOLS['control_action'][1]['properties']['duration_ms']['maximum'], 1000)
        with mock.patch.object(server, '_bridge', return_value=self.client):
            response = server.handle('tools/call', {'name': 'control_action', 'arguments': {
                'action': 'wait', 'duration_ms': 100, 'confirm': True, 'command_id': 'mcp',
                'include_image': True, 'timeout': 1}})
        self.assertEqual(json.loads(response['content'][0]['text'])['status'], 'done')
        self.assertEqual(response['content'][1]['type'], 'image')


if __name__ == '__main__':
    unittest.main()
