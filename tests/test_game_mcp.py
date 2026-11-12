"""显式绑定期间的 MCP 路由与旧工具旁路拒绝。"""
import unittest
from unittest import mock

from gameqakit import mcp_server as server
from gameqakit.game_session import Binding


class McpBindingTests(unittest.TestCase):
    def setUp(self):
        self.binding = Binding()
        self.binding.engaged = True
        self.patch = mock.patch.object(server, '_BINDING', self.binding)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_legacy_tools_cannot_bypass_binding_or_tombstone(self):
        for name in ('click', 'key', 'load', 'kill_game', 'capture', 'win', 'mark', 'watch_start', 'save'):
            with self.subTest(name=name), mock.patch.object(server, 'P', side_effect=AssertionError('旧目标回退')):
                with self.assertRaisesRegex(RuntimeError, '绑定|后台|目标'):
                    server.handle('tools/call', {'name': name, 'arguments': {'confirm': True}})

    def test_control_routes_through_context_without_loading_old_profile(self):
        self.binding.client_for = mock.Mock(return_value=mock.Mock())
        self.binding.client_for.return_value.status.return_value = {'connected': True}
        with mock.patch.object(server, 'P', side_effect=AssertionError('旧目标回退')):
            self.assertTrue(server.t_control_status({})['connected'])
        self.binding.client_for.assert_called_once()

    def test_action_and_resume_use_exclusive_context_execution(self):
        self.binding.execute = mock.Mock(return_value={'status': 'done'})
        with mock.patch.object(server, 'P', side_effect=AssertionError('旧目标回退')):
            server.t_control_action({'action': 'wait', 'frames': 1})
            server.t_control_resume({'command_id': 'resume-1'})
        self.assertEqual([call.args[0] for call in self.binding.execute.call_args_list], ['action', 'resume'])

    def test_managed_tools_share_manager_and_require_confirmation(self):
        manager = mock.Mock()
        manager.prepare.return_value = {'state': 'preparing'}
        manager.start.return_value = {'state': 'starting'}
        manager.stop.return_value = {'state': 'stopping'}
        manager.status.return_value = {'state': 'prepared'}
        for tool in ('game_prepare', 'game_start', 'game_stop'):
            with self.subTest(tool=tool), self.assertRaises(ValueError):
                server.handle('tools/call', {'name': tool, 'arguments': {}})
        with mock.patch.object(server, '_MANAGER', manager), mock.patch.object(server, 'P', side_effect=AssertionError('无需 profile')):
            server.t_game_prepare({'plan': {'fingerprint': 'digest'}, 'request_id': 'p', 'confirm': True})
            server.t_game_start({'directory': 'session', 'fingerprint': 'digest', 'request_id': 's', 'confirm': True})
            server.t_game_stop({'directory': 'session', 'request_id': 'x', 'confirm': True})
            state = server.t_game_status({'directory': 'session'})
        self.assertEqual(state['state'], 'prepared')
        manager.prepare.assert_called_once_with({'fingerprint': 'digest'}, request_id='p', confirm=True)
        manager.start.assert_called_once_with('session', request_id='s', fingerprint='digest', confirm=True)
        manager.stop.assert_called_once_with('session', request_id='x')

    def test_lifecycle_tools_are_available_without_an_active_profile(self):
        tools = {row['name'] for row in server._tool_list()}
        self.assertTrue({'game_inspect', 'game_bind', 'game_unbind', 'game_status'} <= tools)
        with mock.patch.object(server, 'P', side_effect=AssertionError('不需要 profile')):
            result = server.t_game_status({})
        self.assertFalse(result['bound'])


if __name__ == '__main__':
    unittest.main()
