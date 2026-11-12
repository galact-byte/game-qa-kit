"""通用接入 CLI 的只读检查、确认和创建者生命周期。"""
import contextlib
import io
import json
import unittest
from unittest import mock

from gameqakit import cli


class AdmissionCliTests(unittest.TestCase):
    def invoke(self, args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), mock.patch.object(cli, '_resolve', side_effect=AssertionError('不需要人工配置')):
            code = cli.main(['game', *args])
        return code, output.getvalue()

    def test_inspect_needs_no_profile_or_worker(self):
        with mock.patch('gameqakit.admission.inspect', return_value={'launch_readiness': 'blocked'}) as inspect:
            code, output = self.invoke(['inspect', 'source'])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)['launch_readiness'], 'blocked')
        inspect.assert_called_once_with('source', artifacts=None)

    def test_inspect_with_destination_returns_reviewable_plan(self):
        with mock.patch('gameqakit.admission.make_plan', return_value={'fingerprint': 'digest'}):
            code, output = self.invoke(['inspect', 'source', '--destination', 'session', '--artifacts', 'build'])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)['fingerprint'], 'digest')

    def test_start_without_confirmation_does_not_prepare(self):
        with mock.patch('gameqakit.game_session.Manager') as manager:
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                self.invoke(['start', 'source', '--destination', 'session', '--artifacts', 'build', '--fingerprint', 'digest'])
        manager.assert_not_called()

    def test_changed_digest_does_not_prepare(self):
        with mock.patch('gameqakit.admission.make_plan', return_value={'fingerprint': 'new'}), mock.patch('gameqakit.game_session.Manager') as manager:
            with self.assertRaisesRegex(ValueError, '摘要'):
                self.invoke(['start', 'source', '--destination', 'session', '--artifacts', 'build', '--fingerprint', 'old', '--confirm'])
        manager.assert_not_called()

    def test_creator_remains_alive_until_interrupt_and_closes_worker(self):
        manager = mock.Mock()
        manager.prepare.return_value = {'state': 'preparing'}
        manager.start.return_value = {'state': 'starting'}
        manager.status.side_effect = [{'state': 'prepared'}, {'state': 'ready_paused'}, KeyboardInterrupt]
        with mock.patch('gameqakit.admission.make_plan', return_value={'fingerprint': 'digest'}), mock.patch('gameqakit.game_session.Manager', return_value=manager), mock.patch('time.sleep'):
            code, output = self.invoke(['start', 'source', '--destination', 'session', '--artifacts', 'build', '--fingerprint', 'digest', '--confirm'])
        self.assertEqual(code, 0)
        self.assertIn('ready_paused', output)
        manager.start.assert_called_once()
        manager.close.assert_called_once()

    def test_status_has_no_stop_or_start_side_effect(self):
        manager = mock.Mock()
        manager.status.return_value = {'state': 'interrupted_unverified', 'can_stop': False}
        with mock.patch('gameqakit.game_session.Manager', return_value=manager):
            code, output = self.invoke(['status', 'session'])
        self.assertEqual(code, 0)
        self.assertFalse(json.loads(output)['can_stop'])
        manager.start.assert_not_called()
        manager.stop.assert_not_called()


if __name__ == '__main__':
    unittest.main()
