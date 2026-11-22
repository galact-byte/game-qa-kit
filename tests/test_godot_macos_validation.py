"""Reject a transient or stale foreground change before background inputs."""
import time
import unittest
from pathlib import Path
from unittest import mock

from tools import verify_godot_macos as verification
from tools import verify_godot_macos_managed as managed


class GodotFocusGuardTests(unittest.TestCase):
    def state(self, **changes):
        row = {'target_pid': 42, 'original_frontmost': 7, 'frontmost': 7,
               'target_activated': False, 'frontmost_changed': False,
               'samples': 15, 'updated_at': time.time()}
        return {**row, **changes}

    def test_accepts_fresh_unchanged_foreground(self):
        verification.check_focus(self.state(), 42, 7)

    def test_rejects_activation_even_when_foreground_was_restored(self):
        with self.assertRaises(AssertionError):
            verification.check_focus(self.state(target_activated=True), 42, 7)

    def test_rejects_transient_foreground_change_to_another_application(self):
        with self.assertRaises(AssertionError):
            verification.check_focus(self.state(frontmost_changed=True), 42, 7)

    def test_rejects_stale_monitor_instead_of_sending_input(self):
        with self.assertRaises(AssertionError):
            verification.check_focus(self.state(updated_at=time.time() - 5), 42, 7)

    def test_rejects_another_target_or_baseline(self):
        for field in ('target_pid', 'original_frontmost', 'frontmost'):
            with self.subTest(field=field), self.assertRaises(AssertionError):
                verification.check_focus(self.state(**{field: 99}), 42, 7)

    def test_rejects_unsampled_or_missing_monitor(self):
        for row in ({}, self.state(samples=0)):
            with self.subTest(row=row), self.assertRaises(AssertionError):
                verification.check_focus(row, 42, 7)


class ManagedInterruptionTests(unittest.TestCase):
    ready = {'pid': 11, 'worker_pid': 12, 'host_supervisor_pid': 13, 'focus_monitor_pid': 14}

    def test_waits_for_creator_owned_supervisor_after_inner_processes_exit(self):
        client = mock.Mock()
        client.call.side_effect = [
            {'state': 'connection_lost', 'worker_alive': True, 'can_stop': True},
            {'state': 'interrupted_unverified', 'worker_alive': False, 'can_stop': False},
        ]
        with mock.patch.object(managed, '_pid_path', return_value=''):
            final = managed.await_interrupted(client, Path('session'), self.ready)
        self.assertFalse(final['worker_alive'])
        self.assertEqual(client.call.call_count, 2)

    def test_live_supervisor_still_fails_within_original_eight_second_budget(self):
        client = mock.Mock()
        client.call.return_value = {'state': 'connection_lost', 'worker_alive': True, 'can_stop': True}
        with (mock.patch.object(managed, '_pid_path', return_value=''),
              mock.patch('tools.verify_macos_product.time.monotonic', side_effect=[0, 0, 9]),
              mock.patch('tools.verify_macos_product.time.sleep')):
            with self.assertRaisesRegex(RuntimeError, '超时'):
                managed.await_interrupted(client, Path('session'), self.ready)


if __name__ == '__main__':
    unittest.main()
