"""实体并发证据的判据；冒烟和未确认的人工操作不能算实体实证。"""
import unittest
from tests.native_input_concurrent import assess


class ConcurrentEvidenceTests(unittest.TestCase):
    def evidence(self):
        before = {'presses': 0, 'releases': 0, 'position': 100}
        after = {'presses': 12, 'releases': 12, 'position': 340, 'down': False}
        witness = {'left_presses': 4, 'right_presses': 0, 'motion': 20, 'clicks': 3}
        samples = [{'round': i, 'down': True, 'left': True, 'foreground': True,
                    'target_foreground': False, 'last_scan_code': 0xcd} for i in range(12)]
        return before, after, witness, samples

    def test_checks_do_not_certify_physical_device_provenance(self):
        result = assess(*self.evidence(), rounds=12)
        self.assertTrue(all(result['checks'].values()))
        self.assertFalse(result['physical_verified'])
        self.assertEqual(result['status'], 'checks_passed_pending_participant_confirmation')

    def test_smoke_without_participant_is_never_physical_verification(self):
        before, after, witness, samples = self.evidence()
        witness.update(left_presses=0, motion=0, clicks=0)
        for sample in samples:
            sample['left'] = False
        result = assess(before, after, witness, samples, rounds=12, smoke=True)
        self.assertEqual(result['status'], 'smoke_passed_no_physical_claim')
        self.assertFalse(result['physical_verified'])

    def test_no_participant_and_insufficient_overlap_stay_incomplete(self):
        before, after, witness, samples = self.evidence()
        for sample in samples[1:]:
            sample['left'] = False
        result = assess(before, after, witness, samples, rounds=12)
        self.assertEqual(result['status'], 'incomplete_participation')
        self.assertFalse(result['checks']['concurrent_left_and_right'])

    def test_crossed_keys_focus_or_missing_release_fail(self):
        for change in ('right_leak', 'extra_input', 'unreleased', 'foreground', 'wrong_scan_code'):
            with self.subTest(change=change):
                before, after, witness, samples = self.evidence()
                if change == 'right_leak':
                    witness['right_presses'] = 1
                elif change == 'extra_input':
                    after['presses'] += 1
                elif change == 'unreleased':
                    after['down'] = True
                elif change == 'foreground':
                    samples[0]['target_foreground'] = True
                else:
                    samples[0]['last_scan_code'] = 0xcb
                self.assertEqual(assess(before, after, witness, samples, rounds=12)['status'], 'failed')
