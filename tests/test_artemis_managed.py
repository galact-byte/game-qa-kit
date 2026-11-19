"""Real stdio MCP, registered authored scene, independent desktop and process cleanup."""
import ctypes
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest

import psutil
from PIL import Image
from gameqakit.sessions import read_json, write_json
from gameqakit.win32 import _pid_path
from tests.native_input_mcp import mcp, until

ROOT=Path(__file__).resolve().parents[1]
COMPONENTS=os.environ.get('GAMEQAKIT_TEST_ARTEMIS_COMPONENTS')


def marker(path):
    with Image.open(path) as image:
        rgb=image.convert('RGB')
        positions=[index%rgb.width for index,(r,g,b) in enumerate(rgb.getdata()) if r>180 and g<110 and b>120]
    if len(positions)!=2501: raise AssertionError(f'Authored marker pixel count: {len(positions)}')
    return min(positions)


@unittest.skipUnless(os.name=='nt' and COMPONENTS,'Requires registered Artemis runtime')
class ArtemisManagedTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='gameqa-artemis-managed-'); self.addCleanup(temp.cleanup)
        self.root=Path(temp.name); self.source=self.root/'sample'
        shutil.copytree(ROOT/'plugins/artemis/samples/authored',self.source)
        self.destination=self.root/'session'; self.bridge=self.destination/'bridge'
        if evidence:=os.environ.get('GAMEQAKIT_TEST_EVIDENCE'):
            self.addCleanup(shutil.copytree,self.root,Path(evidence)/self._testMethodName,
                            dirs_exist_ok=True,ignore=shutil.ignore_patterns('*.exe','*.dll','tmp','savedata'))

    def connect(self,root=None):
        portable=os.environ.get('GAMEQAKIT_TEST_PORTABLE_MCP')
        return mcp(root or self.root,Path(COMPONENTS)/'HaitokunoShoudou.exe',command=[portable] if portable else None)

    def rpc(self,client,name,expect_error=False,**arguments):
        client.send('tools/call',{'name':name,'arguments':arguments})
        return self.response(client,expect_error)

    def response(self,client,expect_error=False):
        response=client.responses.get(timeout=20); client.records[-1]['response']=response
        self.assertEqual(response['id'],client.number)
        if expect_error:
            self.assertIn('error',response); return response['error']
        self.assertNotIn('error',response,response)
        return json.loads(response['result']['content'][0]['text'])

    def state(self,client,expected):
        def poll():
            state=self.rpc(client,'game_status',directory=str(self.destination))
            if state['state'] in ('failed','cleanup_failed','stopped'):
                self.assertIn(state['state'],expected,state)
            return state if state['state'] in expected and (state['state'] in ('prepared','ready_paused') or not state['worker_alive']) else None
        return until(poll,seconds=25)

    def ready(self,client):
        inspection=self.rpc(client,'game_inspect',path=str(self.source),artifacts=COMPONENTS,destination=str(self.destination))
        self.assertEqual(inspection['launch_readiness'],'ready',inspection)
        plan=inspection['plan']
        self.rpc(client,'game_prepare',plan=plan,request_id='prepare',confirm=True)
        self.state(client,('prepared',))
        self.rpc(client,'game_start',directory=str(self.destination),fingerprint=plan['fingerprint'],request_id='start',confirm=True)
        state=self.state(client,('ready_paused',))
        self.assertEqual((state['paused'],state['held']),(True,[]))
        self.assertEqual(marker(state['screenshot']),80)
        self.assertFalse((self.bridge/'request.json').exists())
        self.rpc(client,'game_bind',directory=str(self.bridge),session_id=state['session_id'],confirm=True)
        self.rpc(client,'control_resume',directory=str(self.bridge),command_id='resume',confirm=True)
        return state

    def move(self,client,ident='right',duration=500):
        return self.rpc(client,'control_action',directory=str(self.bridge),action='move',direction='right',duration_ms=duration,command_id=ident,confirm=True)

    def stop(self,client):
        self.rpc(client,'game_stop',directory=str(self.destination),request_id='stop',confirm=True)
        state=self.state(client,('stopped',))
        self.assertTrue(state['release_confirmed'] and state['target_exited'],state)
        self.assertTrue(state['source_unchanged'] and state['runtime_source_unchanged'],state)
        return state

    def test_right_move_consumed_then_release_wait_and_manual_pause(self):
        with self.connect() as client:
            ready=self.ready(client)
            moved=self.move(client)
            self.assertTrue(moved['press_consumed'] and moved['release_consumed'],moved)
            self.assertGreater(moved['target_release_sample'],moved['target_press_sample'])
            self.assertGreater(marker(self.bridge/moved['screenshot']),80)
            held=marker(self.bridge/moved['screenshot'])
            for ident in ('stable-1','stable-2'):
                result=self.rpc(client,'control_action',directory=str(self.bridge),action='wait',duration_ms=200,command_id=ident,confirm=True)
                self.assertEqual(marker(self.bridge/result['screenshot']),held)
                self.assertFalse(result['press_consumed'] or result['release_consumed'])
            duplicate=self.move(client)
            self.assertEqual(duplicate['target_ack_seq'],moved['target_ack_seq'])
            self.rpc(client,'control_pause',directory=str(self.bridge))
            paused=until(lambda:(s if (s:=read_json(self.bridge/'status.json')).get('paused') and not s.get('held') else None),seconds=2)
            self.assertTrue(paused['target_paused']); self.assertEqual(paused['target_ack_kind'],4)
            self.rpc(client,'control_resume',directory=str(self.bridge),command_id='resume-again',confirm=True)
            self.assertTrue(self.move(client,'right-again',300)['release_consumed'])
            self.stop(client)
        ended=read_json(self.bridge/'termination.json')
        self.assertFalse(any(_pid_path(pid) for pid in (ready['pid'],ready['worker_pid'],client.child.pid,*ended['capture_worker_pids'])))

    def test_capture_failure_preserves_consumption_and_confirms_release(self):
        with self.connect() as client:
            ready=self.ready(client)
            (self.bridge/'screenshots/capture-fault.png').mkdir()
            self.rpc(client,'control_action',directory=str(self.bridge),action='move',direction='right',duration_ms=300,
                     command_id='capture-fault',confirm=True,expect_error=True)
            receipt=read_json(self.bridge/'results/capture-fault.json')
            self.assertEqual((receipt['ok'],receipt['press_consumed'],receipt['release_consumed']),(False,True,True))
            failed=self.state(client,('failed',))
            self.assertTrue(failed['target_exited'] and failed['release_confirmed'],failed)
        self.assertFalse(_pid_path(ready['pid']))

    def test_idle_lease_expiry_pauses_only_after_target_acknowledgement(self):
        with self.connect() as client:
            self.ready(client)
            paused=until(lambda:(s if (s:=read_json(self.bridge/'status.json')).get('paused') and s.get('target_ack_kind')==4 else None),seconds=3)
            self.assertEqual(paused['held'],[])
            self.assertTrue(paused['target_paused'])
            self.assertGreater(paused['target_ack_seq'],1)
            self.assertEqual(marker(self.bridge/paused['observation_screenshot']),80)
            self.stop(client)

    def test_expired_request_cannot_consume_input(self):
        with self.connect() as client:
            self.ready(client)
            request=read_json(self.bridge/'request.json')
            request.update(id='expired',created_at=time.time()-5,payload=dict(action='move',direction='right',duration_ms=100))
            write_json(self.bridge/'request.json',request)
            paused=until(lambda:(s if (s:=read_json(self.bridge/'status.json')).get('paused') and s.get('error') else None),seconds=3)
            self.assertEqual(paused['held'],[])
            self.assertIn('有效期',paused['error'])
            self.assertFalse((self.bridge/'results/expired.json').exists())
            self.stop(client)

    def test_mcp_process_exit_reaps_target_worker_and_capture_processes(self):
        with self.connect() as client:
            ready=self.ready(client)
            client.send('tools/call',{'name':'control_action','arguments':dict(directory=str(self.bridge),action='move',direction='right',duration_ms=1000,command_id='creator-exit',confirm=True)})
            until(lambda:read_json(self.bridge/'status.json').get('held')==['right'],seconds=1)
            owned=[process.pid for process in psutil.Process(client.child.pid).children(recursive=True)]
            client.child.kill(); client.child.wait(3)
            until(lambda:all(not _pid_path(pid) for pid in owned),seconds=5)
            write_json(self.root/'creator-exit.json',dict(owned_pids=owned,reaped=True,release_confirmed=False))
            receipt=self.bridge/'results/creator-exit.json'
            result=read_json(receipt) if receipt.exists() else {}
            self.assertFalse(result.get('ok',False),result)
        self.assertFalse(_pid_path(ready['pid']) or _pid_path(ready['worker_pid']))

    def test_two_sessions_isolate_input_and_cleanup(self):
        other=self.root/'second'; other.mkdir()
        destination=other/'session'; bridge=destination/'bridge'
        with self.connect() as first,self.connect(other) as second:
            ready=self.ready(first)
            self.rpc(first,'control_pause',directory=str(self.bridge))
            until(lambda:read_json(self.bridge/'status.json').get('paused'),seconds=2)
            plan=self.rpc(second,'game_inspect',path=str(self.source),artifacts=COMPONENTS,destination=str(destination))['plan']
            self.rpc(second,'game_prepare',plan=plan,request_id='prepare-second',confirm=True)
            def state(expected):
                value=self.rpc(second,'game_status',directory=str(destination))
                self.assertNotIn(value['state'],('failed','cleanup_failed'),value)
                return value if value['state']==expected else None
            until(lambda:state('prepared'),seconds=15)
            self.rpc(second,'game_start',directory=str(destination),fingerprint=plan['fingerprint'],request_id='start-second',confirm=True)
            isolated=until(lambda:state('ready_paused'),seconds=20)
            self.assertNotEqual(ready['pid'],isolated['pid'])
            self.rpc(first,'control_resume',directory=str(self.bridge),command_id='resume-first',confirm=True)
            self.assertTrue(self.move(first,'only-first',300)['release_consumed'])
            observed=read_json(bridge/'status.json')
            self.assertEqual(marker(bridge/observed['observation_screenshot']),80)
            self.assertTrue(observed['paused']); self.assertEqual(observed['held'],[])
            self.stop(first)
            self.assertTrue(_pid_path(isolated['pid']))
            self.rpc(second,'game_stop',directory=str(destination),request_id='stop-second',confirm=True)
            stopped=until(lambda:state('stopped'),seconds=15)
            self.assertTrue(stopped['release_confirmed'] and stopped['target_exited'],stopped)
        self.assertFalse(_pid_path(ready['pid']) or _pid_path(isolated['pid']))

    def test_target_stop_cannot_be_reported_as_consumed_release(self):
        with self.connect() as client:
            ready=self.ready(client)
            client.send('tools/call',{'name':'control_action','arguments':dict(directory=str(self.bridge),action='move',direction='right',duration_ms=1000,command_id='stopped',confirm=True)})
            until(lambda:read_json(self.bridge/'status.json').get('held')==['right'],seconds=1)
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel.OpenProcess.argtypes=[ctypes.c_ulong,ctypes.c_int,ctypes.c_ulong]; kernel.OpenProcess.restype=ctypes.c_void_p
            kernel.CloseHandle.argtypes=[ctypes.c_void_p]
            handle=kernel.OpenProcess(0x800,False,ready['pid']); self.assertTrue(handle)
            nt=ctypes.WinDLL('ntdll'); nt.NtSuspendProcess.argtypes=[ctypes.c_void_p]
            try:self.assertEqual(nt.NtSuspendProcess(handle),0)
            finally:kernel.CloseHandle(handle)
            self.response(client,expect_error=True)
            self.state(client,('failed','cleanup_failed'))
            ended=read_json(self.bridge/'termination.json')
            self.assertTrue(ended['press_consumed']); self.assertFalse(ended['release_consumed'])
        self.assertFalse(_pid_path(ready['pid']))
