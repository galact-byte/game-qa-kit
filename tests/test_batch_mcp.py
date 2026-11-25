"""通过公开 MCP 和 CLI 路径验证批次准入与生命周期。"""
import contextlib
import io
import json
from pathlib import Path
import unittest
from unittest import mock

from gameqakit import branch_queue, cli, mcp_server as server
from tests import test_branch_queue as fixtures
from tests import test_rgss_admission as admission_fixtures


class BatchMcpTests(unittest.TestCase):
    def setUp(self):
        admission_fixtures.RgssAdmissionTests.setUp(self)
        self.peers = []
        def peer(directory):
            child = fixtures.LifecyclePeer(directory)
            self.peers.append(child)
            return child
        self.queue = branch_queue.BatchQueue(peer_factory=peer)
        self.addCleanup(self.queue.close)
        self.patch = mock.patch.object(server, '_BATCH', self.queue, create=True)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.specs = [dict(id=name, path=str(self.source), artifacts=str(self.artifacts),
                           destination=str(self.root / ('branch-' + name))) for name in 'ab']

    def call(self, name, **arguments):
        response = server.handle('tools/call', {'name': name, 'arguments': arguments})
        return json.loads(response['content'][0]['text'])

    def inspect(self):
        return self.call('batch_inspect', branches=self.specs, max_active=1,
                         report_directory=str(self.root / 'report'))

    def test_public_inspect_is_read_only_without_a_profile(self):
        before = self.recipe.manifest(self.root)
        with mock.patch.object(server, 'P', side_effect=AssertionError('不应采用人工目标')):
            plan = self.inspect()
        self.assertEqual(len(plan['branches']), 2)
        self.assertEqual(before, self.recipe.manifest(self.root))
        self.assertFalse((self.root / 'report').exists())

    def test_start_and_finish_require_explicit_confirmation(self):
        plan = self.inspect()
        for name, args in [('batch_start', dict(plan=plan)),
                           ('batch_finish', dict(branch_id='a', outcome='passed', evidence=[]))]:
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, '确认|confirm'):
                self.call(name, **args)
        self.assertFalse((self.root / 'report').exists())
        self.assertEqual(self.peers, [])

    def test_public_completion_preserves_failure_and_only_advances_after_cleanup(self):
        self.call('batch_start', plan=self.inspect(), confirm=True)
        for _ in range(3):
            state = self.call('batch_status')
            if state['branches'][0]['state'] == 'ready':
                break
        self.assertEqual(state['counts']['passed'], 0)
        self.call('batch_finish', branch_id='a', outcome='failed', evidence=[], note='未响应', confirm=True)
        self.assertEqual(len(self.peers), 1)
        self.peers[0].stop_confirmed()
        state = self.call('batch_status')
        self.assertEqual(state['branches'][0]['state'], 'failed')
        self.assertEqual(len(self.peers), 2)
        state = self.call('batch_cancel')
        self.assertEqual(state['branches'][1]['state'], 'stopping')
        self.peers[1].stop_confirmed()
        state = self.call('batch_status')
        self.assertEqual(state['counts']['failed'], 1)
        self.assertEqual(state['counts']['cancelled'], 1)

    def test_offline_status_cannot_resume_or_claim_live_creator(self):
        self.call('batch_start', plan=self.inspect(), confirm=True)
        self.call('batch_status')
        # 单独 MCP 才采用磁盘汇总；本 MCP 继续持有活动创建者。
        with mock.patch.object(server, '_BATCH', branch_queue.BatchQueue()):
            report = self.call('batch_status', directory=str(self.root / 'report'))
        self.assertFalse(report['live'])
        self.assertFalse(report['can_control'])
        self.assertEqual(report['state'], 'interrupted_unverified')
        self.assertEqual(len(self.peers), 1)

    def test_cli_read_only_report_does_not_require_profile(self):
        self.call('batch_start', plan=self.inspect(), confirm=True)
        output = io.StringIO()
        with mock.patch.object(cli, '_resolve', side_effect=AssertionError('无需 profile')), contextlib.redirect_stdout(output):
            code = cli.main(['batch', 'report', str(self.root / 'report')])
        self.assertEqual(code, 0)
        self.assertFalse(json.loads(output.getvalue())['live'])
        self.assertEqual(len(self.peers), 1)


if __name__ == '__main__':
    unittest.main()
