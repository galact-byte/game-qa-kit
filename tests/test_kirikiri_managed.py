import os
from pathlib import Path
import shutil
import tempfile
import unittest

from PIL import Image

from gameqakit import admission
from gameqakit.sessions import read_json
from plugins.kirikiri.host import FIXTURE, RUNTIME_SHA256
from tests import test_emuera_managed as common
from tests.native_input_mcp import mcp
from tests.test_kirikiri_bridge import COMPONENT


@unittest.skipUnless(os.name == 'nt' and COMPONENT, 'Requires registered KiriKiri runtime')
class KiriKiriManagedTests(common.EmueraManagedTests):
    __unittest_skip__ = not (os.name == 'nt' and COMPONENT)

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='gameqa-kirikiri-managed-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.components = self.root / 'components'
        self.components.mkdir()
        self.exe = self.components / 'runtime.exe'
        shutil.copyfile(COMPONENT, self.exe)
        self.assertEqual(admission.file_digest(self.exe), RUNTIME_SHA256)
        self.source = self.root / 'sample'
        self.source.mkdir()
        self.script = self.source / 'gameqa-kirikiri-fixture.tjs'
        shutil.copyfile(FIXTURE, self.script)
        self.destination = self.root / 'session'
        self.bridge = self.destination / 'bridge'
        if evidence := os.environ.get('GAMEQAKIT_TEST_EVIDENCE'):
            self.addCleanup(shutil.copytree, self.root, Path(evidence) / self._testMethodName,
                            dirs_exist_ok=True, ignore=shutil.ignore_patterns('*.exe', 'tmp', 'appdata', 'localappdata'))

    def connect(self):
        portable = os.environ.get('GAMEQAKIT_TEST_PORTABLE_MCP')
        return mcp(self.root, self.exe, command=[portable] if portable else None)

    def prepare(self, client):
        result = self.rpc(client, 'game_inspect', path=str(self.source), artifacts=str(self.components),
                          destination=str(self.destination))
        plan = result['plan']
        self.rpc(client, 'game_prepare', plan=plan, request_id='prepare', confirm=True)
        self.wait_state(client, 'prepared')
        return plan

    def test_only_registered_script_and_runtime_are_admitted(self):
        args = {'artifacts': self.components}
        plan = admission.make_plan(self.source, self.destination, **args)
        self.assertEqual(plan['backend'], 'kirikiri_authored')
        self.assertEqual(admission.revalidate_plan(plan), plan)
        extra = self.source / 'data.xp3'
        extra.touch()
        self.assertEqual(admission.inspect(self.source, **args)['launch_readiness'], 'blocked')
        extra.unlink()
        with self.exe.open('ab') as stream:
            stream.write(b'changed')
        self.assertEqual(admission.inspect(self.source, **args)['launch_readiness'], 'blocked')
        shutil.copyfile(COMPONENT, self.exe)
        with self.script.open('ab') as stream:
            stream.write(b'// changed')
        self.assertEqual(admission.inspect(self.source, **args)['launch_readiness'], 'blocked')
        self.assertFalse(self.destination.exists())

    def test_prepared_runtime_change_blocks_start(self):
        with self.connect() as client:
            plan = self.prepare(client)
            (self.destination / 'runtime/test-mode.tjs').write_text('blank=black\n', encoding='ascii')
            self.start(client, plan)
            failed = self.wait_state(client, 'failed')
            self.assertIn('runtime_changed', failed['error'])
            self.assertFalse(any(self.bridge.iterdir()))

    def test_confirm_draws_new_phase_wait_sends_no_input_and_stop_releases(self):
        with self.connect() as client:
            ready = self.ready(client)
            self.assertFalse((self.destination / 'runtime/savedata/slot01.tjs').exists())
            with Image.open(ready['screenshot']) as before:
                self.assertEqual(before.convert('RGB').getpixel((0, 0)), (108, 52, 131))
            result = self.rpc(client, 'control_action', directory=str(self.bridge), action='confirm', duration_ms=120,
                              command_id='enter', confirm=True)
            self.assertTrue(result['press_consumed'] and result['release_consumed'])
            with Image.open(result['screenshot']) as after:
                self.assertEqual(after.convert('RGB').getpixel((0, 0)), (46, 204, 113))
            self.assertEqual(admission.file_digest(result['screenshot']), result['screenshot_sha256'])
            self.assertTrue((self.destination / 'runtime/savedata/slot01.tjs').is_file())
            observer = read_json(self.bridge / 'observer.json')
            self.assertEqual([event['handler_query'] for event in observer['events']], [1, 0])
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
