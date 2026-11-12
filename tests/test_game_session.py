"""创建者请求去重、归属和崩溃后只读恢复检查。"""
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from gameqakit import game_session as module
from gameqakit.sessions import write_json


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.plan = {'destination': str(self.root / 'session'), 'fingerprint': 'a' * 64}
        self.revalidate = mock.patch('gameqakit.admission.revalidate_plan', side_effect=lambda plan: plan)
        self.revalidate.start()
        self.addCleanup(self.revalidate.stop)
        self.process = mock.patch('gameqakit.process_job.OwnedProcess')
        self.factory = self.process.start()
        self.addCleanup(self.process.stop)
        self.factory.return_value.alive.return_value = True
        self.manager = module.Manager()

    def test_prepare_requires_specific_confirmation_and_replays_original_worker(self):
        with self.assertRaises(ValueError):
            self.manager.prepare(self.plan, request_id='prepare-1', confirm=False)
        self.factory.assert_not_called()
        first = self.manager.prepare(self.plan, request_id='prepare-1', confirm=True)
        replay = self.manager.prepare(self.plan, request_id='prepare-1', confirm=True)
        self.assertEqual(first['directory'], replay['directory'])
        self.assertEqual(self.factory.call_count, 1)
        self.assertEqual(self.factory.return_value.run.call_count, 1)
        changed = {**self.plan, 'fingerprint': 'b' * 64}
        with self.assertRaisesRegex(ValueError, 'request'):
            self.manager.prepare(changed, request_id='prepare-1', confirm=True)

    def test_new_creator_does_not_adopt_recorded_pid_or_restart_old_request(self):
        self.manager.prepare(self.plan, request_id='prepare-2', confirm=True)
        other = module.Manager()
        replay = other.prepare(self.plan, request_id='prepare-2', confirm=True)
        self.assertFalse(replay['can_stop'])
        self.assertEqual(self.factory.call_count, 1)
        directory = Path(self.plan['destination'])
        write_json(directory / 'session.json', {'schema_version': 1, 'state': 'ready_paused', 'pid': 123})
        with self.assertRaisesRegex(RuntimeError, '创建者'):
            other.stop(directory, request_id='stop-external')
        self.factory.return_value.close.assert_not_called()

    def test_external_status_tracks_worker_lease_without_getting_stop_rights(self):
        from gameqakit.sessions import SessionLease
        directory = Path(self.plan['destination'])
        write_json(directory / 'session.json', {'schema_version': 1, 'state': 'prepared', 'worker_pid': 123})
        observer = module.Manager()
        lease_path = directory.parent / '.admission-requests' / (directory.name + '.lifecycle.lock')
        with SessionLease(lease_path):
            current = observer.status(directory)
            self.assertEqual(current['state'], 'prepared')
            self.assertTrue(current['worker_alive'])
            self.assertFalse(current['can_stop'])
            self.assertFalse(current['owned_by_this_creator'])
            with self.assertRaisesRegex(RuntimeError, '创建者'):
                observer.stop(directory, request_id='observer-stop')
        stale = observer.status(directory)
        self.assertEqual(stale['state'], 'interrupted_unverified')
        self.assertFalse(stale['worker_alive'])

    def test_observation_failure_is_reported_lost_before_worker_reaps_target(self):
        self.manager.prepare(self.plan, request_id='prepare-bad-image', confirm=True)
        root = Path(self.plan['destination'])
        write_json(root / 'session.json', {'schema_version': 1, 'state': 'ready_paused',
                                          'bridge_directory': str(root / 'bridge')})
        with mock.patch('gameqakit.game_session.BridgeClient') as client:
            client.return_value.status.return_value = {'connected': True, 'paused': True,
                                                        'observation_error': 'capture unavailable'}
            state = self.manager.status(root)
        self.assertEqual(state['state'], 'connection_lost')
        self.assertFalse(state['control']['connected'])
        self.assertTrue(state['can_stop'])

    def test_start_requires_prepared_state_and_exact_approved_fingerprint(self):
        self.manager.prepare(self.plan, request_id='prepare-3', confirm=True)
        root = Path(self.plan['destination'])
        write_json(root / 'session.json', {'schema_version': 1, 'state': 'prepared'})
        with self.assertRaises(ValueError):
            self.manager.start(root, request_id='start-1', fingerprint='other', confirm=True)
        self.assertFalse((root / 'start.request.json').exists())
        result = self.manager.start(root, request_id='start-1', fingerprint='a' * 64, confirm=True)
        self.assertEqual(result['state'], 'starting')
        stamp = (root / 'start.request.json').stat().st_mtime_ns
        self.manager.start(root, request_id='start-1', fingerprint='a' * 64, confirm=True)
        self.assertEqual((root / 'start.request.json').stat().st_mtime_ns, stamp)


if __name__ == '__main__':
    unittest.main()
