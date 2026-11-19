"""Reject unregistered builds and exercise the Artemis file channel with real files."""
import importlib
import importlib.util
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from gameqakit.engines import inspect_game

SESSION = 'a' * 32


class ArtemisBridgeTests(unittest.TestCase):
    def module(self, name):
        try:
            return importlib.import_module('plugins.artemis.' + name)
        except ModuleNotFoundError:
            self.fail('Artemis implementation is missing: ' + name)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='artemis-channel-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def status(self, sample=7, *, session=SESSION, pid=42, clock=10000,
               command=2, ack=2, kind=2, paused=0, held=1, reason='none'):
        (self.root/f'consumed.{sample}.tsv').write_text(
            f'GQS1 {session} {pid} {sample} 14 {clock} {command} {ack} {kind} {paused} {held} {reason}\n',
            encoding='ascii', newline='\n')

    def test_authored_marker_is_recognized_without_claiming_commercial_support(self):
        (self.root/'gameqa-artemis-fixture.iet').write_text('fixture', encoding='utf-8')
        self.assertEqual(inspect_game(self.root)['engine'], 'artemis_authored_candidate')

    def test_channel_uses_target_clock_and_cannot_extend_a_down_deadline(self):
        Channel = self.module('channel').Channel
        channel = Channel(self.root, SESSION, 42)
        self.status(command=0, ack=0, kind=0, paused=1, held=0)
        channel.observe()
        channel.send('resume')
        channel.send('down', duration_ms=600)
        self.assertTrue((self.root/'input.1.txt').is_file(), 'Command publication must preserve immutable generations')
        before = (self.root/'input.1.txt').read_text(encoding='ascii').split()
        self.assertEqual(before, ['GQA1', SESSION, '42', '2', '2', '11500', '10600'])
        self.status(8,clock=10100,command=2,ack=2)
        channel.observe(); channel.heartbeat(1500)
        after = (self.root/'input.2.txt').read_text(encoding='ascii').split()
        self.assertEqual((self.root/'input.1.txt').read_text(encoding='ascii').split(), before)
        channel.heartbeat(1500)
        self.assertFalse((self.root/'input.3.txt').exists())
        self.assertEqual(after[-2:], ['11600','10600'])

    def test_incomplete_new_status_does_not_discard_a_committed_record(self):
        Channel = self.module('channel').Channel
        self.status()
        (self.root/'consumed.8.tsv').write_text('GQS1 '+SESSION, encoding='ascii')
        channel = Channel(self.root, SESSION, 42)
        self.assertEqual(channel.observe()['sample_seq'],7)

    def test_unknown_session_pid_and_regressing_ack_are_rejected(self):
        Channel = self.module('channel').Channel
        for change in ({'session':'b'*32},{'pid':43},{'command':1,'ack':2}, {'paused':2}):
            with self.subTest(change=change):
                self.status(**change)
                with self.assertRaises(ValueError): Channel(self.root,SESSION,42).observe()
        self.status()
        channel = Channel(self.root, SESSION,42); channel.observe()
        self.status(8,command=2,ack=1,kind=1)
        with self.assertRaises(ValueError): channel.observe()

    def test_expired_observation_and_out_of_range_duration_cannot_issue_down(self):
        Channel = self.module('channel').Channel
        channel = Channel(self.root,SESSION,42)
        with self.assertRaises(ValueError): channel.send('down',duration_ms=100)
        self.status(); channel.observe()
        for duration in (0,1001,True,1.5):
            with self.subTest(duration=duration), self.assertRaises(ValueError):
                channel.send('down',duration_ms=duration)

    def test_retired_record_in_delete_pending_state_does_not_hide_new_consumption(self):
        channel_module = self.module('channel')
        channel = channel_module.Channel(self.root, SESSION, 42)
        self.status()
        channel.observe()
        self.status(8, clock=10100, command=3, ack=3, kind=3, held=0)
        original = channel_module.open_shared
        def during_retirement(path):
            if path.name == 'consumed.7.tsv':
                raise PermissionError('Windows delete-pending record')
            return original(path)
        with patch.object(channel_module, 'open_shared', during_retirement):
            observed = channel.observe()
        self.assertEqual((observed['sample_seq'], observed['held'], observed['ack_kind']), (8, False, 3))

    def test_unreadable_records_cannot_refresh_the_target_clock(self):
        channel_module = self.module('channel')
        channel = channel_module.Channel(self.root, SESSION, 42)
        self.status()
        channel.observe()
        channel.observed_at -= 2
        with patch.object(channel_module, 'open_shared', side_effect=PermissionError('Delete pending')):
            channel.observe()
        with self.assertRaises(ValueError):
            channel.send('down', duration_ms=100)

    def test_prepare_rejects_unknown_binary_and_modified_authored_scene(self):
        prepare = self.module('prepare')
        (self.root/'HaitokunoShoudou.exe').write_bytes(b'unknown')
        with self.assertRaises(ValueError): prepare.artifact_digests(self.root)
        (self.root/prepare.SCRIPT).write_text('modified',encoding='utf-8')
        with self.assertRaises(ValueError): prepare.source_digests(self.root)
