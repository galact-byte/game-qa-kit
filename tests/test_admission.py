"""接入只读检查与显式绑定的行为边界。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from gameqakit import admission, game_session
from gameqakit.sessions import write_json


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'game'
        self.source.mkdir()

    def test_unknown_is_observation_only_and_inspection_writes_nothing(self):
        before = list(self.root.rglob('*'))
        result = admission.inspect(self.source)
        self.assertEqual(result['launch_readiness'], 'unknown')
        self.assertFalse(result['control_verified'])
        self.assertEqual(before, list(self.root.rglob('*')))
        self.assertTrue(result['manual_observation'])

    def test_known_engine_without_recipe_is_explicitly_blocked(self):
        (self.source / 'Game.ini').write_text('[Game]\nLibrary=RGSS103.dll\n', encoding='utf-8')
        result = admission.inspect(self.source)
        self.assertEqual(result['engine'], 'rpgmaker_xp')
        self.assertEqual(result['launch_readiness'], 'not_migrated')
        self.assertIn('recipe_unavailable', [x['code'] for x in result['blockers']])

    def test_plan_rejects_existing_and_overlapping_destination(self):
        for target in (self.source, self.source / 'session', self.root):
            with self.subTest(target=target), self.assertRaises(ValueError):
                admission.validate_destination(self.source, target)
        admission.validate_destination(self.source, self.root / 'new-session')

    def test_plan_fingerprint_is_deterministic_and_changes_with_content(self):
        self.assertEqual(admission.fingerprint({'b': 2, 'a': 1}), admission.fingerprint({'a': 1, 'b': 2}))
        self.assertNotEqual(admission.fingerprint({'a': 1}), admission.fingerprint({'a': 2}))


class BindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.exe = self.root / 'Game.exe'
        self.exe.touch()
        self.bridge = self.root / 'bridge'
        self.meta = {'protocol': 1, 'session_id': 'session-a', 'pid': 123, 'exe_path': str(self.exe)}
        write_json(self.bridge / 'connection.json', self.meta)
        self.state = {**self.meta, 'connected': True, 'paused': True, 'held': [], 'frame': 1,
                      'capabilities': ['move', 'wait']}
        self.client = mock.Mock()
        self.client.directory = self.bridge
        self.client.status.side_effect = lambda: dict(self.state)
        self.patch = mock.patch('gameqakit.game_session.BridgeClient', return_value=self.client)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.pid_patch = mock.patch('gameqakit.win32._pid_path', return_value=str(self.exe))
        self.pid_patch.start()
        self.addCleanup(self.pid_patch.stop)
        self.binding = game_session.Binding()
        self.addCleanup(self.binding.close)

    def test_bind_checks_session_without_sending_input_and_carries_target(self):
        with self.assertRaisesRegex(ValueError, 'session'):
            self.binding.bind(self.bridge, 'wrong')
        result = self.binding.bind(self.bridge, 'session-a')
        self.assertTrue(result['can_control'])
        self.assertEqual(self.binding.profile.target_pid, 123)
        self.assertEqual(self.binding.profile.exe_path, str(self.exe))
        self.client.resume.assert_not_called()
        self.client.pause.assert_not_called()

    def test_observer_binding_never_claims_action_lease(self):
        state = self.binding.bind(self.bridge, 'session-a', observe_only=True)
        self.assertFalse(state['can_control'])
        controller = game_session.Binding()
        self.addCleanup(controller.close)
        self.assertTrue(controller.bind(self.bridge, 'session-a')['can_control'])
        self.client.resume.assert_not_called()
        self.client.pause.assert_not_called()

    def test_failed_observation_invalidates_binding_and_blocks_actions(self):
        self.binding.bind(self.bridge, 'session-a')
        self.state['observation_error'] = 'capture unavailable'
        state = self.binding.status()
        self.assertFalse(state['connected'])
        self.assertFalse(state['can_control'])
        with self.assertRaises(RuntimeError):
            self.binding.execute('resume', {})
        self.client.resume.assert_not_called()

    def test_failed_observation_cannot_be_bound_as_ready(self):
        self.state['observation_error'] = 'capture unavailable'
        with self.assertRaisesRegex(RuntimeError, '取图'):
            self.binding.bind(self.bridge, 'session-a')
        self.assertIsNone(self.binding.client)

    def test_second_client_can_observe_and_pause_but_cannot_control(self):
        self.binding.bind(self.bridge, 'session-a')
        other = game_session.Binding()
        self.addCleanup(other.close)
        self.assertFalse(other.bind(self.bridge, 'session-a')['can_control'])
        other.client_for({}, write=False).pause()
        with self.assertRaisesRegex(RuntimeError, '控制权'):
            other.client_for({}, write=True)

    def test_cross_directory_and_rebinding_are_rejected(self):
        self.binding.bind(self.bridge, 'session-a')
        with self.assertRaisesRegex(RuntimeError, '解绑'):
            self.binding.bind(self.bridge, 'session-a')
        with self.assertRaisesRegex(ValueError, 'directory'):
            self.binding.client_for({'directory': str(self.root / 'elsewhere')})

    def test_identity_loss_stays_invalid_even_if_a_new_bridge_appears(self):
        self.binding.bind(self.bridge, 'session-a')
        self.state['session_id'] = 'session-b'
        with self.assertRaisesRegex(RuntimeError, '失效'):
            self.binding.client_for({})
        self.state['session_id'] = 'session-a'
        with self.assertRaisesRegex(RuntimeError, '失效'):
            self.binding.client_for({})
        self.assertTrue(self.binding.engaged)

    def test_unbind_requires_confirmed_release_and_keeps_no_target_guard(self):
        self.binding.bind(self.bridge, 'session-a')
        self.state.update(paused=False, held=['ok'])
        with self.assertRaisesRegex(RuntimeError, '松键'):
            self.binding.unbind(timeout=0.02)
        self.assertTrue(self.binding.engaged)
        self.state.update(paused=True, held=[])
        result = self.binding.unbind()
        self.assertFalse(result['bound'])
        self.assertTrue(self.binding.engaged)
        with self.assertRaisesRegex(RuntimeError, '目标'):
            self.binding.client_for({})

    def test_live_disconnect_does_not_count_as_release(self):
        self.binding.bind(self.bridge, 'session-a')
        self.state.update(connected=False)
        with mock.patch('gameqakit.win32._pid_path', return_value=str(self.exe)):
            with self.assertRaisesRegex(RuntimeError, '松键'):
                self.binding.unbind(timeout=0.02)
        self.assertTrue(self.binding.engaged)


if __name__ == '__main__':
    unittest.main()
