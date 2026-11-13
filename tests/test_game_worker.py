"""受管 worker 在首图之后失去取图能力时也必须结束本批。"""
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from gameqakit import game_worker
from gameqakit.sessions import read_json, write_json


class WorkerObservationTests(unittest.TestCase):
    def test_observation_error_fails_batch_and_reaps_owned_session(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'session'
            request = Path(directory) / 'request.json'
            plan = {'destination': str(root), 'backend': 'native_input', 'fingerprint': 'checked'}
            write_json(request, {'payload': plan, 'request_id': 'request-a'})
            session = mock.Mock()
            session.host.alive.return_value = True
            def prepare():
                root.mkdir()
                write_json(root / 'start.request.json', {'operation': 'start', 'directory': str(root.resolve()),
                           'payload': {'fingerprint': 'checked'}})
            def observation():
                (root / 'stop.request').touch()
                return {'connected': True, 'observation_error': 'capture unavailable'}
            session.prepare.side_effect = prepare
            session.start.return_value = {'state': 'ready_paused'}
            session.bridge.status.side_effect = observation
            session.stop.return_value = {'state': 'stopped', 'target_exited': True}
            with mock.patch('plugins.native_input.session.NativeSession', return_value=session):
                self.assertEqual(game_worker._run(request), 1)
            record = read_json(root / 'session.json')
            self.assertEqual(record['state'], 'failed')
            self.assertIn('observation_failed', record['error'])
            session.stop.assert_called_once()
            session.bridge.resume.assert_not_called()

    def test_disconnection_preserves_cause_and_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'session'
            request = Path(directory) / 'request.json'
            plan = {'destination': str(root), 'backend': 'unity_mono_legacy', 'fingerprint': 'checked'}
            write_json(request, {'payload': plan, 'request_id': 'request-a'})
            session = mock.Mock()
            session.host.alive.return_value = True
            def prepare():
                root.mkdir()
                write_json(root / 'start.request.json', {'operation': 'start', 'directory': str(root.resolve()),
                           'payload': {'fingerprint': 'checked'}})
            session.prepare.side_effect = prepare
            session.start.return_value = {'state': 'ready_paused'}
            session.bridge.status.return_value = {'connected': False, 'paused': True,
                                                  'error': 'status sharing violation'}
            session.stop.return_value = {'state': 'stopped', 'target_exited': True}
            with mock.patch('plugins.unity.session.UnitySession', return_value=session):
                self.assertEqual(game_worker._run(request), 1)
            record = read_json(root / 'session.json')
            self.assertEqual(record['state'], 'failed')
            self.assertIn('status sharing violation', record['error'])
            self.assertTrue(record['target_exited'])
            session.stop.assert_called_once()
            session.bridge.resume.assert_not_called()

    def test_rewired_loading_recovery_is_passive_and_failed_recovery_still_reaps(self):
        for recovered in (True, False):
            with self.subTest(recovered=recovered), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / 'session'
                request = Path(directory) / 'request.json'
                write_json(request, {'payload': {'destination': str(root), 'backend': 'unity_mono_rewired',
                           'fingerprint': 'checked'}, 'request_id': 'loading'})
                session = mock.Mock()
                session.host.alive.return_value = True
                def prepare():
                    root.mkdir()
                    write_json(root / 'start.request.json', {'operation': 'start', 'directory': str(root.resolve()),
                               'payload': {'fingerprint': 'checked'}})
                def observe(cancelled):
                    self.assertFalse(cancelled())
                    if recovered:
                        (root / 'stop.request').touch()
                        return True
                    raise RuntimeError('loading_timeout')
                session.prepare.side_effect = prepare
                session.start.return_value = {'state': 'ready_paused'}
                session.bridge.status.return_value = {'connected': False, 'error': 'heartbeat expired'}
                session.observe_loading.side_effect = observe
                session.stop.return_value = {'state': 'stopped', 'target_exited': True}
                with mock.patch('plugins.unity.session.UnitySession', return_value=session):
                    self.assertEqual(game_worker._run(request), 0 if recovered else 1)
                record = read_json(root / 'session.json')
                if recovered:
                    self.assertEqual(record['loading_observation'], 'recovered_paused')
                else:
                    self.assertIn('loading_timeout', record['error'])
                session.observe_loading.assert_called_once()
                session.stop.assert_called_once()
                session.bridge.resume.assert_not_called()
                session.bridge.action.assert_not_called()

    def test_backend_factory_selects_only_registered_sessions(self):
        from plugins.native_input.session import NativeSession
        from plugins.unity.session import UnitySession
        plan = {'destination': 'E:/DevData/test-session'}
        self.assertIsInstance(game_worker.session_for({**plan, 'backend': 'native_input'}), NativeSession)
        self.assertIsInstance(game_worker.session_for({**plan, 'backend': 'unity_mono_legacy'}), UnitySession)
        with self.assertRaisesRegex(ValueError, 'unsupported_backend'):
            game_worker.session_for({**plan, 'backend': 'unknown'})
