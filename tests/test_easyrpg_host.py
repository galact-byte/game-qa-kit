"""Host acknowledgement semantics, independent from a compiled Player."""
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from plugins.easyrpg.host import EasyRpgSupervisor


class EasyRpgHostTests(unittest.TestCase):
    def host(self):
        target = SimpleNamespace(pid=42, argv=['fixture.exe'])
        channel = Mock()
        channel.exchange.return_value = {'seq': 9}
        return EasyRpgSupervisor(target, Path('.'), 'session', channel, 'a'*64)

    def test_initial_host_state_cannot_infer_target_pause(self):
        host = self.host()
        self.assertFalse(host.state()['paused'])
        self.assertFalse(host.state()['target_paused'])

    def test_pause_waits_for_current_target_acknowledgement(self):
        host = self.host()
        host.interrupt('pause')
        host.target_state = dict(paused=1, held=0, ack_seq=8, ack_kind=4)
        self.assertFalse(host.state()['paused'])
        host.target_state['ack_seq'] = 9
        self.assertTrue(host.state()['paused'])
        host.target_state['ack_kind'] = 3
        self.assertFalse(host.state()['paused'])

    def test_repeated_fault_does_not_supersede_pending_pause(self):
        host = self.host()
        host.fault('bad request')
        host.fault('bad request')
        host.channel.exchange.assert_called_once_with(kind=4, lease_ms=1500)

    def test_new_manual_pause_cannot_reuse_an_older_fault_command(self):
        host = self.host()
        host.fault('capture failed')
        host.pause_id = 'manual-stop'
        host.channel.exchange.return_value = {'seq': 10}
        host.interrupt('manual pause')
        self.assertEqual(host.pause_seq, 10)
        self.assertEqual(host.channel.exchange.call_count, 2)
        host.target_state = dict(paused=1, held=0, ack_seq=9, ack_kind=4)
        self.assertFalse(host.state()['paused'])
        host.target_state['ack_seq'] = 10
        self.assertTrue(host.state()['paused'])
        host.interrupt('manual pause')
        self.assertEqual(host.channel.exchange.call_count, 2)

    def test_startup_scene_delay_does_not_start_the_active_frame_watchdog(self):
        host = self.host()
        host.channel.token = 123
        host.channel.exchange.return_value = dict(session=123, pid=42, frame=61, ready=0)
        host.observe()
        self.assertEqual(host.ui_seq, 0)
        host.channel.exchange.return_value['ready'] = 1
        host.target.windows = lambda: [dict(client=(512,384),hwnd=99)]
        host.observe()
        self.assertEqual(host.ui_seq, 61)

    def test_repeated_release_ack_preserves_the_consumption_frame(self):
        host = self.host()
        host.channel.token = 123
        host.ids = {'down': 2, 'up': 3}
        host.active = dict(keyed=True, down_id='down', up_id='up', press=True, release=False)
        host.observer = dict(ready=True)
        snapshot = dict(session=123,pid=42,frame=10,ready=1,ack_seq=3,ack_kind=3,held=0)
        host.channel.exchange.return_value = snapshot
        host.observe()
        snapshot['frame'] = 11
        host.observe()
        self.assertEqual(host.active['release_frame'], 10)
        self.assertEqual(host.active['after_ui'], 10)
        self.assertGreater(host.ui_seq, host.active['after_ui'])

    def test_resume_does_not_complete_before_target_consumption(self):
        host = self.host()
        host.active = dict(request={'payload': {'action': 'resume'}}, resume_seq=9)
        host.target_state = dict(paused=1, ack_seq=8)
        with patch('gameqakit.duration_host.Supervisor.progress') as progress:
            host.progress()
            progress.assert_not_called()
            host.target_state = dict(paused=0, ack_seq=9)
            host.progress()
            progress.assert_called_once_with()
