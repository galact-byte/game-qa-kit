"""正式游戏并发必须同时具备正常动作、真人重叠和隔离证据。"""
import copy
import unittest
from tests import native_input_game as game


class GameConcurrentEvidenceTests(unittest.TestCase):
    def evidence(self):
        actions = []
        samples = []
        for index, (action, button) in enumerate((('move', 'down'), ('move', 'down'),
                                                   ('confirm', 'ok'), ('cancel', 'cancel'))):
            start = 1000 + index * 1000 if index < 3 else 15000
            label = f'key-{index}'
            actions.append({'label': label, 'action': action, 'duration_ms': 120,
                            'result': {'status': 'done', 'press_consumed': True,
                                       'release_consumed': True, 'held': [],
                                       'started_monotonic_ms': start,
                                       'completed_monotonic_ms': start + 300}})
            samples.append({'label': label, 'ticks': start + 60, 'held': [button],
                            'left': True, 'foreground': True, 'target_foreground': False})
        for index in range(7):
            start = 4000 + index * 1000
            label = f'04-settings-hold-{index}'
            actions.append({'label': label, 'action': 'wait', 'duration_ms': 700,
                            'result': {'status': 'done', 'held': [], 'started_monotonic_ms': start,
                                       'completed_monotonic_ms': start + 750}})
            samples.append({'label': label, 'ticks': start + 100, 'held': [], 'left': True,
                            'foreground': True, 'target_foreground': False})
        witness = {'left_presses': 1, 'right_presses': 0, 'motion': 20, 'clicks': 3,
                   'unexpected_keys': 0}
        checks = {'source_unchanged': True, 'external_unchanged': True,
                  'host_exited': True, 'target_exited': True, 'runtime_removed': True}
        return actions, samples, witness, checks

    def assess(self, *args, **kwargs):
        assess = getattr(game, 'assess_concurrency', None)
        self.assertTrue(callable(assess), '正式游戏尚无独立的并发证据判定')
        return assess(*args, **kwargs)

    def test_nonactive_owned_popup_covering_witness_is_rejected(self):
        windows = [
            {'handle': 11, 'pid': 7, 'rect': [50, 50, 150, 150]},
            {'handle': 22, 'pid': 8, 'rect': [0, 0, 200, 200]},
            {'handle': 33, 'pid': 7, 'rect': [0, 0, 200, 200]},
        ]
        inspect = getattr(game, 'window_interference', None)
        self.assertTrue(callable(inspect), '缺少所有目标窗口的遮挡判定')
        self.assertEqual(inspect(windows, 7, 22, 22), {'foreground': False, 'obstructing': [11]})
        self.assertEqual(inspect(windows[1:], 7, 22, 22), {'foreground': False, 'obstructing': []})
        windows[0]['rect'] = [200, 0, 300, 100]
        self.assertEqual(inspect(windows, 7, 22, 11), {'foreground': True, 'obstructing': []})

    def test_real_participation_still_requires_confirmation_and_visual_review(self):
        result = self.assess(*self.evidence())
        self.assertEqual(result['status'], 'checks_passed_pending_participant_confirmation_and_visual_review')
        self.assertFalse(result['physical_verified'])
        self.assertEqual(result['overlap_actions'], 4)
        self.assertEqual(result['settings_overlap_ms'], 6000)

    def test_smoke_cannot_certify_physical_input(self):
        actions, samples, witness, checks = self.evidence()
        witness.update(left_presses=0, motion=0, clicks=0)
        for sample in samples:
            sample['left'] = False
        result = self.assess(actions, samples, witness, checks, smoke=True)
        self.assertEqual(result['status'], 'smoke_passed_no_physical_claim')
        self.assertFalse(result['physical_verified'])

    def test_participation_must_overlap_real_key_hold_and_settings_exposure(self):
        for change in ('no_left', 'stale_sample', 'released_sample', 'short_exposure', 'no_mouse'):
            with self.subTest(change=change):
                actions, samples, witness, checks = self.evidence()
                if change == 'no_left':
                    for row in samples:
                        row['left'] = False
                elif change == 'stale_sample':
                    for row in samples[:4]:
                        row['ticks'] -= 200
                elif change == 'released_sample':
                    for row in samples[:4]:
                        row['held'] = []
                elif change == 'short_exposure':
                    samples = samples[:5]
                else:
                    witness['clicks'] = 0
                self.assertEqual(self.assess(actions, samples, witness, checks)['status'],
                                 'incomplete_participation')

    def test_software_leak_focus_loss_missing_release_or_cleanup_failure_fails(self):
        for change in ('leak', 'right_leak', 'foreground', 'missing_release', 'source', 'runtime'):
            with self.subTest(change=change):
                actions, samples, witness, checks = copy.deepcopy(self.evidence())
                if change == 'leak':
                    witness['unexpected_keys'] = 1
                elif change == 'right_leak':
                    witness['right_presses'] = 1
                elif change == 'foreground':
                    samples[0]['target_foreground'] = True
                elif change == 'missing_release':
                    actions[0]['result']['release_consumed'] = False
                elif change == 'source':
                    checks['source_unchanged'] = False
                else:
                    checks['runtime_removed'] = False
                self.assertEqual(self.assess(actions, samples, witness, checks)['status'], 'failed')

    def test_failed_observation_or_unreleased_wait_cannot_pass(self):
        for field, value in (('status', 'failed'), ('held', ['down'])):
            with self.subTest(field=field):
                actions, samples, witness, checks = self.evidence()
                actions[-1]['result'][field] = value
                self.assertEqual(self.assess(actions, samples, witness, checks)['status'], 'failed')

    def test_empty_or_wait_only_actions_and_empty_samples_cannot_pass_smoke(self):
        actions, samples, witness, checks = self.evidence()
        for subset, rows in (([], samples), (actions[-1:], samples), (actions, [])):
            with self.subTest(actions=len(subset), samples=len(rows)):
                self.assertEqual(self.assess(subset, rows, witness, checks, smoke=True)['status'], 'failed')
