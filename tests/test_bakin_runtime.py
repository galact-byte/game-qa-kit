"""Run the real bridge I/O and callbacks in a self-authored CLR fixture."""
import ctypes
from ctypes import wintypes as W
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

from test_bakin_state import ROOT, CSC
from gameqakit.duration_host import read as read_shared, publish as publish_shared


@unittest.skipUnless(os.name == 'nt' and CSC.is_file(), 'Requires installed Windows .NET Framework compiler')
class BakinRuntimeTests(unittest.TestCase):
    @classmethod
    def build_fixture(cls, source, executable, extra=()):
        build = subprocess.run([str(CSC), '/nologo', '/target:exe', '/main:RuntimeFixture', '/r:System.Drawing.dll',
            '/r:System.Web.Extensions.dll', '/out:' + str(executable),
            *[str(ROOT / 'plugins/bakin' / name) for name in ('InputState.cs','ChannelState.cs')],
            str(source), str(ROOT / 'tests/bakin/RuntimeFixture.cs'), *map(str, extra)],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)
        if build.returncode:
            raise AssertionError(build.stdout + build.stderr)

    @classmethod
    def setUpClass(cls):
        cls.artifacts = tempfile.TemporaryDirectory(prefix='gqk-bakin-runtime-build-')
        cls.addClassCleanup(cls.artifacts.cleanup)
        cls.executable = Path(cls.artifacts.name) / 'RuntimeFixture.exe'
        cls.build_fixture(ROOT / 'plugins/bakin/RuntimeBridge.cs', cls.executable)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='gqk-bakin-runtime-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for name in ('commands','screenshots'):
            (self.root / name).mkdir()
        self.sequence = 0
        self.process = subprocess.Popen([str(self.executable), str(self.root)],
            env=dict(os.environ,GAMEQAKIT_BAKIN_BRIDGE=str(self.root)),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        self.addCleanup(self.stop)
        self.await_value(lambda: (self.root / 'fixture.pid').is_file())
        kernel = ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.argtypes=[W.DWORD,W.BOOL,W.DWORD]; kernel.OpenProcess.restype=W.HANDLE
        kernel.GetProcessTimes.argtypes=[W.HANDLE]+[ctypes.POINTER(W.FILETIME)]*4
        kernel.CloseHandle.argtypes=[W.HANDLE]
        handle=kernel.OpenProcess(0x1000,False,self.process.pid)
        try:
            clocks=[W.FILETIME() for _ in range(4)]
            if not kernel.GetProcessTimes(handle,*map(ctypes.byref,clocks)): raise ctypes.WinError(ctypes.get_last_error())
            creation=str((clocks[0].dwHighDateTime<<32)|clocks[0].dwLowDateTime)
        finally:
            kernel.CloseHandle(handle)
        from plugins.bakin.target import BakinMailbox
        self.identity=dict(protocol=2,bridge_version=3,session_id='runtime_fixture',pid=self.process.pid,creation_id=creation)
        self.mailbox=BakinMailbox.create(self.process.pid,'runtime_fixture')
        self.addCleanup(self.mailbox.close)
        self.publish('binding.json',self.identity)
        self.await_value(lambda:self.status().get('sample_seq',0)>0)

    def stop(self):
        if getattr(self, 'mailbox', None) is not None:
            self.mailbox.close()
            self.mailbox = None
        if self.process.poll() is None:
            (self.root / 'fixture.stop').touch()
            try: self.process.wait(timeout=2)
            except subprocess.TimeoutExpired: self.process.kill(); self.process.wait(timeout=2)

    def clock(self):
        kernel=ctypes.WinDLL('kernel32'); kernel.GetTickCount64.restype=ctypes.c_ulonglong
        return kernel.GetTickCount64()

    def publish(self,name,value):
        publish_shared(self.root/name,value)

    def status(self):
        try:return self.mailbox.read_json('status') or {}
        except (OSError,ValueError,RuntimeError):return {}

    def publish_lease(self, expires_at_ms=None):
        self.mailbox.write_json('lease', dict(self.identity, expires_at_ms=self.clock()+1500 if expires_at_ms is None else expires_at_ms))

    def await_value(self,query):
        deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            result=query()
            if result:return result
            if self.process.poll() is not None: self.fail('CLR fixture exited unexpectedly')
            time.sleep(.01)
        self.fail('CLR bridge did not produce expected state before deadline: '+str(self.status()))

    def command(self,kind,key=0,duration=0):
        now=self.clock();self.sequence+=1
        self.publish('commands/%020d.json'%self.sequence,dict(self.identity,sequence=self.sequence,id='command_'+str(self.sequence),
            kind=kind,key=key,expires_at_ms=now+1000,duration_ms=duration if kind=='down' else 0,
            start_deadline_ms=now+1000 if kind=='down' else 0,
            hard_stop_at_ms=now+1000+duration if kind=='down' else 0))
        return self.sequence

    def resume(self):
        self.publish_lease()
        self.command('resume')
        return self.await_value(lambda:self.status().get('paused') is False)

    def test_short_action_waits_for_delayed_input_then_holds_for_requested_duration(self):
        self.resume()
        (self.root/'fixture.block-input').touch()
        self.await_value(lambda: (self.root/'fixture.input-blocked').exists())
        self.command('down',key=16,duration=100)
        self.await_value(lambda: self.status().get('command_seq')==self.sequence)
        time.sleep(.125)
        self.assertIsNone(self.status().get('press'))
        (self.root/'fixture.block-input').unlink()
        press=self.await_value(lambda:self.status().get('press'))
        self.assertEqual('command_2',press['action_id'])
        self.assertEqual(100,press['hold_until_ms']-press['at_ms'])
        release=self.await_value(lambda:self.status().get('release'))
        self.assertGreaterEqual(release['at_ms'],press['hold_until_ms'])
        self.assertIsNone(self.status().get('error'))

    def test_pause_cancels_pending_runtime_action_before_first_sample(self):
        self.resume()
        (self.root/'fixture.block-input').touch()
        self.await_value(lambda: (self.root/'fixture.input-blocked').exists())
        self.command('down',key=16,duration=100)
        self.await_value(lambda: self.status().get('command_seq')==self.sequence)
        self.command('pause')
        self.await_value(lambda: self.status().get('command_seq')==self.sequence)
        (self.root/'fixture.block-input').unlink()
        before=self.status()['sample_seq']
        state=self.await_value(lambda:s if (s:=self.status()).get('sample_seq',0)>before else None)
        self.assertEqual(0,state['held'])
        self.assertIsNone(state['press'])
        self.assertTrue(state['paused'])

    def test_repeated_lease_publication_with_production_runtime_reader(self):
        from plugins.bakin.target import TargetChannel
        self.mailbox.close()
        self.mailbox = None
        # Verify TargetChannel opens/owns shared memory cleanly on a fresh process.
        self.stop()
        (self.root / 'fixture.stop').unlink(missing_ok=True)
        (self.root / 'binding.json').unlink(missing_ok=True)
        self.process = subprocess.Popen([str(self.executable), str(self.root)],
            env=dict(os.environ,GAMEQAKIT_BAKIN_BRIDGE=str(self.root)),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        self.await_value(lambda: (self.root / 'fixture.pid').is_file() and (self.root / 'fixture.pid').read_text().strip() == str(self.process.pid))
        from types import SimpleNamespace
        from plugins.bakin.prepare import creation_identity
        creation = creation_identity(SimpleNamespace(process=self.process._handle))
        target=TargetChannel(self.root,'runtime_fixture_lease',self.process.pid,creation,clock=self.clock)
        self.addCleanup(target.close)
        self.mailbox = target.mailbox
        self.await_value(lambda: (target.observe() or {}).get('sample_seq', 0) > 0)
        for _ in range(2000):
            target.lease()
            time.sleep(.0005)
        state=self.await_value(target.observe)
        self.assertIsNone(state.get('error'),state)
        self.assertGreater(state['sample_seq'],1)
        self.assertTrue(state['paused'])
        self.assertEqual(0,state['held'])
        self.assertFalse((self.root/'target-status.json').exists())
        self.assertFalse((self.root/'target-lease.json').exists())
        self.assertFalse((self.root/'target-io.json').exists())

    def test_status_and_pause_survive_blocked_status_disk_destination(self):
        # Blocking status/lease/io paths on disk cannot stall shared-memory realtime status.
        for name in ('target-status.json', 'target-lease.json', 'target-io.json'):
            path = self.root / name
            path.unlink(missing_ok=True)
            path.mkdir()
            self.addCleanup(lambda p=path: p.rmdir() if p.is_dir() else None)
        self.resume()
        before = self.status()
        deadline = time.monotonic() + 1.2
        state = before
        while time.monotonic() < deadline:
            self.publish_lease()
            state = self.status() or state
            self.assertIsNone(state.get('error'), state)
            self.assertLess(self.clock() - state['published_at_ms'], 1000, state)
            time.sleep(.02)
        self.command('pause')
        after = self.await_value(lambda: self.status() if self.status().get('command_seq') == self.sequence else None)
        self.assertIsNone(after.get('error'), after)
        self.assertTrue(after['paused'])
        self.assertEqual(0, after['held'])
        self.assertGreater(after['sample_seq'], before['sample_seq'])
        self.assertLess(self.clock() - after['published_at_ms'], 1000)

    def restart_with_delayed_status(self):
        self.stop()
        original = (ROOT / 'plugins/bakin/RuntimeBridge.cs').read_text(encoding='utf-8')
        seam = 'PublishSharedStatus(status);'
        self.assertEqual(1, original.count(seam))
        source = self.root / 'DelayedStatusRuntimeBridge.cs'
        source.write_text(original.replace(seam,
            'if(File.Exists(Path.Combine(root,"fixture.slow-status"))) {'
            ' File.WriteAllText(Path.Combine(root,"fixture.status-write-started"),"1");'
            ' int delay=Int32.Parse(File.ReadAllText(Path.Combine(root,"fixture.slow-status"))); '
            ' File.Delete(Path.Combine(root,"fixture.slow-status")); Thread.Sleep(delay); } ' + seam), encoding='utf-8')
        self.executable = self.root / 'DelayedStatusRuntimeFixture.exe'
        self.build_fixture(source, self.executable)
        self.setUp()

    def test_short_action_is_consumed_while_status_publication_is_slow(self):
        self.restart_with_delayed_status()
        self.resume()
        (self.root / 'fixture.slow-status').write_text('160', encoding='utf-8')
        self.await_value(lambda: (self.root / 'fixture.status-write-started').exists())
        self.command('down', key=4, duration=100)
        state = self.await_value(lambda: value if (value := self.status()).get('error')
            or value.get('command_seq') == self.sequence else None)
        self.assertIsNone(state.get('error'), state)
        self.assertEqual(self.sequence, state['command_seq'])
        press = self.await_value(lambda: (self.status().get('press') or None))
        self.assertEqual('command_2', press['action_id'])
        self.assertEqual(4, press['value'])

    def test_status_worker_stall_still_faults_after_one_second(self):
        self.restart_with_delayed_status()
        self.resume()
        (self.root / 'fixture.slow-status').write_text('1150', encoding='utf-8')
        state = self.await_value(lambda: value if (value := self.status()).get('error') else None)
        self.assertEqual('io_stalled', state['error'])
        self.assertTrue(state['paused'])
        self.assertEqual(0, state['held'])
        self.assertEqual('status_publish', state['io_stall_phase'])
        self.assertGreaterEqual(state['io_stall_ms'], 1000)

    def test_fault_snapshot_distinguishes_blocked_status_from_runtime_pause(self):
        self.restart_with_delayed_status()
        self.resume()
        (self.root / 'fixture.slow-status').write_text('1150', encoding='utf-8')
        state = self.await_value(lambda: value if (value := self.status()).get('error') else None)
        self.assertIn('io_stall_detail', state)
        detail = state['io_stall_detail']
        self.assertEqual('snapshot', detail['status']['operation'])
        self.assertLess(detail['runtime']['age_ms'], 1000)
        self.assertLess(detail['runtime']['max_gap_ms'], 1000)
        self.assertEqual('io_stalled', state['error'])
        self.assertEqual(0, state['held'])

    def test_fault_snapshot_retains_process_wide_heartbeat_gap(self):
        import psutil
        self.resume()
        self.await_value(lambda: self.io_diagnostic().get('runtime', {}).get('at_ms'))
        process = psutil.Process(self.process.pid)
        process.suspend()
        try:
            time.sleep(1.15)
        finally:
            process.resume()
        state = self.await_value(lambda: value if (value := self.status()).get('error') else None)
        self.assertIn('io_stall_detail', state)
        runtime = state['io_stall_detail']['runtime']
        self.assertGreaterEqual(max(runtime['age_ms'], runtime['max_gap_ms']), 1000)
        self.assertEqual('io_stalled', state['error'])
        self.assertEqual(0, state['held'])
        self.assertTrue(state['paused'])

    def test_unwritable_diagnostic_does_not_stall_runtime_heartbeat(self):
        path = self.root / 'target-io.json'
        path.unlink(missing_ok=True)
        path.mkdir()
        self.addCleanup(lambda: path.rmdir() if path.is_dir() else None)
        self.resume()
        time.sleep(.15)
        self.command('pause')
        state = self.await_value(lambda: value if (value := self.status()).get('command_seq') == self.sequence else None)
        self.assertIn('runtime', state)
        self.assertLess(state['runtime']['age_ms'], 1000)
        self.assertIsNone(state['error'])
        self.assertTrue(state['paused'])
        self.assertEqual(0, state['held'])

    def test_runtime_reports_bounded_io_phase_diagnostics(self):
        state=self.await_value(lambda:self.status() if 'io_phase' in self.status() else None)
        self.assertIn(state['io_phase'],('initial_status','status_snapshot','status_open','status_write',
            'status_flush','status_replace','lease_read','lease_apply','command_scan','command_read',
            'command_apply','command_delete','capture_request','sleep'))
        self.assertIsInstance(state['io_max_phase_ms'],int)
        self.assertIsNone(state['io_stall_phase'])
        self.assertEqual(0,state['io_stall_ms'])

    def test_startup_record_identifies_successful_initialization(self):
        diagnostic = self.await_value(lambda: value if (value := read_shared(
            self.root / 'target-startup.json')).get('phase') == 'running' else None)
        self.assertEqual('running', diagnostic['phase'])
        self.assertEqual(self.process.pid, diagnostic['pid'])
        self.assertEqual(self.identity['creation_id'], diagnostic['creation_id'])
        self.assertIsNone(diagnostic['error'])
        self.assertTrue(self.status()['paused'])
        self.assertEqual(0, self.status()['held'])

    def test_invalid_initial_binding_is_diagnosed_without_granting_control(self):
        self.stop()
        (self.root / 'fixture.stop').unlink()
        (self.root / 'target-startup.json').unlink(missing_ok=True)
        self.publish('binding.json', dict(self.identity, protocol=99))
        self.process = subprocess.Popen([str(self.executable), str(self.root)],
            env=dict(os.environ, GAMEQAKIT_BAKIN_BRIDGE=str(self.root)),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        diagnostic = self.await_value(lambda: read_shared(self.root / 'target-startup.json'))
        if diagnostic.get('phase') != 'failed':
            diagnostic = self.await_value(lambda: value if (value := read_shared(
                self.root / 'target-startup.json')).get('phase') == 'failed' else None)
        self.assertEqual('binding_validate', diagnostic['failed_phase'])
        self.assertEqual(self.process.pid, diagnostic['pid'])
        self.assertEqual('ArgumentException', diagnostic['error_type'])
        self.assertIsInstance(diagnostic['hresult'], int)
        self.assertTrue(diagnostic['error'])
        self.assertFalse((self.root / 'target-status.json').exists())
        self.await_value(lambda: (self.root / 'fixture.held').read_text() == '0')
        self.assertIsNone(self.process.poll())

    def test_missing_shared_memory_fails_startup_without_granting_control(self):
        self.stop()
        (self.root / 'fixture.stop').unlink()
        (self.root / 'target-startup.json').unlink(missing_ok=True)
        (self.root / 'binding.json').unlink()
        self.process = subprocess.Popen([str(self.executable), str(self.root)],
            env=dict(os.environ, GAMEQAKIT_BAKIN_BRIDGE=str(self.root)),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        from types import SimpleNamespace
        from plugins.bakin.prepare import creation_identity
        self.identity.update(pid=self.process.pid, creation_id=creation_identity(
            SimpleNamespace(process=self.process._handle)))
        # Intentionally do NOT create BakinMailbox before writing binding.json.
        self.publish('binding.json', self.identity)
        diagnostic = self.await_value(lambda: value if (value := read_shared(
            self.root / 'target-startup.json')).get('phase') == 'failed' else None)
        self.assertEqual('realtime_ipc', diagnostic['failed_phase'])
        self.assertEqual(self.process.pid, diagnostic['pid'])
        self.assertTrue(diagnostic['error'])
        self.await_value(lambda: (self.root / 'fixture.held').read_text() == '0')
        self.assertIsNone(self.process.poll())

    def test_unwritable_startup_record_does_not_break_valid_control_status(self):
        self.stop()
        (self.root / 'fixture.stop').unlink()
        (self.root / 'target-startup.json').unlink(missing_ok=True)
        # A directory at the diagnostic destination deterministically denies publication.
        (self.root / 'target-startup.json').mkdir()
        (self.root / 'binding.json').unlink()
        self.process = subprocess.Popen([str(self.executable), str(self.root)],
            env=dict(os.environ, GAMEQAKIT_BAKIN_BRIDGE=str(self.root)),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        from types import SimpleNamespace
        from plugins.bakin.prepare import creation_identity
        from plugins.bakin.target import BakinMailbox
        self.identity.update(pid=self.process.pid, creation_id=creation_identity(
            SimpleNamespace(process=self.process._handle)))
        self.mailbox = BakinMailbox.create(self.process.pid, 'runtime_fixture')
        self.addCleanup(self.mailbox.close)
        self.publish('binding.json', self.identity)
        self.await_value(lambda: self.status().get('sample_seq', 0) > 0)
        self.resume()
        self.command('down', 16, 800)
        self.await_value(lambda: self.status().get('press'))
        self.command('pause')
        state = self.await_value(lambda: self.status() if self.status().get('release') else None)
        self.assertTrue(state['paused'])
        self.assertEqual(0, state['held'])
        self.assertIsNone(state['error'])
        self.assertIsNone(self.process.poll())

    def test_initial_callbacks_are_neutral_and_not_a_query_receipt(self):
        state=self.status()
        self.assertTrue(state['paused'])
        self.assertEqual(0,state['held'])
        self.assertIsNone(state['press'])
        self.assertIsNone(state['release'])

    def test_ready_follows_normal_title_state_after_fade(self):
        state=self.status()
        self.assertFalse(state.get('ready',False))
        (self.root/'fixture.logo').touch()
        self.await_value(lambda:self.status().get('scene')=='LOGO')
        self.assertFalse(self.status().get('ready',False))
        (self.root/'fixture.fadein').touch()
        self.await_value(lambda:self.status().get('scene')=='TITLE')
        self.assertFalse(self.status().get('ready',False))
        (self.root/'fixture.ready').touch()
        self.await_value(lambda:self.status().get('ready') is True)
        self.assertEqual('TITLE',self.status()['scene'])

    def test_map_readiness_requires_real_loading_end_and_finished_fade(self):
        for marker in ('fixture.map-loading', 'fixture.map-fading', 'fixture.map'):
            (self.root / marker).touch()
        state = self.await_value(lambda: self.status() if self.status().get('scene') == 'MAP' else None)
        self.assertFalse(state['ready'])
        self.assertTrue(state['map_loading'])
        self.assertEqual('FadeIn', state['map_fade'])
        (self.root / 'fixture.map-loading').unlink()
        state = self.await_value(lambda: self.status() if self.status().get('map_loading') is False else None)
        self.assertFalse(state['ready'])
        (self.root / 'fixture.map-fading').unlink()
        state = self.await_value(lambda: self.status() if self.status().get('ready') is True else None)
        self.assertEqual('None', state['map_fade'])
        self.assertEqual(1, state['loading_protocol'])

    def test_real_queries_and_pause_release_are_published(self):
        self.resume();self.command('down',16,800)
        press=self.await_value(lambda:self.status().get('press'))
        self.assertEqual('command_2',press['action_id'])
        self.assertEqual(16,press['value'])
        self.command('pause')
        release=self.await_value(lambda:self.status().get('release'))
        self.assertEqual('command_2',release['action_id'])
        self.assertEqual(0,release['value'])
        self.assertGreater(release['at_ms'],press['at_ms'])
        self.assertEqual(0,self.status()['held'])

    def test_foreign_command_faults_and_releases(self):
        self.resume();self.command('down',16,800)
        self.await_value(lambda:self.status().get('press'))
        self.sequence+=1
        self.publish('commands/%020d.json'%self.sequence,dict(self.identity,session_id='other',sequence=self.sequence,
            id='foreign',kind='pause',key=0,expires_at_ms=self.clock()+1000,duration_ms=0,start_deadline_ms=0,hard_stop_at_ms=0))
        self.await_value(lambda:self.status().get('error'))
        self.assertEqual(0,self.status()['held'])
        self.assertTrue(self.status()['paused'])

    def test_capture_records_input_at_readback_and_preserves_release(self):
        self.resume(); self.command('down', 8, 1000)
        press = self.await_value(lambda: self.status().get('press'))
        receipts = []
        for sequence in (1, 2):
            self.publish('target-capture.json', dict(self.identity, sequence=sequence,
                id='held_'+str(sequence), expires_at_ms=self.clock()+3000))
            receipt = self.await_value(lambda: value if (value := read_shared(
                self.root/'target-capture-result.json')).get('sequence') == sequence else None)
            receipts.append(receipt)
            snapshot = receipt['input_snapshot']
            self.assertEqual('command_2', snapshot['action_id'])
            self.assertEqual(8, snapshot['held'])
            self.assertGreaterEqual(snapshot['sample_seq'], press['sample_seq'])
            self.assertLessEqual(snapshot['at_ms'], receipt['captured_at_ms'])
        self.assertGreater(receipts[1]['draw_seq'], receipts[0]['draw_seq'])
        self.command('pause')
        self.await_value(lambda: self.status().get('release'))
        self.publish('target-capture.json', dict(self.identity, sequence=3,
            id='released', expires_at_ms=self.clock()+3000))
        receipt = self.await_value(lambda: value if (value := read_shared(
            self.root/'target-capture-result.json')).get('sequence') == 3 else None)
        self.assertEqual(0, receipt['input_snapshot']['held'])

    def test_capture_diagnostics_identify_waiting_draw_and_keep_heartbeat_live(self):
        (self.root/'fixture.skip-draw').touch()
        self.publish('target-capture.json',dict(self.identity,sequence=1,id='waiting_picture',expires_at_ms=self.clock()+3000))
        state=self.await_value(lambda:self.status() if (self.status().get('capture') or {}).get('phase')=='waiting_draw' else None)
        capture=state['capture']
        self.assertEqual((1,'waiting_picture'),(capture['sequence'],capture['id']))
        self.assertIsNone(capture['error'])
        self.assertGreaterEqual(capture['phase_at_ms'],capture['accepted_at_ms'])
        before=state['published_at_ms']
        state=self.await_value(lambda:self.status() if self.status().get('published_at_ms',0)>before+50 else None)
        self.assertEqual('waiting_draw',state['capture']['phase'])
        self.assertGreaterEqual(state['capture']['phase_ms'],50)
        self.assertLess(self.clock()-state['published_at_ms'],1000)

    def test_capture_diagnostics_identify_blocked_render_readback(self):
        marker=self.root/'fixture.block-readback'
        marker.touch()
        try:
            self.publish('target-capture.json',dict(self.identity,sequence=1,id='blocked_readback',expires_at_ms=self.clock()+3000))
            state=self.await_value(lambda:self.status() if (self.status().get('capture') or {}).get('phase')=='readback' else None)
            before=state['published_at_ms']
            state=self.await_value(lambda:self.status() if self.status().get('published_at_ms',0)>before+50 else None)
            self.assertEqual('readback',state['capture']['phase'])
            self.assertGreaterEqual(state['capture']['phase_ms'],50)
            self.assertIsNone(state['capture']['error'])
        finally:
            marker.unlink()
        state=self.await_value(lambda:self.status() if (self.status().get('capture') or {}).get('phase')=='done' else None)
        self.assertEqual('blocked_readback',state['capture']['id'])

    def test_receipt_publication_reports_phase_without_blocking_pause(self):
        self.stop()
        source=self.root/'DelayedCaptureBridge.cs'
        source.write_text('using FileStream = DelayedDurableFileStream;\n'+
                          (ROOT/'plugins/bakin/RuntimeBridge.cs').read_text(encoding='utf-8'),encoding='utf-8')
        self.executable=self.root/'DelayedCaptureFixture.exe'
        self.build_fixture(source,self.executable,(ROOT/'tests/bakin/DelayedDurableFileStream.cs',))
        self.setUp()
        self.resume()
        marker=self.root/'fixture.block-receipt-flush'
        marker.touch()
        try:
            self.publish('target-capture.json',dict(self.identity,sequence=1,id='blocked_receipt',expires_at_ms=self.clock()+3000))
            state=self.await_value(lambda:self.status() if (self.status().get('capture') or {}).get('phase')=='receipt_publish' else None)
            self.assertEqual('blocked_receipt',state['capture']['id'])
            diagnostic = self.await_value(lambda: value if (value := self.io_diagnostic()).get(
                'capture', {}).get('operation') == 'flush' else None)
            self.assertEqual(str(self.root / 'target-capture-result.json'), diagnostic['capture']['path'])
            before_diagnostic = diagnostic['published_at_ms']
            self.command('pause')
            state=self.await_value(lambda:self.status() if self.status().get('command_seq')==self.sequence else None)
            self.assertTrue(state['paused'])
            self.assertEqual(0,state['held'])
            self.assertEqual('receipt_publish',state['capture']['phase'])
            self.assertFalse((self.root/'target-capture-result.json').exists())
            diagnostic = self.await_value(lambda: value if (value := self.io_diagnostic()).get(
                'published_at_ms', 0) > before_diagnostic else None)
            self.assertEqual('flush', diagnostic['capture']['operation'])
        finally:
            marker.unlink()
        self.await_value(lambda:self.status() if (self.status().get('capture') or {}).get('phase')=='done' else None)
        self.assertEqual('blocked_receipt',read_shared(self.root/'target-capture-result.json')['id'])

    def io_diagnostic(self):
        return self.mailbox.read_json('diagnostic') or {}

    def test_steady_state_does_not_write_status_lease_or_io_files_to_disk(self):
        self.resume()
        self.await_value(lambda: self.io_diagnostic().get('runtime', {}).get('at_ms'))
        time.sleep(.15)
        self.assertFalse((self.root / 'target-status.json').exists())
        self.assertFalse((self.root / 'target-lease.json').exists())
        self.assertFalse((self.root / 'target-io.json').exists())

    def test_receipt_replace_failure_retains_file_and_error_in_independent_diagnostic(self):
        path = self.root / 'target-capture-result.json'
        self.publish(path.name, dict(sequence=0))
        with path.open('rb'):
            self.publish('target-capture.json', dict(self.identity, sequence=1,
                id='blocked_receipt_replace', expires_at_ms=self.clock()+3000))
            diagnostic = self.await_value(lambda: value if (value := self.io_diagnostic()).get(
                'capture', {}).get('failure') else None)
            failure = diagnostic['capture']['failure']
            self.assertEqual('replace', failure['operation'])
            self.assertEqual(str(path), failure['path'])
            self.assertIn(failure['hresult'] & 0xffff, (5, 32, 33, 1175))
            self.assertEqual('blocked_receipt_replace', read_shared(
                self.root / 'target-capture-result.json.part')['id'])
            self.assertTrue((self.root / 'screenshots/00000001.png').is_file())
            self.assertTrue(self.status()['paused'])
            self.assertEqual(0, self.status()['held'])
            # On failure, target-io.json post-mortem is also flushed to disk.
            disk_diag = self.await_value(lambda: value if (value := read_shared(
                self.root / 'target-io.json')).get('capture', {}).get('failure') else None)
            self.assertEqual('replace', disk_diag['capture']['failure']['operation'])
        self.assertIsNone(self.process.poll())

    def test_unwritable_io_diagnostic_does_not_block_normal_press_and_release(self):
        path = self.root / 'target-io.json'
        path.mkdir()
        self.addCleanup(lambda: path.rmdir() if path.is_dir() else None)
        self.await_value(lambda: self.io_diagnostic().get('pid'))
        time.sleep(.15)
        self.resume(); self.command('down', 16, 800)
        self.await_value(lambda: self.status().get('press'))
        self.command('pause')
        state = self.await_value(lambda: self.status() if self.status().get('release') else None)
        self.assertTrue(state['paused'])
        self.assertEqual(0, state['held'])
        self.assertIsNone(state['error'])

    def test_png_receipt_is_complete_and_bound_to_request(self):
        self.publish('target-capture.json',dict(self.identity,sequence=1,id='picture_1',expires_at_ms=self.clock()+2000))
        def receipt():
            try:return read_shared(self.root/'target-capture-result.json')
            except (OSError,ValueError):return {}
        result=self.await_value(receipt)
        self.assertEqual('picture_1',result['id'])
        self.assertEqual(1,result['sequence'])
        path=self.root/result['screenshot']
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),result['sha256'])
        from PIL import Image
        with Image.open(path) as image:
            image.load(); self.assertEqual((640,360),image.size)
        state=self.await_value(lambda:self.status() if (self.status().get('capture') or {}).get('phase')=='done' else None)
        self.assertEqual((1,'picture_1'),(state['capture']['sequence'],state['capture']['id']))
        self.assertIsNone(state['capture']['error'])

    def test_png_encoding_does_not_block_live_status_or_pause(self):
        self.resume()
        (self.root/'fixture.noisy-capture').touch()
        self.publish('target-capture.json',dict(self.identity,sequence=1,id='slow_png',expires_at_ms=self.clock()+3000))
        partial=self.root/'screenshots/00000001.png.part'
        self.await_value(partial.exists)
        before=self.await_value(self.status)
        self.command('pause')
        after=self.await_value(lambda:state if (state:=self.status()).get('command_seq')==self.sequence
            and state.get('sample_seq',0)>before['sample_seq'] else None)
        self.assertTrue(partial.exists(),'Control status was published only after PNG encoding ended')
        self.assertTrue(after['paused'],after)
        self.assertEqual(0,after['held'])
        self.assertIsNone(after['error'],after)
        self.assertGreater(after['sample_seq'],before['sample_seq'])
        self.assertGreater(after['published_at_ms'],before['published_at_ms'])
        self.assertEqual('encoding',after['capture']['phase'])
        self.assertEqual('slow_png',after['capture']['id'])

    def test_png_publication_failure_faults_control_and_releases(self):
        self.resume(); self.command('down',16,1000)
        self.await_value(lambda:self.status().get('press'))
        (self.root/'screenshots/00000001.png').mkdir()
        self.publish('target-capture.json',dict(self.identity,sequence=1,id='blocked_png',expires_at_ms=self.clock()+3000))
        self.await_value(lambda:self.status().get('error'))
        self.assertTrue(self.status()['paused'])
        self.assertEqual(0,self.status()['held'])
        capture=self.status()['capture']
        self.assertEqual('image_publish',capture['phase'])
        self.assertEqual('blocked_png',capture['id'])
        self.assertIn('IOException',capture['error'])

    def test_invalid_shared_lease_faults_and_releases(self):
        self.resume();self.command('down',16,1000)
        self.await_value(lambda:self.status().get('press'))
        self.mailbox.write_json('lease',dict(self.identity,session_id='other',expires_at_ms=self.clock()+1500))
        self.await_value(lambda:self.status().get('error')=='invalid_channel_message')
        self.assertTrue(self.status()['paused'])
        self.assertEqual(0,self.status()['held'])

    def test_status_producer_stall_neutralizes_without_holding_input_lock(self):
        from plugins.bakin.target import TargetChannel
        self.restart_with_delayed_status()
        self.resume();self.command('down',16,1000)
        self.await_value(lambda:self.status().get('press'))
        (self.root / 'fixture.slow-status').write_text('1150', encoding='utf-8')
        def neutral():
            try:return (self.root/'fixture.held').read_text()=='0'
            except OSError:return False
        self.await_value(neutral)
        # Host-side TargetChannel.observe() must also reject stale snapshots instead of masking expiry.
        stale_error = None
        deadline = time.monotonic() + 2
        observer = object.__new__(TargetChannel)
        observer.root = self.root
        observer.clock = self.clock
        observer.identity = dict(self.identity)
        observer.sequence = self.sequence
        observer.sample_sequence = observer.draw_sequence = observer.capture_sequence = 0
        observer.pending_capture = observer.action_timing = None
        observer.status = {}
        observer.mailbox = self.mailbox
        while time.monotonic() < deadline:
            try:
                state = observer.observe()
                if state and state.get('error') == 'io_stalled':
                    break
            except ValueError as exc:
                stale_error = str(exc)
                break
            time.sleep(.01)
        state = self.await_value(lambda:self.status() if self.status().get('error')=='io_stalled' else None)
        self.assertTrue(state['paused'])
        self.assertEqual(0, state['held'])
        # Either host observed stale >1s before recovery or bridge latched io_stalled within 1s.
        self.assertTrue(stale_error is None or 'Stale or invalid Bakin target status' in stale_error)
