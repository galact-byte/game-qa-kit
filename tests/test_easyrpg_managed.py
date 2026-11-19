"""Real MCP coverage for the registered source-built EasyRPG fixture."""
import ctypes
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest

from PIL import Image
import psutil

from gameqakit.admission import file_digest
from gameqakit.sessions import read_json, write_json
from gameqakit.win32 import _pid_path
from tests.native_input_mcp import mcp, until
from tests.test_easyrpg_admission import COMPONENTS, ROOT


def character_x(path):
    with Image.open(path) as image:
        pixels = image.convert('RGB')
        points = [index % image.width for index, (r, g, b) in enumerate(pixels.getdata())
                  if r > 140 and b > 80 and r > g*2 and b > g*2]
    if len(points) < 30:
        raise AssertionError('Visible authored character not found')
    return min(points)


@unittest.skipUnless(os.name == 'nt' and COMPONENTS, 'Requires registered source-built EasyRPG runtime')
class EasyRpgManagedTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='gameqa-easyrpg-managed-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / 'sample'
        shutil.copytree(ROOT / 'plugins/easyrpg/samples/rm2k',self.source)
        self.destination = self.root / 'session'
        self.bridge = self.destination / 'bridge'
        if evidence := os.environ.get('GAMEQAKIT_TEST_EVIDENCE'):
            self.addCleanup(shutil.copytree,self.root,Path(evidence)/self._testMethodName,
                            dirs_exist_ok=True,ignore=shutil.ignore_patterns('*.exe','tmp'))

    def connect(self, directory=None):
        portable = os.environ.get('GAMEQAKIT_TEST_PORTABLE_MCP')
        return mcp(directory or self.root,Path(COMPONENTS)/'GameQaEasyRpg.exe',command=[portable] if portable else None)

    def rpc(self,client,name,*,expect_error=False,**arguments):
        client.send('tools/call',{'name':name,'arguments':arguments})
        response = client.responses.get(timeout=20)
        client.records[-1]['response']=response
        self.assertEqual(response['id'],client.number)
        if expect_error:
            self.assertIn('error',response)
            return response['error']
        self.assertNotIn('error',response)
        return json.loads(response['result']['content'][0]['text'])

    def wait_state(self,client,expected):
        def poll():
            state=self.rpc(client,'game_status',directory=str(self.destination))
            terminal=state['state'] in {'failed','cleanup_failed','stopped'}
            if terminal:self.assertEqual(state['state'],expected,state)
            return state if state['state']==expected and (not terminal or not state['worker_alive']) else None
        return until(poll,seconds=30)

    def prepare(self,client):
        plan=self.rpc(client,'game_inspect',path=str(self.source),artifacts=COMPONENTS,
                      destination=str(self.destination))['plan']
        self.rpc(client,'game_prepare',plan=plan,request_id='prepare',confirm=True)
        self.wait_state(client,'prepared')
        return plan

    def start(self,client,plan):
        self.rpc(client,'game_start',directory=str(self.destination),fingerprint=plan['fingerprint'],
                 request_id='start',confirm=True)

    def ready(self,client):
        self.start(client,self.prepare(client))
        state=self.wait_state(client,'ready_paused')
        self.assertEqual((state['paused'],state['held']),(True,[]))
        self.assertFalse((self.bridge/'request.json').exists())
        self.assertEqual(file_digest(state['screenshot']),state['screenshot_sha256'])
        character_x(state['screenshot'])
        self.rpc(client,'game_bind',directory=str(self.bridge),session_id=state['session_id'],confirm=True)
        self.rpc(client,'control_resume',directory=str(self.bridge),command_id='resume',confirm=True)
        return state

    def move(self,client,ident='right',duration=180):
        return self.rpc(client,'control_action',directory=str(self.bridge),action='move',direction='right',
                        duration_ms=duration,command_id=ident,confirm=True)

    def stop(self,client):
        self.rpc(client,'game_stop',directory=str(self.destination),request_id='stop',confirm=True)
        stopped=self.wait_state(client,'stopped')
        self.assertTrue(stopped['release_confirmed'] and stopped['target_exited'],stopped)
        self.assertTrue(stopped['source_unchanged'] and stopped['runtime_source_unchanged'],stopped)
        return stopped

    def assert_reaped(self,ready,client):
        ended=read_json(self.bridge/'termination.json')
        pids=[ready['pid'],ready['worker_pid'],client.child.pid,*ended['capture_worker_pids']]
        self.assertTrue(ended['capture_worker_pids'])
        self.assertFalse(any(_pid_path(pid) for pid in pids))

    def test_move_has_target_consumption_then_release_and_wait_is_stable(self):
        u = ctypes.WinDLL('user32')
        u.GetForegroundWindow.restype = ctypes.c_void_p
        u.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        foreground = []
        finished = threading.Event()

        def observe_foreground():
            previous = None
            while not finished.is_set():
                hwnd = u.GetForegroundWindow()
                if hwnd != previous:
                    previous = hwnd
                    pid = ctypes.c_ulong()
                    u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                    owned = False
                    try:
                        process = psutil.Process(pid.value)
                        owned = process.pid == os.getpid() or any(
                            parent.pid == os.getpid() for parent in process.parents())
                    except (psutil.Error, OSError):
                        pass
                    foreground.append(dict(hwnd=hwnd, pid=pid.value,
                                           executable=str(_pid_path(pid.value)), owned=owned))
                finished.wait(.01)

        monitor = threading.Thread(target=observe_foreground, daemon=True)
        monitor.start()
        def finish_observation():
            finished.set()
            monitor.join(timeout=2)
            write_json(self.root/'foreground.json', foreground)
        self.addCleanup(finish_observation)
        with self.connect() as client:
            ready=self.ready(client)
            result=self.move(client)
            self.assertTrue(result['press_consumed'] and result['release_consumed'],result)
            self.assertGreater(result['target_release_frame'],result['target_press_frame'])
            self.assertEqual(result['held'],[])
            self.assertGreater(character_x(self.bridge/result['screenshot']),character_x(ready['screenshot'])+10)
            waited=self.rpc(client,'control_action',directory=str(self.bridge),action='wait',duration_ms=160,
                            command_id='settle',confirm=True)
            stable=self.rpc(client,'control_action',directory=str(self.bridge),action='wait',duration_ms=160,
                            command_id='stable',confirm=True)
            self.assertFalse(waited['press_consumed'] or waited['release_consumed'])
            self.assertEqual(character_x(self.bridge/waited['screenshot']),character_x(self.bridge/stable['screenshot']))
            repeated=self.move(client)
            self.assertEqual(repeated['target_ack_seq'],result['target_ack_seq'])
            self.stop(client)
        self.assert_reaped(ready,client)
        finish_observation()
        self.assertTrue(foreground)
        self.assertFalse(any(event['owned'] for event in foreground), foreground)

    def test_pause_requires_target_ack_then_resume_accepts_a_new_command(self):
        with self.connect() as client:
            ready = self.ready(client)
            self.move(client,'before-pause',100)
            self.rpc(client,'control_pause',directory=str(self.bridge))
            def paused():
                state = read_json(self.bridge/'status.json')
                return state if state.get('paused') and state.get('held') == [] else None
            state = until(paused,seconds=2)
            self.assertTrue(state['target_paused'])
            self.assertGreater(state['target_ack_seq'],0)
            self.rpc(client,'control_resume',directory=str(self.bridge),command_id='resume-again',confirm=True)
            result = self.move(client,'after-pause',100)
            self.assertTrue(result['press_consumed'] and result['release_consumed'])
            self.stop(client)
        self.assert_reaped(ready,client)

    def test_concurrent_sessions_isolate_input_and_process_cleanup(self):
        other = self.root/'second'
        other.mkdir()
        destination = other/'session'
        bridge = destination/'bridge'
        with self.connect() as first, self.connect(other) as second:
            ready = self.ready(first)
            self.rpc(first,'control_pause',directory=str(self.bridge))
            until(lambda:read_json(self.bridge/'status.json').get('paused'),seconds=2)
            plan = self.rpc(second,'game_inspect',path=str(self.source),artifacts=COMPONENTS,
                            destination=str(destination))['plan']
            self.rpc(second,'game_prepare',plan=plan,request_id='prepare-second',confirm=True)
            def state(expected):
                status = self.rpc(second,'game_status',directory=str(destination))
                self.assertNotIn(status['state'],('failed','cleanup_failed'),status)
                return status if status['state'] == expected else None
            until(lambda:state('prepared'),seconds=15)
            self.rpc(second,'game_start',directory=str(destination),fingerprint=plan['fingerprint'],
                     request_id='start-second',confirm=True)
            isolated = until(lambda:state('ready_paused'),seconds=20)
            self.assertNotEqual(isolated['pid'],ready['pid'])
            initial_x = character_x(isolated['screenshot'])
            self.rpc(first,'control_resume',directory=str(self.bridge),command_id='resume-first',confirm=True)
            self.move(first,'only-first')
            observation = until(lambda:read_json(bridge/'status.json').get('observation_screenshot'),seconds=3)
            self.assertEqual(character_x(bridge/observation),initial_x)
            self.assertTrue(read_json(bridge/'status.json')['paused'])
            self.stop(first)
            self.assertTrue(_pid_path(isolated['pid']))
            self.rpc(second,'game_stop',directory=str(destination),request_id='stop-second',confirm=True)
            stopped = until(lambda:state('stopped'),seconds=15)
            self.assertTrue(stopped['target_exited'] and stopped['release_confirmed'],stopped)
        self.assert_reaped(ready,first)
        self.assertFalse(_pid_path(isolated['pid']))

    def test_prepared_runtime_tampering_blocks_launch(self):
        with self.connect() as client:
            plan=self.prepare(client)
            with (self.destination/'runtime/GameQaEasyRpg.exe').open('ab') as stream:stream.write(b'changed')
            self.start(client,plan)
            failed=self.wait_state(client,'failed')
            self.assertIn('runtime_changed',failed['error'])
            self.assertFalse(any(self.bridge.iterdir()))

    def test_capture_failure_keeps_consumption_evidence_and_releases(self):
        with self.connect() as client:
            ready=self.ready(client)
            (self.bridge/'screenshots/capture-fault.png').mkdir()
            self.rpc(client,'control_action',directory=str(self.bridge),action='move',direction='right',
                     duration_ms=120,command_id='capture-fault',confirm=True,expect_error=True)
            receipt=read_json(self.bridge/'results/capture-fault.json')
            self.assertEqual((receipt['ok'],receipt['press_consumed'],receipt['release_consumed']),(False,True,True))
            failed=self.wait_state(client,'failed')
            self.assertTrue(failed['target_exited'] and failed['release_confirmed'],failed)
        self.assert_reaped(ready,client)

    def test_expired_request_cannot_consume_input(self):
        with self.connect() as client:
            ready=self.ready(client)
            previous=read_json(self.bridge/'request.json')
            request={**previous,'id':'expired','created_at':time.time()-5,
                     'payload':{'action':'move','direction':'right','duration_ms':100}}
            write_json(self.bridge/'request.json',request)
            def safely_paused():
                state = read_json(self.bridge/'status.json')
                return state if state.get('error') and state.get('paused') and state.get('held') == [] else None
            paused = until(safely_paused,seconds=3)
            self.assertIn('有效期',paused['error'])
            self.assertFalse((self.bridge/'results/expired.json').exists())
            self.stop(client)
        self.assert_reaped(ready,client)

    def test_target_stall_after_consumption_does_not_claim_release(self):
        with self.connect() as client:
            ready=self.ready(client)
            client.send('tools/call',{'name':'control_action','arguments':{
                'directory':str(self.bridge),'action':'move','direction':'right','duration_ms':1000,
                'command_id':'stall','confirm':True}})
            until(lambda:read_json(self.bridge/'status.json').get('held')==['right'] and
                  read_json(self.bridge/'status.json').get('target_ack_seq',0)>1,seconds=3)
            psutil.Process(ready['pid']).suspend()
            response=client.responses.get(timeout=6)
            client.records[-1]['response']=response
            self.assertIn('error',response)
            ended=self.wait_state(client,'cleanup_failed')
            self.assertTrue(ended['target_exited'])
            self.assertFalse(ended['release_confirmed'])
            receipt=read_json(self.bridge/'termination.json')
            self.assertTrue(receipt['press_consumed'])
            self.assertFalse(receipt['release_consumed'])
        self.assert_reaped(ready,client)
