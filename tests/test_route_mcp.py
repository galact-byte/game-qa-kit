"""通过真实工具路由完成具名路线记录，不允许绕过确认或误切目标。"""
import json
import tempfile
from pathlib import Path
import unittest
from unittest import mock

from gameqakit import mcp_server as server
from gameqakit.game_session import Binding
from tests.test_route_report import FileBridge


class RouteMcpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.plugin = FileBridge(self.root)
        self.addCleanup(self.plugin.close)
        self.binding = Binding()
        self.binding.bind(self.plugin.root, 'route-session')
        self.addCleanup(self.binding.close)
        self.patch = mock.patch.object(server, '_BINDING', self.binding)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.route_patch = mock.patch.object(server, '_ROUTE', None)
        self.route_patch.start()
        self.addCleanup(self.route_patch.stop)

    def call(self, name, **args):
        response = server.handle('tools/call', {'name': name, 'arguments': args})
        return json.loads(response['content'][0]['text'])

    def test_tools_are_discoverable_and_require_confirmation(self):
        names = {row['name'] for row in server._tool_list()}
        self.assertTrue({'route_begin', 'route_step', 'route_status', 'route_finish'} <= names)
        for name in ('route_begin', 'route_step', 'route_finish'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.call(name)
        self.assertEqual(self.plugin.actions, [])

    def test_complete_recording_is_available_after_unbind_and_creator_stop(self):
        begin = self.call('route_begin', goal='从测试检查点查看地图', confirm=True)
        self.assertEqual(begin['state'], 'recording')
        step = self.call('route_step', step_id='map', label='观察地图', action='wait', frames=1, confirm=True)
        self.assertEqual(step['state'], 'done')
        self.call('game_unbind')
        with mock.patch.object(server, '_MANAGER') as manager:
            manager.status.return_value = dict(state='stopped', target_exited=True, release_confirmed=True, worker_alive=False)
            result = self.call('route_finish', outcome='passed', note='截图确认地图画面', confirm=True)
        self.assertEqual(result['state'], 'finished')
        self.assertTrue(result['cleanup']['cleanup_verified'])
        self.assertTrue(Path(result['report_html']).is_file())
        self.assertEqual(self.call('route_status')['outcome'], 'passed')

    def test_active_route_reuses_existing_record_and_blocks_unrecorded_actions(self):
        first = self.call('route_begin', goal='观察地图', confirm=True)
        self.assertEqual(first['route_id'], self.call('route_begin', goal='观察地图', confirm=True)['route_id'])
        with self.assertRaisesRegex(RuntimeError, '路线|记录'):
            self.call('route_begin', goal='另一条路线', confirm=True)
        for name in ('control_action', 'control_resume'):
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, 'route_step|路线'):
                self.call(name, action='wait', frames=1, confirm=True)
        self.assertEqual(self.plugin.actions, [])
        self.assertTrue(self.call('control_status')['connected'])
        self.assertEqual(self.call('control_pause')['state'], 'pause_requested')

    def test_empty_context_does_not_fall_back_to_old_profile(self):
        self.binding.unbind()
        with mock.patch.object(server, 'P', side_effect=AssertionError('不能回退 profile')):
            with self.assertRaisesRegex(RuntimeError, '绑定|目标'):
                self.call('route_begin', goal='不能控制旧目标', confirm=True)
        self.assertEqual(self.plugin.actions, [])


if __name__ == '__main__':
    unittest.main()
