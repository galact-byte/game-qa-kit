import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from PIL import Image
from gameqakit.duration_host import publish


class BakinTargetTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='gqk-bakin-target-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        from plugins.bakin.target import TargetChannel
        self.target=TargetChannel(self.root,'fixture',42,'123',clock=lambda:1000)
        self.addCleanup(self.target.close)
        self.identity=self.target.identity

    def status(self,**updates):
        value=dict(self.identity,published_at_ms=1000,command_seq=0,command_kind='',sample_seq=2,draw_seq=1,scene='TITLE',ready=True,paused=True,held=0,error=None,press=None,release=None)
        value.update(updates)
        self.target.mailbox.write_json('status',value)
        return self.target.observe()

    @unittest.skipUnless(os.name=='nt','Requires Windows named shared memory')
    def test_lease_and_status_use_named_shared_memory_without_disk_files(self):
        from plugins.bakin.target import BakinMailbox
        self.target.lease()
        self.assertFalse((self.root/'target-lease.json').exists())
        self.assertFalse((self.root/'target-lease.json.part').exists())
        with BakinMailbox.open_existing(42,'fixture') as peer:
            self.assertEqual(2500,peer.read_json('lease')['expires_at_ms'])
            self.target.clock=lambda:1100
            self.target.lease()
            self.assertEqual(2600,peer.read_json('lease')['expires_at_ms'])
            snapshot=dict(self.identity,published_at_ms=1100,command_seq=0,command_kind='',
                          sample_seq=2,draw_seq=1,scene='TITLE',ready=True,paused=True,held=0,
                          error=None,press=None,release=None)
            peer.write_json('status',snapshot)
        observed=self.target.observe()
        self.assertEqual(1100,observed['published_at_ms'])
        self.assertFalse((self.root/'target-status.json').exists())

    @unittest.skipUnless(os.name=='nt','Requires Windows named shared memory')
    def test_duplicate_shared_memory_creation_is_rejected(self):
        from plugins.bakin.target import TargetChannel
        with self.assertRaises(OSError):
            TargetChannel(self.root,'fixture',42,'123',clock=lambda:1000)

    @unittest.skipUnless(os.name=='nt','Requires Windows named shared memory')
    def test_incomplete_shared_memory_write_never_returns_torn_status(self):
        import ctypes
        self.target.mailbox.write_json('status',dict(self.identity,published_at_ms=1000,command_seq=0,
            command_kind='',sample_seq=2,draw_seq=1,scene='TITLE',ready=True,paused=True,held=0,
            error=None,press=None,release=None))
        # Simulate a writer dying mid-update (odd sequence number).
        seq=ctypes.c_long.from_address(self.target.mailbox.address)
        seq.value=seq.value+1
        self.assertIsNone(self.target.observe())

    @unittest.skipUnless(os.name=='nt','Requires Windows named shared memory')
    def test_supervisor_status_uses_shared_memory_without_bridge_status_file(self):
        import sys
        import time
        from gameqakit.control import BridgeClient
        from gameqakit.sessions import write_json
        from plugins.bakin.target import TargetChannel
        bridge = self.root / 'bridge'
        bridge.mkdir()
        target_dir = bridge / 'target'
        target_dir.mkdir()
        pid = os.getpid()
        exe = sys.executable
        with TargetChannel(target_dir, 'sup_shm', pid, '123', clock=lambda: 1000) as ch:
            meta = dict(protocol=2, engine='bakin', session_id='sup_shm', pid=pid,
                        exe_path=exe, control_transport='bakin-shm-v1')
            write_json(bridge / 'connection.json', meta)
            state = dict(protocol=2, engine='bakin', session_id='sup_shm', pid=pid, exe_path=exe,
                         updated_at=time.time(), paused=True, pause_id='', error='',
                         capabilities=['move', 'confirm', 'cancel', 'wait'],
                         buttons=['up', 'down', 'left', 'right', 'ok', 'cancel'],
                         max_duration_ms=1000, timing_mode='duration_ms', pause_scope='input',
                         simulation_pause=False, observation_seq=1, observation_screenshot='screenshots/startup.png',
                         held=[])
            ch.mailbox.write_json('supervisor', state)
            # Even if bridge/status.json is blocked as a directory, BridgeClient reads shared memory.
            (bridge / 'status.json').mkdir()
            client = BridgeClient(bridge, pid=pid, exe_path=exe)
            observed = client.status()
            self.assertTrue(observed['connected'], observed)
            self.assertEqual('sup_shm', observed['session_id'])
            # Stale updated_at in shared memory must still fail closed.
            ch.mailbox.write_json('supervisor', dict(state, updated_at=time.time() - 5))
            stale = client.status()
            self.assertFalse(stale['connected'])
            self.assertIn('心跳过期', stale['error'])
            client.close()
            # Non-Bakin engine claiming bakin-shm-v1 must be rejected.
            write_json(bridge / 'connection.json', dict(meta, engine='other'))
            other = BridgeClient(bridge, pid=pid, exe_path=exe)
            rejected = other.status()
            self.assertFalse(rejected['connected'])
            other.close()

    def test_down_command_carries_bounded_start_and_duration_without_legacy_hold(self):
        from gameqakit.duration_host import read
        self.target.command('down', action_id='short', key=16, duration=100)
        value = read(self.root/'commands/00000000000000000001.json')
        self.assertEqual(3, value['bridge_version'])
        self.assertNotIn('hold_until_ms', value)
        self.assertEqual(100, value.get('duration_ms'))
        self.assertEqual(2000, value.get('start_deadline_ms'))
        self.assertEqual(2100, value.get('hard_stop_at_ms'))

    def test_previous_bridge_version_is_rejected(self):
        for old in (1, 2):
            with self.subTest(bridge_version=old), self.assertRaisesRegex(ValueError,'identity'):
                self.status(bridge_version=old)

    def test_forged_consumption_deadline_cannot_confirm_an_action(self):
        self.target.command('down',action_id='action',key=16,duration=100)
        valid=dict(action_id='action',sample_seq=2,query_type=0,game_state=0,at_ms=1000,value=16,hold_until_ms=1100)
        self.status(command_seq=1,held=16,press=valid)
        self.assertTrue(self.target.action_pressed('action',16))
        for changes in (dict(hold_until_ms=1101),dict(at_ms=999),dict(query_type=2),dict(at_ms=True),dict(value=0)):
            self.status(command_seq=1,held=16,press=dict(valid,**changes))
            self.assertFalse(self.target.action_pressed('action',16))

    def test_initial_pause_and_monotonic_identity(self):
        self.assertTrue(self.status()['paused'])
        with self.assertRaises(ValueError):self.status(sample_seq=1)

    def test_readiness_must_be_a_boolean(self):
        for ready in ('true',1,None):
            with self.subTest(ready=ready),self.assertRaises(ValueError):
                self.status(ready=ready)

    def test_pid_reuse_identity_is_rejected(self):
        with self.assertRaises(ValueError):self.status(creation_id='124')

    def test_stale_status_is_rejected(self):
        with self.assertRaises(ValueError):self.status(published_at_ms=0)

    def test_command_does_not_overwrite_unacknowledged_action(self):
        self.target.command('resume')
        with self.assertRaises(RuntimeError):self.target.command('pause')
        self.assertEqual(1,len(list((self.root/'commands').iterdir())))
        self.status(command_seq=1,command_kind='resume')
        self.target.command('pause')
        self.assertTrue((self.root/'commands/00000000000000000002.json').is_file())

    def capture(self,color='red',**updates):
        self.target.request_capture('shot')
        path=self.root/'screenshots/00000001.png'
        Image.new('RGB',(1280,720),color).save(path)
        receipt=dict(self.identity,sequence=1,id='shot',screenshot='screenshots/00000001.png',sha256=hashlib.sha256(path.read_bytes()).hexdigest(),captured_at_ms=1000,draw_seq=2,scene='TITLE')
        receipt.update(updates);publish(self.root/'target-capture-result.json',receipt)
        return self.target.capture_result()

    def test_capture_input_snapshot_rejects_boolean_held_mask(self):
        with self.assertRaisesRegex(ValueError, 'input snapshot'):
            self.capture(input_snapshot=dict(action_id='action', held=True, sample_seq=2, at_ms=1000))

    def test_capture_input_snapshot_is_bound_to_readback_time(self):
        with self.assertRaisesRegex(ValueError, 'input snapshot'):
            self.capture(input_snapshot=dict(action_id='action', held=8, sample_seq=2, at_ms=1001))

    def test_capture_digest_and_request_identity(self):
        image,receipt=self.capture()
        self.assertEqual((1280,720),image.size)
        self.assertEqual('TITLE',receipt['scene'])

    def test_black_capture_remains_rejected(self):
        with self.assertRaisesRegex(RuntimeError,'invalid'):self.capture('black')

    def test_loading_observation_can_keep_a_valid_blank_frame(self):
        original = self.target.capture_result
        self.target.capture_result = lambda: original(allow_blank=True)
        image, receipt = self.capture('black')
        self.assertEqual((1280,720), image.size)
        self.assertIs(True, receipt['blank_frame'])

    def test_loading_permission_does_not_bypass_capture_digest_or_deadline(self):
        original = self.target.capture_result
        self.target.capture_result = lambda: original(allow_blank=True)
        with self.assertRaises(ValueError):
            self.capture('black', sha256='0'*64)

    def test_registered_loading_status_requires_actual_boolean_map_state(self):
        with self.assertRaises(ValueError):
            self.status(loading_protocol=1, title_state='TITLE', map_loading='false', map_fade='None')

    def test_forged_capture_digest_is_rejected(self):
        with self.assertRaises(ValueError):self.capture(sha256='0'*64)

    def test_capture_cannot_escape_channel(self):
        with self.assertRaises(ValueError):self.capture(screenshot='../elsewhere.png')

    def test_late_capture_is_rejected_even_with_valid_receipt(self):
        self.target.request_capture('late')
        self.target.clock=lambda:4000
        with self.assertRaises(TimeoutError):self.target.capture_result()

    def test_query_evidence_requires_neutral_release(self):
        self.status(command_seq=0)
        self.target.action_timing=dict(id='action',key=1,issued_at_ms=800,start_deadline_ms=1800,
            duration_ms=100,hard_stop_at_ms=1900)
        press=dict(action_id='action',sample_seq=2,query_type=0,game_state=2,at_ms=900,value=1,hold_until_ms=1000)
        release=dict(action_id='action',sample_seq=3,query_type=0,game_state=2,at_ms=1000,value=0,hold_until_ms=1000)
        self.status(sample_seq=3,press=press,release=release)
        self.assertTrue(self.target.action_released('action',1))
        self.status(sample_seq=3,press=press,release=dict(release,value=1))
        self.assertFalse(self.target.action_released('action',1))
