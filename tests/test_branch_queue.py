"""分支调度的顺序、回收边界与汇总证据。"""
import copy
import hashlib
import json
from pathlib import Path
import unittest

from gameqakit import admission
from tests import test_rgss_admission as fixtures
try:
    from gameqakit import branch_queue
except ImportError:
    branch_queue = None


class LifecyclePeer:
    """替代游戏启动边界；所有调度和文件留证仍使用真实实现。"""
    def __init__(self, log_directory):
        self.directory = Path(log_directory)
        self.state = {'state': 'prepared', 'worker_alive': True, 'can_stop': True}
        self.calls = []
        self.closed = False

    def call(self, name, arguments):
        self.calls.append((name, copy.deepcopy(arguments)))
        if name in ('game_prepare', 'game_start', 'game_stop'):
            if arguments.get('confirm') is not True:
                raise ValueError('confirmation required')
        if name == 'game_prepare':
            self.plan = arguments['plan']
        elif name == 'game_start':
            self.state.update(state='ready_paused', bridge_directory=str(Path(self.plan['destination']) / 'bridge'),
                              session_id=self.directory.name, pid=123, exe_path='fixture.exe',
                              control={'connected': True, 'paused': True, 'held': []})
        elif name == 'game_stop':
            self.state.update(state='stopping')
        elif name != 'game_status':
            raise AssertionError('unexpected tool: ' + name)
        return copy.deepcopy(self.state)

    def stop_confirmed(self, state='stopped'):
        self.state.update(state=state, worker_alive=False, can_stop=False,
                          original_unchanged=True, checkpoint_unchanged=True)

    def close(self):
        self.closed = True
        return True


class BranchQueueTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(branch_queue, '缺少通用分支排队功能')
        fixtures.RgssAdmissionTests.setUp(self)
        self.peers = []
        self.queue = branch_queue.BatchQueue(peer_factory=self.peer)
        self.addCleanup(self.queue.close)

    def peer(self, directory):
        peer = LifecyclePeer(directory)
        self.peers.append(peer)
        return peer

    def plan(self, concurrency=1, branches=3):
        specs = [{'id': letter, 'path': str(self.source), 'artifacts': str(self.artifacts),
                  'destination': str(self.root / ('branch-' + letter))} for letter in 'abc'[:branches]]
        return branch_queue.make_plan(specs, max_active=concurrency, report_directory=str(self.root / 'report'))

    def start(self, concurrency=1, branches=3):
        plan = self.plan(concurrency, branches)
        self.queue.start(plan, confirm=True)
        for _ in range(3):
            report = self.queue.status()
            if any(row['state'] == 'ready' for row in report['branches']):
                return report
        self.fail('分支未进入就绪状态')

    def evidence(self):
        path = self.root / 'review.txt'
        path.write_text('人工核对当前画面', encoding='utf-8')
        return str(path)

    def test_plan_is_read_only_and_retains_distinct_checkpoints(self):
        specs = []
        for name, content in [('a', b'early'), ('b', b'late')]:
            point = self.root / ('point-' + name)
            point.mkdir()
            (point / 'Save01.rvdata2').write_bytes(content)
            specs.append(dict(id=name, path=str(self.source), artifacts=str(self.artifacts),
                              destination=str(self.root / ('branch-' + name)), checkpoint=str(point)))
        before = self.recipe.manifest(self.root)
        plan = branch_queue.make_plan(specs, max_active=2, report_directory=str(self.root / 'report'))
        self.assertEqual(before, self.recipe.manifest(self.root))
        self.assertNotEqual(plan['branches'][0]['plan']['checkpoint'], plan['branches'][1]['plan']['checkpoint'])
        self.assertEqual(plan['fingerprint'], admission.fingerprint({k: v for k, v in plan.items() if k != 'fingerprint'}))

    def test_serial_ready_is_not_passed_and_does_not_start_next(self):
        report = self.start()
        self.assertEqual(len(self.peers), 1)
        self.assertEqual([r['state'] for r in report['branches']], ['ready', 'queued', 'queued'])
        self.assertEqual(report['counts']['passed'], 0)
        self.assertEqual(report['state'], 'running')
        self.assertFalse(any(name.startswith('control_') for name, _ in self.peers[0].calls))

    def test_parallel_slots_are_independent_and_bounded(self):
        report = self.start(2)
        self.assertEqual(len(self.peers), 2)
        self.assertEqual([r['state'] for r in report['branches']], ['ready', 'ready', 'queued'])
        self.assertNotEqual(self.peers[0].directory, self.peers[1].directory)
        self.assertNotEqual(self.peers[0].plan['destination'], self.peers[1].plan['destination'])

    def test_finish_waits_for_worker_exit_before_freeing_slot(self):
        self.start()
        self.queue.finish('a', outcome='passed', evidence=[self.evidence()], note='已审图')
        self.assertEqual(len(self.peers), 1)
        self.assertEqual(self.queue.status()['branches'][0]['state'], 'stopping')
        self.peers[0].state['state'] = 'stopped'
        self.assertEqual(self.queue.status()['branches'][1]['state'], 'queued')
        self.peers[0].stop_confirmed()
        report = self.queue.status()
        self.assertEqual(report['branches'][0]['state'], 'passed')
        self.assertEqual(len(self.peers), 2)
        self.assertTrue(self.peers[0].closed)
        self.assertEqual(report['branches'][0]['evidence'][0]['sha256'],
                         hashlib.sha256(Path(self.evidence()).read_bytes()).hexdigest())

    def test_cleanup_failure_blocks_queue_and_cannot_count_as_pass(self):
        self.start()
        self.queue.finish('a', outcome='passed', evidence=[self.evidence()])
        self.peers[0].stop_confirmed('cleanup_failed')
        report = self.queue.status()
        self.assertEqual(report['state'], 'blocked')
        self.assertEqual(report['branches'][0]['state'], 'cleanup_failed')
        self.assertEqual(report['counts']['passed'], 0)
        self.assertEqual(len(self.peers), 1)

    def test_runtime_failure_remains_failed_and_next_only_after_exit(self):
        self.start()
        self.peers[0].state.update(state='failed', error='fixture crash')
        self.assertEqual(self.queue.status()['branches'][1]['state'], 'queued')
        self.peers[0].stop_confirmed('failed')
        report = self.queue.status()
        self.assertEqual(report['branches'][0]['state'], 'failed')
        self.assertEqual(len(self.peers), 2)

    def test_cancel_never_starts_queued_branches_and_records_cleanup(self):
        self.start(2)
        self.queue.cancel()
        self.peers[0].stop_confirmed()
        self.peers[1].stop_confirmed()
        report = self.queue.status()
        self.assertEqual(report['state'], 'cancelled')
        self.assertEqual([r['state'] for r in report['branches']], ['cancelled'] * 3)
        self.assertEqual(len(self.peers), 2)

    def test_missing_or_changed_evidence_cannot_finish_or_stop(self):
        self.start()
        with self.assertRaises((ValueError, FileNotFoundError)):
            self.queue.finish('a', outcome='passed', evidence=[str(self.root / 'missing')])
        with self.assertRaises(ValueError):
            self.queue.finish('a', outcome='passed', evidence=[])
        self.assertFalse(any(name == 'game_stop' for name, _ in self.peers[0].calls))

    def test_conflicting_completion_cannot_overwrite_original_judgment(self):
        self.start()
        evidence = [self.evidence()]
        self.queue.finish('a', outcome='failed', evidence=evidence, note='真实失败')
        self.queue.finish('a', outcome='failed', evidence=evidence, note='真实失败')
        with self.assertRaises(ValueError):
            self.queue.finish('a', outcome='passed', evidence=evidence)
        self.assertEqual(sum(name == 'game_stop' for name, _ in self.peers[0].calls), 1)

    def test_changed_batch_digest_and_unconfirmed_start_have_no_side_effect(self):
        plan = self.plan()
        with self.assertRaises(ValueError):
            self.queue.start(plan, confirm=False)
        with self.assertRaises(ValueError):
            self.queue.start({**plan, 'max_active': 2}, confirm=True)
        self.assertEqual(self.peers, [])
        self.assertFalse((self.root / 'report').exists())

    def test_batch_rejects_duplicate_ids_and_any_output_overlap(self):
        plan = self.plan()
        specs = [dict(id=r['id'], path=r['plan']['source'], artifacts=r['plan']['artifacts'],
                      destination=r['plan']['destination']) for r in plan['branches']]
        for change in ('id', 'destination', 'nested', 'report', 'source'):
            rows = copy.deepcopy(specs)
            report = str(self.root / 'report')
            if change == 'id': rows[1]['id'] = rows[0]['id']
            elif change == 'destination': rows[1]['destination'] = rows[0]['destination']
            elif change == 'nested': rows[1]['destination'] = rows[0]['destination'] + '/nested'
            elif change == 'report': report = rows[0]['destination'] + '/report'
            else: report = str(self.source / 'report')
            with self.subTest(change=change), self.assertRaises(ValueError):
                branch_queue.make_plan(rows, max_active=2, report_directory=report)

    def test_saved_running_report_is_unverified_without_creator_and_cannot_resume(self):
        self.start()
        report = branch_queue.read_report(self.root / 'report')
        self.assertFalse(report['live'])
        self.assertFalse(report['can_control'])
        self.assertEqual(report['branches'][0]['state'], 'interrupted_unverified')
        with self.assertRaises(ValueError):
            branch_queue.BatchQueue(peer_factory=self.peer).start(self.queue.plan, confirm=True)

    def test_evidence_changed_during_cleanup_cannot_count_as_passed(self):
        self.start()
        evidence = self.evidence()
        self.queue.finish('a', outcome='passed', evidence=[evidence])
        Path(evidence).write_text('已替换的证据', encoding='utf-8')
        self.peers[0].stop_confirmed()
        report = self.queue.status()
        self.assertEqual(report['branches'][0]['state'], 'unverified')
        self.assertFalse(report['branches'][0]['evidence_intact'])
        self.assertEqual(report['branches'][0]['judgment']['outcome'], 'passed')
        self.assertEqual(report['counts']['passed'], 0)

    def test_creator_close_verifies_cleanup_and_never_starts_queued_work(self):
        self.start()
        peer = self.peers[0]
        def close_peer():
            peer.stop_confirmed()
            from gameqakit.sessions import write_json
            directory = Path(peer.plan['destination'])
            directory.mkdir()
            write_json(directory / 'session.json', {'schema_version': 1, **peer.state})
            peer.closed = True
            return True
        peer.close = close_peer
        self.queue.close()
        report = branch_queue.read_report(self.root / 'report')
        self.assertEqual(report['state'], 'cancelled')
        self.assertTrue(report['branches'][0]['cleanup_verified'])
        self.assertEqual(report['branches'][0]['state'], 'cancelled')
        self.assertEqual(len(self.peers), 1)

    def test_close_preserves_already_completed_batch_judgment(self):
        self.start(branches=1)
        self.queue.finish('a', outcome='failed', evidence=[], note='失败')
        self.peers[0].stop_confirmed()
        self.assertEqual(self.queue.status()['state'], 'completed')
        self.queue.close()
        self.assertEqual(branch_queue.read_report(self.root / 'report')['state'], 'completed')

    def test_changed_original_cannot_release_next_slot(self):
        self.start()
        self.peers[0].stop_confirmed()
        self.peers[0].state['source_unchanged'] = False
        report = self.queue.status()
        self.assertEqual(report['state'], 'blocked')
        self.assertEqual(report['branches'][0]['state'], 'cleanup_failed')
        self.assertEqual(len(self.peers), 1)

    def test_unconfirmed_release_cannot_release_next_slot(self):
        self.start()
        self.peers[0].stop_confirmed()
        self.peers[0].state['release_confirmed'] = False
        self.assertEqual(self.queue.status()['state'], 'blocked')
        self.assertEqual(len(self.peers), 1)

    def test_unconfirmed_target_exit_cannot_release_next_slot(self):
        self.start()
        self.peers[0].stop_confirmed()
        self.peers[0].state['target_exited'] = False
        report = self.queue.status()
        self.assertEqual(report['state'], 'blocked')
        self.assertEqual(report['branches'][0]['state'], 'cleanup_failed')
        self.assertEqual(len(self.peers), 1)

    def test_eof_blocks_new_work_and_preserves_unverified_branch(self):
        self.start()
        def broken(*_): raise EOFError('peer disappeared')
        self.peers[0].call = broken
        report = self.queue.status()
        self.assertEqual(report['state'], 'blocked')
        self.assertEqual(report['branches'][0]['state'], 'interrupted_unverified')
        self.assertEqual(len(self.peers), 1)


if __name__ == '__main__':
    unittest.main()
