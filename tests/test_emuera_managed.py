import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image, ImageChops

from gameqakit import admission
from gameqakit.sessions import read_json
from gameqakit.win32 import _pid_path
from plugins.emuera.session import EmueraSession
from tests.native_input_mcp import mcp, until
from tests.test_emuera_bridge import COMPONENTS


class EmueraCleanupTests(unittest.TestCase):
    def test_already_paused_capture_fault_reuses_supervisor_release_confirmation(self):
        with tempfile.TemporaryDirectory() as temp:
            session = EmueraSession({'destination': temp, 'source': None, 'source_digests': {},
                                     'artifacts': None, 'artifact_digests': {}})
            session.directory.mkdir()
            session.host = Mock()
            session.host.alive.side_effect = [True, False]
            session.bridge = Mock()
            session.bridge.status.return_value = {
                'connected': True, 'paused': True, 'held': [],
                'observation_error': 'capture_failed', 'error': 'capture_failed',
            }
            with patch('plugins.emuera.session.recipe.source_digests', return_value={}), \
                    patch('plugins.emuera.session.recipe.artifact_digests', return_value={}), \
                    patch('gameqakit.duration_session._pid_path', return_value=None):
                cleanup = session.stop()
            self.assertTrue(cleanup['release_confirmed'])
            self.assertEqual(cleanup['state'], 'stopped')
            session.bridge.pause.assert_not_called()
            self.assertEqual(read_json(session.directory / 'stop.json')['session_id'], session.session)


