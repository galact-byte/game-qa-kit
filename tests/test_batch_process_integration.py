"""用真实 stdio/Manager/Job 与替身目标核验排队及创建者退出，不运行游戏。"""
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

import psutil

from gameqakit import admission
from gameqakit.mcp_client import McpProcess
from gameqakit.sessions import read_json, write_json

FIXTURE = Path(__file__).with_name('batch_fixture.py')


@unittest.skipUnless(os.name == 'nt', '真实 Job 进程回收仅在 Windows 验证')
class BatchProcessIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.clients, self.identities = [], []
        self.source = self.root / 'original'
        self.source.mkdir()
        (self.source / 'original.txt').write_text('untouched', encoding='utf-8')

    def tearDown(self):
        for client in reversed(self.clients):
            client.close()
        self.assertTrue(self.wait(lambda: all(not self.alive(p) for p in self.identities)),
                        '本测试自有进程未回收')
        self.assertEqual((self.source / 'original.txt').read_text(encoding='utf-8'), 'untouched')
        self.temp.cleanup()

    @staticmethod
    def wait(predicate, seconds=12):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            result = predicate()
            if result:
                return result
            time.sleep(.03)
        return False

    @staticmethod
    def alive(identity):
        try:
            p = psutil.Process(identity['pid'])
            return p.create_time() == identity['created'] and p.exe() == identity['exe']
        except psutil.NoSuchProcess:
            return False

    def remember(self, pid):
        p = psutil.Process(pid)
        identity = dict(pid=pid, created=p.create_time(), exe=p.exe())
        self.identities.append(identity)
        return identity

    def setup_batch(self, *, limit=1, count=2, fail_first=False):
        branches = []
        for i in range(count):
            child = dict(schema_version=1, backend='rgss3', source=str(self.source),
                         artifacts=str(self.source), destination=str(self.root / f'branch-{i}'),
                         source_sha256=admission.file_digest(self.source / 'original.txt'),
                         fixture_fail=fail_first and i == 0)
            child['fingerprint'] = admission.fingerprint(child)
            branches.append(dict(id=str(i), plan=child))
        plan = dict(schema_version=1, branches=branches, max_active=limit,
                    report_directory=str(self.root / 'report'))
        plan['fingerprint'] = admission.fingerprint(plan)
        (self.root / 'creator').mkdir()
        client = McpProcess(self.root / 'creator', command=[sys.executable, '-u', '-B', str(FIXTURE), 'queue'])
        self.clients.append(client)
        self.remember(client.process.pid)
        report = client.call('batch_start', dict(plan=plan, confirm=True))
        return client, report

    def until_state(self, client, branch, state):
        def check():
            report = client.call('batch_status', {})
            return report if report['branches'][branch]['state'] == state else False
        result = self.wait(check)
        self.assertTrue(result, f'分支 {branch} 未到 {state}；最终报告：{client.call("batch_status", {})}')
        return result

    def remember_branch(self, report, branch):
        row = report['branches'][branch]
        self.remember(row['session']['worker_pid'])
        self.remember(row['session']['pid'])
        # 每支单独 MCP，进程身份取自其审计记录。
        peer_record = read_json(self.root / 'report' / str(branch) / 'mcp-process.json')
        self.remember(peer_record['pid'])

    def test_serial_waits_for_real_worker_and_target_exit_then_records_evidence(self):
        client, report = self.setup_batch()
        report = self.until_state(client, 0, 'ready')
        self.assertEqual(report['branches'][1]['state'], 'queued')
        self.remember_branch(report, 0)
        first = list(self.identities[1:])
        evidence = self.root / 'review.txt'
        evidence.write_text('reviewed fixture result', encoding='utf-8')
        client.call('batch_finish', dict(branch_id='0', outcome='passed', evidence=[str(evidence)], confirm=True))
        report = self.until_state(client, 1, 'ready')
        self.assertTrue(all(not self.alive(p) for p in first))
        self.assertTrue(report['branches'][0]['cleanup_verified'])
        self.assertEqual(report['branches'][0]['state'], 'passed')
        self.remember_branch(report, 1)
        client.call('batch_cancel', dict(confirm=True))
        report = self.until_state(client, 1, 'cancelled')
        self.assertEqual(report['counts']['passed'], 1)
        self.assertTrue(client.close())
        saved = read_json(self.root / 'report' / 'summary.json')
        self.assertFalse(saved['live'])
        self.assertTrue(all(row['cleanup_verified'] for row in saved['branches']))

    def test_parallel_creator_eof_reaps_both_targets_without_starting_queued_branch(self):
        client, _ = self.setup_batch(limit=2, count=3)
        report = self.until_state(client, 0, 'ready')
        report = self.until_state(client, 1, 'ready')
        self.assertEqual(report['branches'][2]['state'], 'queued')
        self.remember_branch(report, 0)
        self.remember_branch(report, 1)
        self.assertTrue(client.close())
        self.assertTrue(self.wait(lambda: all(not self.alive(p) for p in self.identities)))
        saved = read_json(self.root / 'report' / 'summary.json')
        self.assertEqual(saved['state'], 'cancelled')
        self.assertEqual(saved['counts']['cancelled'], 3)
        self.assertTrue(all(row['cleanup_verified'] for row in saved['branches'][:2]))
        self.assertFalse((self.root / 'branch-2').exists())

    def test_failed_start_is_preserved_and_queue_advances_after_worker_exit(self):
        client, _ = self.setup_batch(fail_first=True)
        report = self.until_state(client, 1, 'ready')
        self.assertEqual(report['branches'][0]['state'], 'failed')
        self.assertTrue(report['branches'][0]['cleanup_verified'])
        self.assertIn('fixture launch failed', report['branches'][0]['session']['error'])
        self.remember_branch(report, 1)
        self.assertTrue(client.close())

    def start_fault_batch(self):
        client, _ = self.setup_batch(limit=2, count=3)
        report = self.until_state(client, 0, 'ready')
        report = self.until_state(client, 1, 'ready')
        self.remember_branch(report, 0)
        first = self.identities[-3:]
        self.remember_branch(report, 1)
        healthy = self.identities[-3:]
        return client, report, first, healthy

    def test_lease_pause_keeps_slot_until_explicit_finish(self):
        client, _, first, healthy = self.start_fault_batch()
        fault = self.root / 'branch-0' / 'fixture-control.json'
        write_json(fault, {'paused': True, 'error': 'lease_expired'})
        observed = self.root / 'branch-0' / 'fixture-observed.json'
        self.assertTrue(self.wait(lambda: observed.exists()
                                  and read_json(observed).get('error') == 'lease_expired'))
        self.assertEqual(read_json(observed)['held'], [])
        report = client.call('batch_status', {})
        self.assertEqual(report['branches'][0]['state'], 'ready')
        self.assertEqual(report['branches'][2]['state'], 'queued')
        self.assertFalse((self.root / 'branch-2').exists())
        self.assertTrue(all(self.alive(p) for p in first + healthy))
        client.call('batch_finish', dict(branch_id='0', outcome='unverified',
                                        evidence=[], confirm=True))
        report = self.until_state(client, 2, 'ready')
        self.remember_branch(report, 2)
        self.assertEqual(report['branches'][0]['state'], 'unverified')
        self.assertTrue(report['branches'][0]['cleanup_verified'])
        self.assertTrue(all(not self.alive(p) for p in first))
        self.assertTrue(all(self.alive(p) for p in healthy))
        self.assertEqual(report['branches'][1]['state'], 'ready')

    def test_capture_failure_reaps_branch_before_starting_replacement(self):
        client, _, first, healthy = self.start_fault_batch()
        write_json(self.root / 'branch-0' / 'fixture-control.json',
                   {'observation_error': 'capture unavailable'})
        report = self.until_state(client, 2, 'ready')
        self.remember_branch(report, 2)
        failed = report['branches'][0]
        self.assertEqual(failed['state'], 'failed')
        self.assertIn('observation_failed', failed['session']['error'])
        self.assertTrue(failed['cleanup_verified'])
        self.assertTrue(failed['session']['release_confirmed'])
        self.assertTrue(failed['session']['target_exited'])
        self.assertFalse(failed['session']['worker_alive'])
        self.assertTrue(all(not self.alive(p) for p in first))
        self.assertTrue(all(self.alive(p) for p in healthy))
        self.assertEqual(report['branches'][1]['state'], 'ready')
        self.assertTrue((self.root / 'branch-0' / 'session.json').is_file())

    def test_capture_failure_without_release_confirmation_blocks_replacement(self):
        client, _, first, healthy = self.start_fault_batch()
        write_json(self.root / 'branch-0' / 'fixture-control.json',
                   {'observation_error': 'capture unavailable', 'release_confirmed': False})
        report = self.until_state(client, 0, 'cleanup_failed')
        self.assertEqual(report['state'], 'blocked')
        failed = report['branches'][0]
        self.assertFalse(failed['cleanup_verified'])
        self.assertIn('observation_failed', failed['session']['error'])
        self.assertFalse(failed['session']['release_confirmed'])
        self.assertTrue(failed['session']['target_exited'])
        self.assertFalse(failed['session']['worker_alive'])
        self.assertTrue(all(not self.alive(p) for p in first))
        self.assertTrue(all(self.alive(p) for p in healthy))
        self.assertEqual(report['branches'][1]['state'], 'ready')
        self.assertEqual(report['branches'][2]['state'], 'queued')
        self.assertFalse((self.root / 'branch-2').exists())
        client.call('batch_finish', dict(branch_id='1', outcome='unverified',
                                        evidence=[], confirm=True))
        report = self.until_state(client, 1, 'unverified')
        self.assertEqual(report['state'], 'blocked')
        self.assertTrue(report['branches'][1]['cleanup_verified'])
        self.assertTrue(all(not self.alive(p) for p in healthy))
        self.assertEqual(report['branches'][2]['state'], 'queued')
        self.assertFalse((self.root / 'branch-2').exists())
        self.assertTrue((self.root / 'branch-0' / 'session.json').is_file())

    def test_abrupt_creator_exit_reaps_owned_process_tree_and_report_stays_unverified(self):
        client, _ = self.setup_batch(count=1)
        report = self.until_state(client, 0, 'ready')
        self.remember_branch(report, 0)
        identity = self.identities[0]
        self.assertTrue(self.alive(identity))
        # 只结束刚核对三元身份的本测试创建者，验证真实 Job 级联回收。
        process = psutil.Process(identity['pid'])
        self.assertEqual(process.create_time(), identity['created'])
        self.assertEqual(process.exe(), identity['exe'])
        process.kill()
        process.wait(timeout=5)
        self.assertTrue(self.wait(lambda: all(not self.alive(p) for p in self.identities)))
        from gameqakit.branch_queue import read_report
        saved = read_report(self.root / 'report')
        self.assertEqual(saved['state'], 'interrupted_unverified')
        self.assertFalse(saved['live'])
        self.assertEqual(saved['counts']['passed'], 0)


if __name__ == '__main__':
    unittest.main()
