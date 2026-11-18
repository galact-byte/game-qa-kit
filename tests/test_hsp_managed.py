import os
from pathlib import Path
import shutil
import tempfile
import unittest

from PIL import Image

from gameqakit import admission
from gameqakit.sessions import read_json
from gameqakit.win32 import _pid_path
from plugins.hsp.prepare_authored import SCRIPT
from tests import test_emuera_managed as common
from tests.native_input_mcp import mcp
from tests.native_input_smoke import until


COMPONENTS = os.environ.get('GAMEQAKIT_TEST_HSP')
FIXTURE = Path(__file__).resolve().parents[1] / 'plugins/hsp/samples' / SCRIPT


@unittest.skipUnless(os.name == 'nt' and COMPONENTS, 'Requires registered HSP components')
class HspManagedTests(unittest.TestCase):
    rpc = common.EmueraManagedTests.rpc
    wait_state = common.EmueraManagedTests.wait_state
    start = common.EmueraManagedTests.start

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='gameqa-hsp-managed-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.components = self.root / 'components'
        self.components.mkdir()
        self.executable = self.components / 'hsp3.exe'
        shutil.copyfile(Path(COMPONENTS) / self.executable.name, self.executable)
        shutil.copyfile(Path(COMPONENTS) / 'GameQaHsp.dll', self.components / 'GameQaHsp.dll')
        self.source = self.root / 'sample'
        self.source.mkdir()
        self.script = self.source / SCRIPT
        shutil.copyfile(FIXTURE, self.script)
        self.destination = self.root / 'session'
        self.bridge = self.destination / 'bridge'
        if evidence := os.environ.get('GAMEQAKIT_TEST_EVIDENCE'):
            self.addCleanup(shutil.copytree, self.root, Path(evidence) / self._testMethodName,
                            dirs_exist_ok=True, ignore=shutil.ignore_patterns('*.exe', '*.dll', 'tmp'))

    def connect(self):
        portable = os.environ.get('GAMEQAKIT_TEST_PORTABLE_MCP')
        return mcp(self.root, self.executable, command=[portable] if portable else None)

    def prepare(self, client):
        result = self.rpc(client, 'game_inspect', path=str(self.source), artifacts=str(self.components),
                          destination=str(self.destination))
        self.assertEqual(result['launch_readiness'], 'ready', result)
        plan = result['plan']
        self.rpc(client, 'game_prepare', plan=plan, request_id='prepare', confirm=True)
        self.wait_state(client, 'prepared')
        return plan

    def ready(self, client):
        self.start(client, self.prepare(client))
        state = self.wait_state(client, 'ready_paused')
        self.assertEqual((state['paused'], state['held']), (True, []))
        self.assertFalse((self.bridge / 'request.ini').exists())
        self.assertEqual(admission.file_digest(state['screenshot']), state['screenshot_sha256'])
        self.rpc(client, 'game_bind', directory=str(self.bridge), session_id=state['session_id'], confirm=True)
        return state

    def test_only_registered_bytecode_and_runtime_are_admitted(self):
        args = {'artifacts': self.components}
        plan = admission.make_plan(self.source, self.destination, **args)
        self.assertEqual(plan['backend'], 'hsp_authored')
        self.assertEqual(admission.revalidate_plan(plan), plan)
        extra = self.source / 'save.dat'
        extra.touch()
        self.assertEqual(admission.inspect(self.source, **args)['launch_readiness'], 'blocked')
        extra.unlink()
        with self.executable.open('ab') as stream:
            stream.write(b'changed')
        self.assertEqual(admission.inspect(self.source, **args)['launch_readiness'], 'blocked')
        shutil.copyfile(Path(COMPONENTS) / self.executable.name, self.executable)
        with self.script.open('ab') as stream:
            stream.write(b'changed')
        self.assertEqual(admission.inspect(self.source, **args)['launch_readiness'], 'blocked')

    def test_prepared_runtime_change_blocks_start(self):
        with self.connect() as client:
            plan = self.prepare(client)
            with (self.destination / 'runtime/gameqa-hsp-fixture.ax').open('ab') as stream:
                stream.write(b'changed')
            self.start(client, plan)
            failed = self.wait_state(client, 'failed')
            self.assertIn('runtime_changed', failed['error'])
            self.assertFalse((self.bridge / 'connection.ini').exists())

    def test_first_image_then_resume_move_and_stop(self):
        with self.connect() as client:
            ready = self.ready(client)
            with Image.open(ready['screenshot']) as before:
                self.assertEqual(before.convert('RGB').getpixel((41, 241)), (0, 100, 200))
            until(lambda: (self.bridge / 'screenshots/observation.png').is_file(), seconds=3)
            def paused_preview():
                state = self.rpc(client, 'control_status', directory=str(self.bridge))
                return state if state.get('paused') and state.get('observation_seq', 0) > 1 else None
            until(paused_preview, seconds=3)
            self.assertFalse((self.bridge / 'request.ini').exists())
            self.rpc(client, 'control_resume', directory=str(self.bridge), command_id='resume', confirm=True)
            moved = self.rpc(client, 'control_action', directory=str(self.bridge), action='move', direction='right',
                             frames=6, command_id='right', confirm=True)
            self.assertTrue(moved['consumed'])
            with Image.open(moved['screenshot']) as after:
                self.assertEqual(after.convert('RGB').getpixel((41, 241)), (235, 235, 235))
                self.assertEqual(after.convert('RGB').getpixel((53, 241)), (0, 100, 200))
            self.assertEqual(read_json(self.destination / 'runtime/observed.json')['x'], 52)
            for action, key in (('confirm', 1), ('cancel', 0)):
                result = self.rpc(client, 'control_action', directory=str(self.bridge), action=action,
                                  command_id=action, confirm=True)
                self.assertTrue(result['consumed'])
                self.assertEqual(read_json(self.destination / 'runtime/observed.json')['menu'], key)
            self.rpc(client, 'game_stop', directory=str(self.destination), request_id='stop', confirm=True)
            stopped = self.wait_state(client, 'stopped')
            self.assertTrue(stopped['release_confirmed'] and stopped['target_exited'])
            self.assertTrue(stopped['source_unchanged'] and stopped['artifact_unchanged'])
            self.assertFalse(_pid_path(ready['pid']))

    def test_live_observation_failure_reaps_owned_process(self):
        with self.connect() as client:
            ready = self.ready(client)
            until(lambda: (self.bridge / 'screenshots/observation.png').is_file(), seconds=3)
            (self.bridge / 'screenshots/observation.png.part').mkdir()
            failed = self.wait_state(client, 'failed')
            self.assertIn('capture_failed', failed['error'])
            self.assertTrue(failed['release_confirmed'] and failed['target_exited'])
            self.assertFalse(_pid_path(ready['pid']))
