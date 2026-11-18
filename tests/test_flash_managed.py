import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest

from PIL import Image

try:
    import psutil
except ImportError:
    psutil = None

from gameqakit import admission
from gameqakit.sessions import write_json
from gameqakit.win32 import _pid_path
from tests import test_emuera_managed as common
from tests.native_input_mcp import mcp
from tests.native_input_smoke import until

EDGE = os.environ.get('GAMEQAKIT_TEST_FLASH_EDGE')
FIXTURE = Path(__file__).resolve().parents[1] / 'plugins/flash/samples/gameqa-flash-fixture.swf'


@unittest.skipUnless(os.name == 'nt' and EDGE, 'Requires the registered Edge build')
class FlashManagedTests(unittest.TestCase):
    rpc = common.EmueraManagedTests.rpc
    wait_state = common.EmueraManagedTests.wait_state
    start = common.EmueraManagedTests.start
    ready = common.EmueraManagedTests.ready

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='gameqa-flash-managed-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / 'sample'
        self.source.mkdir()
        self.script = self.source / FIXTURE.name
        shutil.copyfile(FIXTURE, self.script)
        self.destination = self.root / 'session'
        self.bridge = self.destination / 'bridge'
        if evidence := os.environ.get('GAMEQAKIT_TEST_EVIDENCE'):
            self.addCleanup(shutil.copytree, self.root, Path(evidence) / self._testMethodName,
                            dirs_exist_ok=True, ignore=shutil.ignore_patterns('browser', 'ruffle'))

    def connect(self, root=None):
        portable = os.environ.get('GAMEQAKIT_TEST_PORTABLE_MCP')
        return mcp(root or self.root, Path(EDGE), command=[portable] if portable else None)

    def prepare(self, client):
        result = self.rpc(client, 'game_inspect', path=str(self.source), destination=str(self.destination))
        self.assertEqual(result['launch_readiness'], 'ready', result)
        plan = result['plan']
        self.assertEqual(plan['backend'], 'flash_authored')
        self.rpc(client, 'game_prepare', plan=plan, request_id='prepare', confirm=True)
        self.wait_state(client, 'prepared')
        return plan

    def assert_reaped(self, ready, client):
        self.assertFalse(any(_pid_path(pid) for pid in (ready['pid'], ready['worker_pid'], client.child.pid)))

    def test_unregistered_swf_is_refused(self):
        self.assertEqual(admission.inspect(self.source)['engine'], 'flash_authored_candidate')
        with self.script.open('ab') as stream:
            stream.write(b'extra')
        report = admission.inspect(self.source)
        self.assertEqual(report['launch_readiness'], 'blocked')
        self.assertTrue(any(item['field'] == 'source' for item in report['blockers']))

    def test_prepared_sample_tamper_refuses_start(self):
        with self.connect() as client:
            plan = self.prepare(client)
            with (self.destination / 'runtime' / self.script.name).open('ab') as stream:
                stream.write(b'extra')
            self.start(client, plan)
            self.assertIn('runtime_changed', self.wait_state(client, 'failed')['error'])
            self.assertFalse((self.bridge / 'connection.json').exists())

    def test_right_input_observed_and_released_then_stop(self):
        with self.connect() as client:
            ready = self.ready(client)
            with Image.open(ready['screenshot']) as shot:
                self.assertEqual(shot.convert('RGB').getpixel((48, 110)), (80, 240, 100))
            action = self.rpc(client, 'control_action', directory=str(self.bridge), action='move', direction='right',
                              duration_ms=140, command_id='right', confirm=True)
            self.assertTrue(action['press_consumed'] and action['release_consumed'], action)
            self.assertEqual(action['held'], [])
            with Image.open(self.bridge / action['pressed_screenshot']) as shot:
                self.assertEqual(shot.convert('RGB').getpixel((136, 110)), (80, 240, 100))
            with Image.open(action['screenshot']) as shot:
                self.assertEqual(shot.convert('RGB').getpixel((48, 110)), (80, 240, 100))
            request = (self.bridge / 'request.json').read_bytes()
            replay = self.rpc(client, 'control_action', directory=str(self.bridge), action='move', direction='right',
                              duration_ms=140, command_id='right', confirm=True)
            self.assertEqual(replay['screenshot_sha256'], action['screenshot_sha256'])
            self.rpc(client, 'control_action', directory=str(self.bridge), action='move', direction='left',
                     duration_ms=140, command_id='left', confirm=True, expect_error=True)
            self.assertEqual((self.bridge / 'request.json').read_bytes(), request)
            waited = self.rpc(client, 'control_action', directory=str(self.bridge), action='wait',
                              duration_ms=55, command_id='wait', confirm=True)
            self.assertFalse(waited['press_consumed'] or waited['release_consumed'])
            self.rpc(client, 'game_stop', directory=str(self.destination), request_id='stop', confirm=True)
            ended = self.wait_state(client, 'stopped')
            self.assertTrue(ended['target_exited'] and ended['release_confirmed'], ended)
            self.assertTrue(ended['source_unchanged'] and ended['runtime_source_unchanged'])
        self.assert_reaped(ready, client)

    def test_expired_command_sends_no_input(self):
        with self.connect() as client:
            ready = self.ready(client)
            request = json.loads((self.bridge / 'request.json').read_bytes())
            request.update(id='expired', created_at=time.time() - 30,
                           payload=dict(action='move', direction='right', duration_ms=200))
            write_json(self.bridge / 'request.json', request)
            def terminal():
                state = self.rpc(client, 'game_status', directory=str(self.destination))
                return state if not state['worker_alive'] else None

            ended = until(terminal, seconds=10)
            self.assertIn(ended['state'], ('failed', 'cleanup_failed'), ended)
            self.assertTrue(ended['target_exited'], ended)
            term = json.loads((self.bridge / 'termination.json').read_bytes())
            self.assertIsNone(term['pending_command'], term)
            self.assertFalse(term['press_consumed'], term)
            self.assertFalse((self.bridge / 'results/expired.json').exists())
            for path in (self.bridge / 'screenshots').glob('*.png'):
                with Image.open(path) as shot:
                    self.assertEqual(shot.convert('RGB').getpixel((48, 110)), (80, 240, 100))
        self.assert_reaped(ready, client)

    def test_concurrent_sessions_keep_input_and_jobs_separate(self):
        other = self.root / 'second'
        other.mkdir()
        destination = other / 'session'
        bridge = destination / 'bridge'
        with self.connect() as first, self.connect(other) as second:
            ready = self.ready(first)
            paused = self.rpc(second, 'game_inspect', path=str(self.source), destination=str(destination))['plan']
            self.rpc(second, 'game_prepare', plan=paused, request_id='prepare-second', confirm=True)

            def status():
                state = self.rpc(second, 'game_status', directory=str(destination))
                self.assertNotIn(state['state'], ('failed', 'cleanup_failed'), state)
                return state

            until(lambda: status()['state'] == 'prepared', seconds=10)
            self.rpc(second, 'game_start', directory=str(destination), fingerprint=paused['fingerprint'],
                     request_id='start-second', confirm=True)

            def second_ready():
                state = status()
                return state if state['state'] == 'ready_paused' else None

            second_state = until(second_ready, seconds=16)
            self.assertNotEqual(ready['pid'], second_state['pid'])
            self.rpc(first, 'control_resume', directory=str(self.bridge), command_id='resume-first', confirm=True)
            action = self.rpc(first, 'control_action', directory=str(self.bridge), action='move', direction='right',
                              duration_ms=140, command_id='first-only', confirm=True)
            with Image.open(self.bridge / action['pressed_screenshot']) as shot:
                self.assertEqual(shot.convert('RGB').getpixel((136, 110)), (80, 240, 100))
            self.rpc(first, 'game_stop', directory=str(self.destination), request_id='stop-first', confirm=True)
            ended = self.wait_state(first, 'stopped')
            self.assertTrue(ended['target_exited'])
            self.assertEqual(status()['state'], 'ready_paused')
            self.assertFalse((bridge / 'request.json').exists())
            self.rpc(second, 'game_stop', directory=str(destination), request_id='stop-second', confirm=True)
            until(lambda: not status()['worker_alive'], seconds=10)
            for path in (bridge / 'screenshots').glob('*.png'):
                with Image.open(path) as shot:
                    self.assertEqual(shot.convert('RGB').getpixel((48, 110)), (80, 240, 100))
        self.assert_reaped(ready, first)
        self.assert_reaped(second_state, second)

    @unittest.skipIf(psutil is None, 'Requires psutil to suspend only the owned renderer')
    def test_browser_exit_after_press_reports_unconfirmed_release(self):
        with self.connect() as client:
            ready = self.ready(client)
            browser = psutil.Process(ready['pid'])
            children = browser.children(recursive=True)
            self.send_move(client, 'browser-exit')
            until(self.moved, seconds=3)
            browser.kill()
            self.await_failed_move(client)
            ended = self.wait_state(client, 'cleanup_failed')
            self.assertTrue(ended['target_exited'], ended)
            self.assertFalse(ended['release_confirmed'], ended)
            stopped = json.loads((self.bridge / 'termination.json').read_bytes())
            self.assertTrue(stopped['press_consumed'], stopped)
            self.assertFalse(stopped['release_consumed'], stopped)
            self.assertFalse(any(child.is_running() for child in children))
        self.assert_reaped(ready, client)

    def send_move(self, client, command_id):
        client.send('tools/call', {'name': 'control_action', 'arguments': {
            'directory': str(self.bridge), 'action': 'move', 'direction': 'right',
            'duration_ms': 1000, 'command_id': command_id, 'confirm': True}})

    def moved(self):
        state = json.loads((self.bridge / 'status.json').read_bytes())
        if state['held'] != ['right']:
            return False
        path = self.bridge / state['observation_screenshot']
        with Image.open(path) as shot:
            return shot.convert('RGB').getpixel((136, 110)) == (80, 240, 100)

    def await_failed_move(self, client):
        response = client.responses.get(timeout=6)
        client.records[-1]['response'] = response
        self.assertIn('error', response)

    @unittest.skipIf(psutil is None, 'Requires psutil to suspend only the owned renderer')
    def test_stalled_renderer_after_press_reaps_job_without_claiming_release(self):
        with self.connect() as client:
            ready = self.ready(client)
            children = psutil.Process(ready['pid']).children(recursive=True)
            renderers = [child for child in children if '--type=renderer' in child.cmdline()]
            self.assertTrue(renderers)
            self.send_move(client, 'stall')
            until(self.moved, seconds=3)
            fault_started = time.monotonic()
            for renderer in renderers:
                renderer.suspend()
            self.await_failed_move(client)
            ended = self.wait_state(client, 'cleanup_failed')
            self.assertTrue(ended['target_exited'], ended)
            self.assertFalse(ended['release_confirmed'], ended)
            stopped = json.loads((self.bridge / 'termination.json').read_bytes())
            self.assertTrue(stopped['press_consumed'], stopped)
            self.assertFalse(stopped['release_consumed'], stopped)
            self.assertIn(stopped['reason'], ('capture_timeout', 'release_timeout'))
            self.assertLess(time.monotonic() - fault_started, 5)
            self.assertFalse(any(child.is_running() for child in children))
        self.assert_reaped(ready, client)
