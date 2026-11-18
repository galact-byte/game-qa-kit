import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from gameqakit import admission
from PIL import Image
from gameqakit.sessions import read_json
from tests.native_input_mcp import mcp, until
from tests.test_godot_bridge import RUNTIME, fixture_pack


class GodotAdmissionTests(unittest.TestCase):
    def test_only_registered_sample_and_runtime_are_ready(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, artifacts = root / "sample", root / "components"
            source.mkdir()
            artifacts.mkdir()
            pack = source / "fixture.pck"
            fixture_pack(pack)
            runtime = artifacts / "godot-runtime.exe"
            runtime.write_bytes(b"test double; never executed")
            destination = root / "session"
            result = admission.inspect(source, artifacts=artifacts)
            self.assertEqual(result["engine"], "godot4")
            self.assertEqual(result["launch_readiness"], "blocked")
            with patch("plugins.godot.prepare.RUNTIME_SHA256", admission.file_digest(runtime)):
                plan = admission.make_plan(source, destination, artifacts=artifacts)
                self.assertEqual(plan["backend"], "godot_authored")
                self.assertEqual(admission.revalidate_plan(plan), plan)
                self.assertFalse(destination.exists())
                extra = source / "unregistered.dll"
                extra.touch()
                self.assertEqual(admission.inspect(source, artifacts=artifacts)["launch_readiness"], "blocked")
                extra.unlink()
                body = pack.read_bytes()
                pack.write_bytes(body[:-1] + bytes([body[-1] ^ 1]))
                rejected = admission.inspect(source, artifacts=artifacts)
                self.assertEqual(rejected["engine"], "godot4")
                self.assertEqual(rejected["launch_readiness"], "blocked")
                self.assertFalse(destination.exists())


@unittest.skipUnless(os.name == 'nt' and RUNTIME, '需 Windows 与固定 Godot 4.5.1 运行时')
class GodotManagedMcpTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='gameqa-godot-managed-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        if evidence := os.environ.get('GAMEQAKIT_TEST_EVIDENCE'):
            self.addCleanup(shutil.copytree, self.root, Path(evidence) / self._testMethodName,
                            dirs_exist_ok=True, ignore=shutil.ignore_patterns('*.exe', 'appdata', 'localappdata', 'tmp'))
        self.source = self.root / 'sample'
        self.source.mkdir()
        fixture_pack(self.source / 'fixture.pck')
        self.runtime = Path(RUNTIME).resolve()
        if self.runtime.name != 'godot-runtime.exe':
            components = self.root / 'components'
            components.mkdir()
            self.runtime = Path(shutil.copyfile(self.runtime, components / 'godot-runtime.exe'))
        self.session = self.root / 'session'

    def rpc(self, client, name, *, expect_error=False, **arguments):
        client.send('tools/call', {'name': name, 'arguments': arguments})
        response = client.responses.get(timeout=8)
        self.assertEqual(response['id'], client.number)
        client.records[-1]['response'] = response
        if expect_error:
            self.assertIn('error', response)
            return response['error']['message']
        self.assertNotIn('error', response)
        return json.loads(response['result']['content'][0]['text'])

    def await_state(self, client, expected):
        def poll():
            state = self.rpc(client, 'game_status', directory=str(self.session))
            terminal = state['state'] in {'stopped', 'failed', 'cleanup_failed'}
            if state['state'] == expected and (not terminal or not state['worker_alive']):
                return state
            if terminal:
                self.assertEqual(state['state'], expected, state)
        return until(poll, seconds=20)

    def prepare(self, client):
        inspected = self.rpc(client, 'game_inspect', path=str(self.source), artifacts=str(self.runtime.parent),
                             destination=str(self.session))
        self.assertEqual(inspected['launch_readiness'], 'ready', inspected)
        plan = inspected['plan']
        self.rpc(client, 'game_prepare', plan=plan, request_id='prepare', confirm=True)
        self.await_state(client, 'prepared')
        return plan

    def start(self, client, plan):
        self.rpc(client, 'game_start', directory=str(self.session), fingerprint=plan['fingerprint'],
                 request_id='start', confirm=True)

    def ready(self, client):
        self.start(client, self.prepare(client))
        state = self.await_state(client, 'ready_paused')
        self.assertTrue(state['paused'])
        self.assertEqual(state['held'], [])
        self.assertEqual(admission.file_digest(state['screenshot']), state['screenshot_sha256'])
        self.assertFalse((self.session / 'bridge/requests').exists())
        self.assertEqual(read_json(self.session / 'fixture-state.json')['confirms'], 0)
        self.rpc(client, 'game_bind', directory=state['bridge_directory'], session_id=state['session_id'], confirm=True)
        self.rpc(client, 'control_resume', command_id='resume', confirm=True)
        return state

    def assert_reaped(self, state, *pids):
        from gameqakit.win32 import _pid_path
        self.assertTrue(state['target_exited'])
        for pid in pids:
            self.assertFalse(_pid_path(pid), f'PID {pid} 仍在运行')

    def test_admitted_scene_moves_and_stops_released(self):
        with mcp(self.root, self.runtime) as client:
            ready = self.ready(client)
            result = self.rpc(client, 'control_action', action='move', direction='right', frames=10,
                              command_id='right', confirm=True)
            self.assertTrue(result['consumed'])
            actual = read_json(self.session / 'fixture-state.json')
            self.assertEqual(actual['x'], 100)
            self.assertFalse(actual['right_pressed'])
            for path, x in ((ready['screenshot'], 60), (result['screenshot'], 100)):
                with Image.open(path) as image:
                    self.assertEqual(image.getpixel((x, 105))[:3], (255, 128, 32))
                    self.assertNotEqual(image.getpixel((x - 1, 105))[:3], (255, 128, 32))
            self.rpc(client, 'game_stop', directory=str(self.session), request_id='stop', confirm=True)
            stopped = self.await_state(client, 'stopped')
            self.assertTrue(stopped['release_confirmed'])
            self.assertTrue(stopped['source_unchanged'] and stopped['runtime_source_unchanged'])
        self.assert_reaped(stopped, ready['worker_pid'], ready['pid'], client.child.pid)

    def test_black_frame_preserves_consumption_and_stops_batch(self):
        with mcp(self.root, self.runtime) as client:
            ready = self.ready(client)
            (self.session / 'fixture-state.json.blank').touch()
            error = self.rpc(client, 'control_action', action='confirm', frames=2, command_id='black',
                             confirm=True, expect_error=True)
            self.assertIn('blank_capture', error)
            receipt = read_json(self.session / 'bridge/results/black.json')
            self.assertTrue(receipt['consumed'])
            self.assertFalse(receipt['ok'])
            self.assertEqual(receipt['error'], 'blank_capture')
            self.assertFalse((self.session / 'bridge/screenshots/black.png').exists())
            failed = self.await_state(client, 'failed')
            self.assertIn('observation_failed：blank_capture', failed['error'])
            for name in ('control_resume', 'control_action'):
                args = {'action': 'confirm'} if name == 'control_action' else {}
                self.rpc(client, name, command_id='blocked', confirm=True, expect_error=True, **args)
            self.assertEqual({p.stem for p in (self.session / 'bridge/requests').glob('*.json')}, {'resume', 'black'})
        self.assert_reaped(failed, ready['worker_pid'], ready['pid'], client.child.pid)

    def test_changed_prepared_pack_never_launches(self):
        with mcp(self.root, self.runtime) as client:
            plan = self.prepare(client)
            with (self.session / 'runtime/fixture.pck').open('ab') as pack:
                pack.write(b'!')
            self.start(client, plan)
            failed = self.await_state(client, 'failed')
            self.assertFalse((self.session / 'bridge/connection.json').exists())
            self.assertNotIn('pid', failed)
        self.assert_reaped(failed, failed['worker_pid'], client.child.pid)
