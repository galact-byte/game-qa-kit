"""真实 C# 加载边界与宿主被动观察的安全回归。"""
import os
from pathlib import Path
import runpy
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from gameqakit.sessions import write_json
from plugins.unity.session import UnitySession

ROOT = Path(__file__).resolve().parents[1]


class UnityLoadingTests(unittest.TestCase):
    def test_compiled_loading_window_safety(self):
        if os.name != 'nt':
            self.skipTest('需要 Windows .NET Framework')
        tools = runpy.run_path(str(ROOT / 'plugins/unity/build.py'))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'LoadingWindowTests.exe'
            tools['compile_sources'](output, [ROOT / 'plugins/unity/ControlCore.cs',
                                              ROOT / 'plugins/unity/LoadingWindow.cs',
                                              ROOT / 'tests/unity/LoadingWindowTests.cs'])
            result = subprocess.run([str(output)], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('PASS: Unity bounded loading safety', result.stdout)

    def make_session(self, directory, **signal_changes):
        session = UnitySession({'destination': directory, 'backend': 'unity_mono_rewired'})
        session.host = SimpleNamespace(pid=123, alive=lambda: True)
        session.bridge = mock.Mock()
        meta = dict(protocol=1, session_id='s', pid=123, exe_path='E:/test/game.exe')
        session.bridge._connection.return_value = meta
        signal = dict(schema_version=1, **meta, id='load-1', started_at=100.,
                      start_frame=10, start_observation_seq=4, release_observed=True,
                      release_frame=9, eligible=True, phase='loading',
                      requested=['Common', 'Intro'], pending=['Intro'], completion_frame=0)
        signal.update(signal_changes)
        write_json(Path(directory) / 'bridge/loading.json', signal)
        fresh = dict(connected=True, **meta, frame=21, observation_frame=21, observation_seq=5,
                     paused=True, held=[], release_observed=True, input_ready=True,
                     loading_id='load-1', loading_phase='ready_paused')
        session.bridge.status.return_value = fresh
        return session, signal, fresh

    def test_observation_waits_for_all_scenes_and_fresh_image_without_resuming(self):
        with tempfile.TemporaryDirectory() as directory:
            session, signal, fresh = self.make_session(directory)
            ticks = [100.5]
            def sleep(_):
                ticks[0] += .1
                signal.update(phase='ready_paused', pending=[], completion_frame=20)
                write_json(Path(directory) / 'bridge/loading.json', signal)
            with mock.patch('plugins.unity.session.time.time', side_effect=lambda: ticks[0]), \
                 mock.patch('plugins.unity.session.time.monotonic', side_effect=lambda: ticks[0]), \
                 mock.patch('plugins.unity.session.time.sleep', side_effect=sleep), \
                 mock.patch('plugins.unity.session.validate_ready', return_value={'screenshot': 'verified.png'}) as validate:
                self.assertTrue(session.observe_loading(lambda: False))
            session.bridge.pause.assert_called_once()
            session.bridge.resume.assert_not_called()
            session.bridge.action.assert_not_called()
            validate.assert_called_once()

    def test_image_validation_cannot_outlive_loading_or_restore_cancelled_target(self):
        for failure in ('deadline', 'cancel', 'exit'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                session, _, _ = self.make_session(directory, phase='ready_paused', pending=[], completion_frame=20)
                ticks, cancelled = [100.5], [False]
                def validate(*_):
                    if failure == 'deadline':
                        ticks[0] = 110.
                    elif failure == 'cancel':
                        cancelled[0] = True
                    else:
                        session.host.alive = lambda: False
                    return {'screenshot': 'late.png'}
                expected = {'deadline': 'loading_timeout', 'cancel': 'loading_cancelled',
                            'exit': 'loading_target_exited'}[failure]
                with mock.patch('plugins.unity.session.time.time', side_effect=lambda: ticks[0]), \
                     mock.patch('plugins.unity.session.time.monotonic', side_effect=lambda: ticks[0]), \
                     mock.patch('plugins.unity.session.validate_ready', side_effect=validate):
                    with self.assertRaisesRegex(RuntimeError, expected):
                        session.observe_loading(lambda: cancelled[0])
                session.bridge.resume.assert_not_called()
                session.bridge.action.assert_not_called()

    def test_missing_wrong_identity_unproven_or_old_loading_never_extends_life(self):
        changes = ({'session_id': 'other'}, {'pid': 999}, {'release_observed': False},
                   {'eligible': False}, {'started_at': 80}, {'started_at': float('nan')},
                   {'requested': []}, {'release_frame': 11})
        for change in changes:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                session, _, _ = self.make_session(directory, **change)
                with mock.patch('plugins.unity.session.time.time', return_value=100.5):
                    self.assertFalse(session.observe_loading(lambda: False))
                session.bridge.pause.assert_not_called()

    def test_timeout_and_cancel_stop_without_resume_or_input(self):
        for cancel in (False, True):
            with self.subTest(cancel=cancel), tempfile.TemporaryDirectory() as directory:
                session, _, _ = self.make_session(directory)
                ticks = [100.5]
                def sleep(_): ticks[0] += 1
                with mock.patch('plugins.unity.session.time.time', side_effect=lambda: ticks[0]), \
                     mock.patch('plugins.unity.session.time.monotonic', side_effect=lambda: ticks[0]), \
                     mock.patch('plugins.unity.session.time.sleep', side_effect=sleep):
                    with self.assertRaisesRegex(RuntimeError, 'loading_cancelled' if cancel else 'loading_timeout'):
                        session.observe_loading(lambda: cancel)
                self.assertLessEqual(ticks[0], 110.5)
                session.bridge.resume.assert_not_called()
                session.bridge.action.assert_not_called()

    def test_repeated_observation_cannot_restart_the_fixed_deadline(self):
        with tempfile.TemporaryDirectory() as directory:
            session, _, _ = self.make_session(directory)
            ticks = [100.5]
            with mock.patch('plugins.unity.session.time.time', return_value=100.5), \
                 mock.patch('plugins.unity.session.time.monotonic', side_effect=lambda: ticks[0]), \
                 mock.patch('plugins.unity.session.time.sleep', side_effect=lambda _: ticks.__setitem__(0, ticks[0]+1)):
                with self.assertRaisesRegex(RuntimeError, 'loading_timeout'):
                    session.observe_loading(lambda: False)
                self.assertFalse(session.observe_loading(lambda: False))
            session.bridge.pause.assert_called_once()
            session.bridge.resume.assert_not_called()
            session.bridge.action.assert_not_called()

    def test_target_exit_fatal_and_identity_change_abort_observation(self):
        for failure in ('exit', 'fatal', 'identity'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                session, signal, _ = self.make_session(directory)
                expected = {'exit': 'loading_target_exited', 'fatal': 'loading_bridge_failed',
                            'identity': 'loading_identity_changed'}[failure]
                if failure == 'exit':
                    session.host.alive = lambda: False
                elif failure == 'fatal':
                    write_json(Path(directory) / 'bridge/fatal.json', {'error': 'capture_failed'})
                reads = [signal, {**signal, 'pid': 999 if failure == 'identity' else 123}]
                with mock.patch('plugins.unity.session.time.time', return_value=100.5), \
                     mock.patch('plugins.unity.session.read_json', side_effect=reads):
                    with self.assertRaisesRegex(RuntimeError, expected):
                        session.observe_loading(lambda: False)
                session.bridge.resume.assert_not_called()
                session.bridge.action.assert_not_called()

    def test_stale_queued_image_or_unpaused_recovery_is_rejected(self):
        for change in ({'observation_frame': 20}, {'paused': False}, {'held': ['ok']},
                       {'loading_id': 'other'}, {'observation_seq': 4}):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                session, _, fresh = self.make_session(directory, phase='ready_paused', pending=[], completion_frame=20)
                fresh.update(change)
                ticks = [100.5]
                with mock.patch('plugins.unity.session.time.time', return_value=100.5), \
                     mock.patch('plugins.unity.session.time.monotonic', side_effect=lambda: ticks[0]), \
                     mock.patch('plugins.unity.session.time.sleep', side_effect=lambda _: ticks.__setitem__(0, ticks[0]+1)), \
                     mock.patch('plugins.unity.session.validate_ready') as validate:
                    with self.assertRaisesRegex(RuntimeError, 'loading_timeout'):
                        session.observe_loading(lambda: False)
                validate.assert_not_called()


if __name__ == '__main__':
    unittest.main()
