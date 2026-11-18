import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from gameqakit import admission
from gameqakit.sessions import read_json
from gameqakit.win32 import _pid_path
from PIL import Image
from plugins.renpy.prepare import BRIDGE, SCRIPT
from tests.native_input_mcp import mcp, until
from tests.test_renpy_bridge import RUNTIME


@unittest.skipUnless(os.name == "nt" and RUNTIME, "Requires the registered RenPy runtime")
class RenpyManagedTests(unittest.TestCase):
    def test_only_fixed_authored_project_can_be_prepared(self):
        script = self.source / SCRIPT
        plan = admission.make_plan(self.source, self.destination, artifacts=RUNTIME)
        self.assertEqual(plan['backend'], 'renpy_authored')
        self.assertEqual(admission.revalidate_plan(plan), plan)
        script.write_bytes(script.read_bytes() + b'# changed\n')
        self.assertEqual(admission.inspect(self.source, artifacts=RUNTIME)['launch_readiness'], 'blocked')
        self.assertFalse(self.destination.exists())

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='gameqa-renpy-managed-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / 'sample'
        self.source.mkdir()
        shutil.copyfile(BRIDGE.parent / 'samples' / SCRIPT, self.source / SCRIPT)
        self.destination = self.root / 'session'
        if evidence := os.environ.get('GAMEQAKIT_TEST_EVIDENCE'):
            self.addCleanup(shutil.copytree, self.root, Path(evidence) / self._testMethodName,
                            dirs_exist_ok=True, ignore=shutil.ignore_patterns('lib', 'renpy', 'appdata', 'localappdata', 'tmp'))

    def rpc(self, client, name, *, expect_error=False, **arguments):
        client.send('tools/call', {'name': name, 'arguments': arguments})
        response = client.responses.get(timeout=10)
        self.assertEqual(response['id'], client.number)
        client.records[-1]['response'] = response
        if expect_error:
            self.assertIn('error', response)
            return response['error']['message']
        self.assertNotIn('error', response)
        return json.loads(response['result']['content'][0]['text'])

    def wait_state(self, client, expected):
        def poll():
            state = self.rpc(client, 'game_status', directory=str(self.destination))
            terminal = state['state'] in {'stopped', 'failed', 'cleanup_failed'}
            if state['state'] == expected and (not terminal or not state['worker_alive']):
                return state
            if terminal and state['state'] != expected:
                self.assertEqual(state['state'], expected, state)
        return until(poll, seconds=35)

    def prepare(self, client):
        result = self.rpc(client, 'game_inspect', path=str(self.source), artifacts=RUNTIME,
                          destination=str(self.destination))
        self.assertEqual(result['launch_readiness'], 'ready', result)
        plan = result['plan']
        self.rpc(client, 'game_prepare', plan=plan, request_id='prepare', confirm=True)
        self.wait_state(client, 'prepared')
        return plan

    def start(self, client, plan):
        self.rpc(client, 'game_start', directory=str(self.destination), fingerprint=plan['fingerprint'],
                 request_id='start', confirm=True)

    def ready(self, client):
        self.start(client, self.prepare(client))
        state = self.wait_state(client, 'ready_paused')
        self.assertEqual((state['paused'], state['held']), (True, []))
        self.assertEqual(admission.file_digest(state['screenshot']), state['screenshot_sha256'])
        self.assertFalse((self.destination / 'bridge/request.json').exists())
        self.rpc(client, 'game_bind', directory=state['bridge_directory'], session_id=state['session_id'], confirm=True)
        self.rpc(client, 'control_resume', command_id='resume', confirm=True)
        return state

    def connection(self):
        return mcp(self.root, Path(RUNTIME).resolve() / 'lib/py3-windows-x86_64/pythonw.exe')

    def test_changed_prepared_script_is_rejected_before_launch(self):
        with self.connection() as client:
            plan = self.prepare(client)
            path = self.destination / 'runtime/game' / SCRIPT
            path.write_bytes(path.read_bytes() + b'# changed after prepare\n')
            self.start(client, plan)
            failed = self.wait_state(client, 'failed')
            self.assertIn('runtime_changed', failed['error'])
            self.assertNotIn('pid', failed)
            self.assertFalse(any((self.destination / 'bridge').iterdir()))

    def test_registered_scene_starts_paused_moves_and_stops(self):
        with self.connection() as client:
            ready = self.ready(client)
            result = self.rpc(client, 'control_action', action='move', direction='right', duration_ms=120,
                              command_id='right', confirm=True)
            self.assertTrue(result['press_consumed'] and result['release_consumed'])
            def orange_left(path):
                with Image.open(path) as image:
                    return min(x for x in range(image.width)
                               if image.getpixel((x, image.height // 2))[:3] == (255, 128, 32))
            self.assertGreater(orange_left(result['screenshot']), orange_left(ready['screenshot']))
            self.rpc(client, 'game_stop', directory=str(self.destination), request_id='stop', confirm=True)
            stopped = self.wait_state(client, 'stopped')
            self.assertTrue(stopped['target_exited'] and stopped['release_confirmed'])
            self.assertTrue(stopped['source_unchanged'] and stopped['runtime_source_unchanged'])
            fixture = read_json(self.destination / 'fixture.json')
            self.assertEqual((fixture['x'], fixture['held']), (140, False))
            self.assertEqual([event['type'] for event in fixture['events']], ['down', 'up'])
        for pid in (ready['pid'], ready['worker_pid'], client.child.pid):
            self.assertFalse(_pid_path(pid), f'PID {pid} still running')

    def test_black_frame_preserves_receipt_and_stops(self):
        with self.connection() as client:
            ready = self.ready(client)
            (self.destination / 'blank-after-confirm').touch()
            error = self.rpc(client, 'control_action', action='confirm', duration_ms=50,
                             command_id='black', confirm=True, expect_error=True)
            self.assertIn('blank', error)
            receipt = read_json(self.destination / 'bridge/results/black.json')
            self.assertEqual((receipt['ok'], receipt['press_consumed'], receipt['release_consumed']), (False, True, True))
            self.assertFalse((self.destination / 'bridge/screenshots/black.png').exists())
            failed = self.wait_state(client, 'failed')
            self.assertIn('observation_failed', failed['error'])
            self.assertTrue(failed['target_exited'] and failed['release_confirmed'])
            self.rpc(client, 'control_resume', command_id='blocked', confirm=True, expect_error=True)
            self.assertEqual(read_json(self.destination / 'bridge/request.json')['id'], 'black')
        for pid in (ready['pid'], ready['worker_pid'], client.child.pid):
            self.assertFalse(_pid_path(pid), f'PID {pid} still running')
