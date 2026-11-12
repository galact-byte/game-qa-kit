"""真实文件协议的行为测试；假插件只替代引擎，不替代客户端的校验与留证。"""
import configparser
import importlib
import importlib.util
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
from gameqakit.sessions import SessionLease, write_json


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.assertIsNotNone(importlib.util.find_spec('gameqakit.control'), '尚无统一后台控制客户端')
        self.control = importlib.import_module('gameqakit.control')
        self.meta = {'protocol': 1, 'session_id': 'session-a', 'pid': os.getpid(),
                     'exe_path': sys.executable, 'engine': 'rpgmaker_mv'}
        self.state = {**self.meta, 'updated_at': time.time(), 'frame': 10, 'paused': False,
                      'capabilities': ['move', 'confirm', 'cancel', 'wait', 'press', 'release', 'click'],
                      'screen': [320, 240], 'scene': 'Scene_Map', 'input_ready': True}
        write_json(self.root / 'connection.json', self.meta)
        write_json(self.root / 'status.json', self.state)
        self.client = self.control.BridgeClient(self.root, pid=os.getpid(), exe_path=sys.executable)
        self.stop = threading.Event()
        self.workers = []
        self.addCleanup(self.cleanup_workers)

    def cleanup_workers(self):
        self.stop.set()
        for worker in self.workers:
            worker.join(timeout=2)

    def plugin_once(self, mutation=None):
        def respond():
            while not self.stop.wait(0.005):
                request_path = self.root / 'request.json'
                if not request_path.exists():
                    continue
                try:
                    request = json.loads(request_path.read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    continue
                shot = self.root / 'screenshots' / (request['id'] + '.png')
                shot.parent.mkdir(exist_ok=True)
                Image.new('RGB', (320, 240), (20, 70, 100)).save(shot)
                result = {'protocol': 1, 'session_id': 'session-a', 'id': request['id'], 'ok': True,
                          'consumed': True, 'frame': 11, 'capture_frame': 11,
                          'screenshot': 'screenshots/' + request['id'] + '.png',
                          'scene': 'Scene_Map', 'paused': False}
                if mutation:
                    mutation(result, shot)
                write_json(self.root / 'results' / (request['id'] + '.json'), result)
                return
        worker = threading.Thread(target=respond)
        self.workers.append(worker)
        worker.start()

    def test_action_returns_verified_post_action_image_and_frame(self):
        self.plugin_once()
        result = self.client.action('move', direction='right', frames=3, command_id='move-a', timeout=1)
        self.assertTrue(result['consumed'])
        self.assertEqual(result['frame'], 11)
        with Image.open(result['screenshot']) as image:
            self.assertEqual(image.size, (320, 240))
        request = json.loads((self.root / 'request.json').read_text())
        self.assertEqual(request['payload'], {'action': 'move', 'direction': 'right', 'frames': 3})
        self.assertEqual(request['after_frame'], 10)

    def test_optional_square_requires_engine_declaration(self):
        for declared in (None, [], ['ok'], 'square'):
            with self.subTest(buttons=declared):
                write_json(self.root / 'status.json', {**self.state, 'buttons': declared})
                with self.assertRaises((ValueError, RuntimeError)):
                    self.client.action('press', button='square', timeout=0.05)
                self.assertFalse((self.root / 'request.json').exists())
        write_json(self.root / 'status.json', {**self.state, 'buttons': ['square']})
        self.plugin_once()
        result = self.client.action('press', button='square', frames=12, command_id='square', timeout=1)
        self.assertTrue(result['consumed'])
        request = json.loads((self.root / 'request.json').read_text())
        self.assertEqual(request['payload'], {'action': 'press', 'button': 'square', 'frames': 12})

    def test_invalid_actions_bounds_ids_and_extra_fields_do_not_queue(self):
        for action, options in [('move', {'direction': 'diagonal'}), ('wait', {'frames': 0}),
                                ('wait', {'frames': True}), ('press', {'button': 'f12'}),
                                ('click', {'x': 320, 'y': 10}), ('click', {'x': 0, 'y': -1}),
                                ('wait', {'command_id': '../outside'}), ('wait', {'unexpected': 1})]:
            with self.subTest(action=action, options=options), self.assertRaises(ValueError):
                self.client.action(action, timeout=0.05, **options)
        self.assertFalse((self.root / 'request.json').exists())

    def test_stale_or_wrong_process_session_never_sends(self):
        variants = [{'updated_at': 0}, {'session_id': 'old'}, {'pid': os.getpid() + 1},
                    {'exe_path': 'C:/not-the-game.exe'}, {'protocol': 999}]
        for variant in variants:
            with self.subTest(variant=variant):
                write_json(self.root / 'status.json', {**self.state, **variant})
                with self.assertRaises(RuntimeError):
                    self.client.action('wait', command_id='not-sent', timeout=0.05)
                self.assertFalse((self.root / 'request.json').exists())

    def test_missing_capability_or_manual_pause_is_not_desktop_fallback(self):
        for variant in [{'capabilities': []}, {'paused': True}, {'input_ready': False}]:
            with self.subTest(variant=variant):
                write_json(self.root / 'status.json', {**self.state, **variant})
                with self.assertRaises(RuntimeError):
                    self.client.action('confirm', timeout=0.05)
        self.assertFalse((self.root / 'request.json').exists())

    def test_duplicate_id_returns_result_without_resending_but_rejects_new_payload(self):
        self.plugin_once()
        self.client.action('wait', command_id='once', timeout=1)
        before = (self.root / 'request.json').stat().st_mtime_ns
        result = self.client.action('wait', command_id='once', timeout=1)
        self.assertTrue(result['ok'])
        self.assertEqual((self.root / 'request.json').stat().st_mtime_ns, before)
        with self.assertRaises(ValueError):
            self.client.action('confirm', command_id='once', timeout=0.05)

    def test_old_frame_invalid_image_and_escaping_path_fail_closed(self):
        mutations = [lambda result, shot: result.update(frame=10, capture_frame=10),
                     lambda result, shot: result.update(screenshot='../outside.png'),
                     lambda result, shot: shot.write_bytes(b'not png'),
                     lambda result, shot: Image.new('RGB', (320, 240), 'black').save(shot)]
        for index, mutation in enumerate(mutations):
            with self.subTest(index=index):
                self.plugin_once(mutation)
                with self.assertRaises(RuntimeError):
                    self.client.action('wait', command_id='bad-' + str(index), timeout=1)
                self.workers[-1].join(timeout=2)
                self.assertTrue((self.root / 'pause.json').exists())
                (self.root / 'request.json').unlink()

    def test_timeout_pauses_and_query_does_not_resend(self):
        with self.assertRaises(TimeoutError):
            self.client.action('wait', command_id='slow', timeout=0.05)
        self.assertTrue((self.root / 'pause.json').exists())
        before = (self.root / 'request.json').stat().st_mtime_ns
        result = self.client.result('slow')
        self.assertEqual(result['status'], 'pending')
        self.assertEqual((self.root / 'request.json').stat().st_mtime_ns, before)

    def test_pause_bypasses_inflight_action_lock_and_returns_requested_not_confirmed(self):
        with SessionLease(self.root / 'host.lock'):
            result = self.client.pause()
        self.assertEqual(result['state'], 'pause_requested')
        self.assertTrue((self.root / 'pause.json').exists())

    def test_ini_transport_uses_same_actions_and_normalizes_native_bmp_evidence(self):
        meta = {**self.meta, 'command_format': 'ini'}
        self.control.write_connection(self.root, meta)
        write_json(self.root / 'status.json', {**self.state, 'max_input_frames': 1})
        def respond():
            while not self.stop.wait(0.005):
                if not (self.root / 'request.ini').exists():
                    continue
                parser = configparser.ConfigParser(interpolation=None)
                parser.read(self.root / 'request.ini', encoding='utf-8')
                row = parser['bridge']
                self.assertEqual(row['payload.action'], 'wait')
                self.assertTrue((self.root / 'lease.ini').exists())
                identifier = row['id']
                shot = self.root / 'screenshots' / (identifier + '.bmp')
                shot.parent.mkdir(exist_ok=True)
                Image.new('RGB', (320, 240), (30, 70, 110)).save(shot)
                write_json(self.root / 'results' / (identifier + '.json'), {
                    'protocol': 1, 'session_id': 'session-a', 'id': identifier,
                    'ok': True, 'consumed': True, 'frame': 11, 'capture_frame': 11,
                    'screenshot': 'screenshots/' + identifier + '.bmp'})
                return
        worker = threading.Thread(target=respond)
        self.workers.append(worker)
        worker.start()
        result = self.client.action('wait', command_id='native-bmp', timeout=1)
        with Image.open(result['screenshot']) as image:
            self.assertEqual(image.format, 'PNG')
        self.client.pause()
        self.assertTrue((self.root / 'pause.ini').exists())
        with self.assertRaises(ValueError):
            self.client.action('move', direction='right', frames=2, timeout=0.05)

    def test_image_from_other_command_is_not_accepted_as_new_evidence(self):
        self.plugin_once(lambda result, shot: result.update(screenshot='screenshots/other.png'))
        (self.root / 'screenshots').mkdir(exist_ok=True)
        Image.new('RGB', (320, 240), 'blue').save(self.root / 'screenshots/other.png')
        with self.assertRaises(RuntimeError):
            self.client.action('wait', command_id='new-command', timeout=1)

    def test_mcp_uses_unified_tools_requires_input_consent_and_can_return_image(self):
        from gameqakit import mcp_server as server
        from gameqakit.profile import GameProfile
        profile = GameProfile(name='isolated-test', proc=Path(sys.executable).stem,
                              exe_path=sys.executable, target_pid=os.getpid())
        with mock.patch.object(server, '_PROFILE', profile):
            status = server.handle('tools/call', {'name': 'control_status', 'arguments': {'directory': str(self.root)}})
            self.assertTrue(json.loads(status['content'][0]['text'])['connected'])
            for name in ['control_action', 'control_resume']:
                with self.subTest(name=name), self.assertRaises(ValueError):
                    server.handle('tools/call', {'name': name, 'arguments': {'directory': str(self.root)}})
            self.plugin_once()
            response = server.handle('tools/call', {'name': 'control_action', 'arguments': {
                'directory': str(self.root), 'action': 'wait', 'frames': 2,
                'confirm': True, 'include_image': True, 'command_id': 'mcp-step', 'timeout': 1}})
            self.assertEqual(json.loads(response['content'][0]['text'])['status'], 'done')
            self.assertEqual(response['content'][1]['type'], 'image')
            self.assertEqual(response['content'][1]['mimeType'], 'image/png')
            self.assertFalse((self.root / 'pause.json').exists())
            paused = server.handle('tools/call', {'name': 'control_pause', 'arguments': {'directory': str(self.root)}})
            self.assertEqual(json.loads(paused['content'][0]['text'])['state'], 'pause_requested')

    def test_mcp_engine_inspection_does_not_require_active_game(self):
        from gameqakit import mcp_server as server
        (self.root / 'js').mkdir()
        (self.root / 'js/rmmz_core.js').touch()
        result = server.handle('tools/call', {'name': 'engine_inspect', 'arguments': {'path': str(self.root)}})
        self.assertEqual(json.loads(result['content'][0]['text'])['engine'], 'rpgmaker_mz')

    def test_registered_script_entry_exposes_engine_and_control_tools(self):
        import subprocess
        from gameqakit.profile import GameProfile
        (self.root / 'js').mkdir()
        (self.root / 'js/rmmz_core.js').touch()
        profile = self.root / 'profile.json'
        write_json(profile, GameProfile(name='script-test', proc=Path(sys.executable).stem,
                                       exe_path=sys.executable).to_dict())
        messages = [
            {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
                'name': 'engine_inspect', 'arguments': {'path': str(self.root)}}},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {
                'name': 'control_status', 'arguments': {'directory': str(self.root)}}},
            {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {
                'name': 'control_pause', 'arguments': {'directory': str(self.root)}}},
        ]
        script = Path(__file__).resolve().parent.parent / 'gameqakit/mcp_server.py'
        result = subprocess.run([sys.executable, str(script)],
                                input='\n'.join(json.dumps(row) for row in messages) + '\n',
                                text=True, capture_output=True, encoding='utf-8', timeout=5,
                                env={**os.environ, 'GAMEQAKIT_PROFILE': str(profile)})
        self.assertEqual(result.returncode, 0, result.stderr)
        responses = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(responses), 3)
        self.assertTrue(all('result' in row for row in responses), responses)
        values = [json.loads(row['result']['content'][0]['text']) for row in responses]
        self.assertEqual(values[0]['engine'], 'rpgmaker_mz')
        self.assertTrue(values[1]['connected'])
        self.assertEqual(values[2]['state'], 'pause_requested')
        self.assertEqual(result.stderr, '')

    def test_lease_exists_before_command_is_published(self):
        def publish(path, value):
            if Path(path).name == 'request.json':
                self.assertTrue((self.root / 'lease.json').exists(), '插件可能先看到命令、却看不到租约')
            return write_json(path, value)
        self.plugin_once()
        with mock.patch.object(self.control, 'write_json', side_effect=publish):
            self.client.action('wait', command_id='leased', timeout=1)

    def test_file_lease_renews_during_image_validation_and_stops_after_return(self):
        for format_name in ('json', 'ini'):
            with self.subTest(format=format_name):
                meta = {**self.meta, 'command_format': format_name}
                self.control.write_connection(self.root, meta)
                self.client = self.control.BridgeClient(self.root, pid=os.getpid(), exe_path=sys.executable)
                observed = []
                original = Image.open

                def slow_open(*args, **kwargs):
                    time.sleep(.25)
                    path = self.root / ('lease.' + format_name)
                    if format_name == 'json':
                        expiry = json.loads(path.read_text())['expires_at']
                    else:
                        parser = configparser.ConfigParser(interpolation=None)
                        parser.read(path, encoding='utf-8')
                        expiry = float(parser['bridge']['expires_at'])
                    observed.append(expiry)
                    return original(*args, **kwargs)

                # The response fixture uses JSON requests; publish the matching reply
                # before the client waits, including for the INI wire variant.
                command_id = 'slow-validation-' + format_name
                shot = self.root / 'screenshots' / (command_id + '.png')
                shot.parent.mkdir(exist_ok=True)
                Image.new('RGB', (32, 24), 'blue').save(shot)
                write_json(self.root / 'results' / (command_id + '.json'), {
                    'protocol': 1, 'session_id': 'session-a', 'id': command_id,
                    'ok': True, 'consumed': True, 'frame': 11, 'capture_frame': 11,
                    'screenshot': 'screenshots/' + shot.name})
                started = time.time()
                with mock.patch.object(self.control.Image, 'open', side_effect=slow_open):
                    result = self.client.action('wait', command_id=command_id, timeout=2)
                self.assertEqual(result['status'], 'done')
                self.assertGreater(observed[0], started + 1.6, '图像校验期间没有发布新租约')
                lease = self.root / ('lease.' + format_name)
                after = lease.read_bytes()
                time.sleep(.15)
                self.assertEqual(lease.read_bytes(), after, '动作返回后仍在续租')

    def test_background_renewal_failure_pauses_and_stops_worker(self):
        original = self.client._wire_write
        publications = []

        def fail_renewal(name, value, meta=None):
            if name == 'lease':
                publications.append(value)
                if len(publications) > 1:
                    raise OSError('renewal unavailable')
            return original(name, value, meta)

        with mock.patch.object(self.client, '_wire_write', side_effect=fail_renewal):
            with self.assertRaisesRegex(RuntimeError, '控制续租失败'):
                self.client.action('wait', command_id='renewal-fails', timeout=1)
        self.assertTrue((self.root / 'pause.json').exists())
        self.assertEqual(len(publications), 2)
        self.assertFalse(any(t.name == 'control-lease' for t in threading.enumerate()))

    def test_historical_result_is_labeled_after_session_changes(self):
        self.plugin_once()
        self.client.action('wait', command_id='past', timeout=1)
        write_json(self.root / 'connection.json', {**self.meta, 'session_id': 'session-b'})
        result = self.client.result('past')
        self.assertTrue(result.get('historical'))

    def test_pause_is_target_and_session_scoped_without_waiting_for_action_lock(self):
        self.client.pause()
        record = json.loads((self.root / 'pause.json').read_text())
        self.assertEqual(record.get('session_id'), 'session-a')
        write_json(self.root / 'pause.json', {'id': 'old-stop', 'session_id': 'old-session'})
        self.plugin_once()
        self.client.action('wait', command_id='new-session-action', timeout=1)
        request = json.loads((self.root / 'request.json').read_text())
        self.assertEqual(request['pause_id'], '')
        wrong = self.control.BridgeClient(self.root, pid=os.getpid() + 1, exe_path=sys.executable)
        with self.assertRaises(RuntimeError):
            wrong.pause()

    def test_result_before_manual_takeover_is_labeled_historical_even_in_same_session(self):
        self.plugin_once()
        self.client.action('wait', command_id='before-takeover', timeout=1)
        self.client.pause()
        self.assertTrue(self.client.result('before-takeover')['historical'])

    def test_request_carries_pause_generation_to_prevent_queued_action_reviving(self):
        write_json(self.root / 'pause.json', {'id': 'human-stop', 'session_id': 'session-a'})
        write_json(self.root / 'status.json', {**self.state, 'paused': True})
        self.plugin_once()
        self.client.resume(command_id='explicit-resume', timeout=1)
        request = json.loads((self.root / 'request.json').read_text())
        self.assertEqual(request['pause_id'], 'human-stop')
        self.assertEqual(request['payload']['action'], 'resume')


if __name__ == '__main__':
    unittest.main()
