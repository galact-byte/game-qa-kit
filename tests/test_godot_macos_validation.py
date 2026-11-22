"""Reject a transient or stale foreground change before background inputs."""
import time
import unittest

from tools import verify_godot_macos as verification


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


if __name__ == '__main__':
    unittest.main()