@unittest.skipUnless(os.name == 'nt' and COMPONENTS, 'Requires registered Emuera runtime')
class EmueraManagedTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='gameqa-emuera-managed-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / 'sample'
        self.source.mkdir()
        self.script = self.source / 'gameqa-emuera-fixture.erb'
        original = Path(__file__).resolve().parents[1] / 'plugins/emuera/samples' / self.script.name
        shutil.copyfile(original, self.script)
        self.destination = self.root / 'session'
        self.bridge = self.destination / 'bridge'
        if evidence := os.environ.get('GAMEQAKIT_TEST_EVIDENCE'):
            self.addCleanup(shutil.copytree, self.root, Path(evidence) / self._testMethodName,
                            dirs_exist_ok=True, ignore=shutil.ignore_patterns('*.exe', 'tmp', 'appdata', 'localappdata'))

    def test_only_registered_script_and_runtime_are_admitted(self):
        plan = admission.make_plan(self.source, self.destination, artifacts=COMPONENTS)
        self.assertEqual(plan['backend'], 'emuera_authored')
        self.assertEqual(admission.revalidate_plan(plan), plan)
        components = self.root / 'components'
        components.mkdir()
        runtime = components / 'Emuera.exe'
        runtime.write_bytes((Path(COMPONENTS) / runtime.name).read_bytes() + b'changed')
        self.assertEqual(admission.inspect(self.source, artifacts=components)['launch_readiness'], 'blocked')
        self.script.write_bytes(self.script.read_bytes() + b'\n; changed\n')
        self.assertEqual(admission.inspect(self.source, artifacts=COMPONENTS)['launch_readiness'], 'blocked')
        self.assertFalse(self.destination.exists())

    def connect(self):
        exe = Path(COMPONENTS) / 'Emuera.exe'
        portable = os.environ.get('GAMEQAKIT_TEST_PORTABLE_MCP')
        return mcp(self.root, exe, command=[portable] if portable else None)

    def rpc(self, client, name, *, expect_error=False, **arguments):
        client.send('tools/call', {'name': name, 'arguments': arguments})
        response = client.responses.get(timeout=10)
        client.records[-1]['response'] = response
        self.assertEqual(response['id'], client.number)
        if expect_error:
            self.assertIn('error', response)
            return response['error']
        self.assertNotIn('error', response)
        return json.loads(response['result']['content'][0]['text'])

    def wait_state(self, client, expected):
        def poll():
            state = self.rpc(client, 'game_status', directory=str(self.destination))
            terminal = state['state'] in {'failed', 'cleanup_failed', 'stopped'}
            if terminal:
                self.assertEqual(state['state'], expected, state)
            return state if state['state'] == expected and (not terminal or not state['worker_alive']) else None
        return until(poll, seconds=30)

    def prepare(self, client):
        result = self.rpc(client, 'game_inspect', path=str(self.source), artifacts=COMPONENTS,
                          destination=str(self.destination))
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
        self.assertFalse((self.bridge / 'input.json').exists())
        self.assertEqual(admission.file_digest(state['screenshot']), state['screenshot_sha256'])
        self.rpc(client, 'game_bind', directory=str(self.bridge), session_id=state['session_id'], confirm=True)
        self.rpc(client, 'control_resume', directory=str(self.bridge), command_id='resume', confirm=True)
        return state

    def assert_reaped(self, state, client):
        ended = read_json(self.bridge / 'termination.json')
        pids = [state['pid'], state['worker_pid'], client.child.pid, *ended['capture_worker_pids']]
        self.assertTrue(ended['capture_worker_pids'])
        self.assertFalse(any(_pid_path(pid) for pid in pids))

    def test_prepared_runtime_change_blocks_start(self):
        with self.connect() as client:
            plan = self.prepare(client)
            launcher = self.destination / 'runtime/GameQaEmuera.exe'
            with launcher.open('ab') as stream:
                stream.write(b'changed')
            self.start(client, plan)
            failed = self.wait_state(client, 'failed')
            self.assertIn('runtime_changed', failed['error'])
            self.assertFalse(any(self.bridge.iterdir()))

    def test_confirm_draws_new_phase_wait_sends_no_input_and_stop_releases(self):
        with self.connect() as client:
            ready = self.ready(client)
            result = self.rpc(client, 'control_action', directory=str(self.bridge), action='confirm', duration_ms=70,
                              command_id='enter', confirm=True)
            self.assertTrue(result['press_consumed'] and result['release_consumed'])
            with Image.open(result['screenshot']) as after, Image.open(ready['screenshot']) as before:
                pixels = after.convert('RGB')
                self.assertIsNotNone(ImageChops.difference(before.convert('RGB'), pixels).getbbox())
                self.assertGreater(sum(1 for r, g, b in pixels.getdata() if 30 <= r <= 95 and g > 160 and 70 <= b <= 170), 100)
            self.assertEqual(admission.file_digest(result['screenshot']), result['screenshot_sha256'])
            waited = self.rpc(client, 'control_action', directory=str(self.bridge), action='wait', duration_ms=50,
                              command_id='wait', confirm=True)
            self.assertFalse(waited['press_consumed'] or waited['release_consumed'])
            self.assertEqual(waited['screenshot_sha256'], result['screenshot_sha256'])
            self.rpc(client, 'game_stop', directory=str(self.destination), request_id='stop', confirm=True)
            stopped = self.wait_state(client, 'stopped')
            self.assertTrue(stopped['release_confirmed'] and stopped['target_exited'])
            self.assertTrue(stopped['source_unchanged'] and stopped['runtime_source_unchanged'])
            self.assertEqual(stopped['termination_reason'], 'stop_requested')
        self.assert_reaped(ready, client)

    def test_capture_failure_preserves_consumption_and_stops(self):
        with self.connect() as client:
            ready = self.ready(client)
            (self.bridge / 'screenshots/failed-capture.png').mkdir()
            error = self.rpc(client, 'control_action', directory=str(self.bridge), action='confirm', duration_ms=70,
                             command_id='failed-capture', confirm=True, expect_error=True)
            self.assertIn('取图失败', error['message'])
            receipt = read_json(self.bridge / 'results/failed-capture.json')
            self.assertEqual((receipt['ok'], receipt['press_consumed'], receipt['release_consumed']), (False, True, True))
            failed = self.wait_state(client, 'failed')
            self.assertIn('observation_failed', failed['error'])
            self.assertTrue(failed['target_exited'] and failed['release_confirmed'])
            self.rpc(client, 'control_resume', directory=str(self.bridge), command_id='blocked', confirm=True, expect_error=True)
            self.assertEqual(read_json(self.bridge / 'request.json')['id'], 'failed-capture')
        self.assert_reaped(ready, client)
