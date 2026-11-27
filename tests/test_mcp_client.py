"""真实 stdio 子进程的路由、EOF、超时和关闭行为。"""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

try:
    from gameqakit.mcp_client import McpProcess
except ImportError:
    McpProcess = None

PEER = r'''
import json, os, sys, time
for line in sys.stdin:
    msg = json.loads(line)
    args = msg.get('params', {}).get('arguments', {})
    if args.get('eof'):
        break
    if args.get('delay'):
        time.sleep(args['delay'])
    if args.get('error'):
        print(json.dumps({'jsonrpc':'2.0', 'id':msg['id'], 'error':{'code':-32000, 'message':'fixture rejection'}}), flush=True)
        continue
    result = {'pid':os.getpid(), 'arguments':args, 'tool':msg.get('params', {}).get('name')}
    print(json.dumps({'jsonrpc':'2.0', 'id':msg['id'], 'result':{'content':[{'type':'text','text':json.dumps(result)}]}}), flush=True)
'''


class McpProcessTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(McpProcess, '缺少分支独立 MCP 子进程通路')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def peer(self, name='peer', real=False):
        folder = self.root / name
        folder.mkdir()
        peer = McpProcess(folder, **({} if real else {'command': [sys.executable, '-u', '-B', '-c', PEER]}))
        self.addCleanup(peer.close)
        return peer

    def test_independent_peers_preserve_tool_arguments_and_process_identity(self):
        a, b = self.peer('a'), self.peer('b')
        first = a.call('fixture', {'branch': '甲', 'frames': 3})
        second = b.call('fixture', {'branch': '乙', 'duration_ms': 20})
        self.assertNotEqual(first['pid'], second['pid'])
        self.assertEqual(first['arguments'], {'branch': '甲', 'frames': 3})
        self.assertEqual(second['arguments'], {'branch': '乙', 'duration_ms': 20})

    @unittest.skipUnless(os.name == 'nt', '需要 Windows 控制台 API')
    def test_stdio_peer_has_valid_pipes_without_allocating_a_console_host(self):
        # 无窗口控制台仍有异步退出的 conhost；stdio MCP 不需要控制台。
        code = r'''
import ctypes, json, sys
for line in sys.stdin:
    msg = json.loads(line)
    value = {'console_cp': ctypes.windll.kernel32.GetConsoleCP(),
             'arguments': msg['params']['arguments']}
    print(json.dumps({'id':msg['id'], 'result':{'content':[
        {'type':'text','text':json.dumps(value)}]}}), flush=True)
'''
        folder = self.root / 'console-free'
        folder.mkdir()
        peer = McpProcess(folder, command=[sys.executable, '-u', '-B', '-c', code])
        self.addCleanup(peer.close)
        value = peer.call('fixture', {'text': '管道中文'})
        self.assertEqual(value['arguments'], {'text': '管道中文'})
        self.assertEqual(value['console_cp'], 0, 'MCP 管道不应分配控制台及其宿主')
        self.assertTrue(peer.close())
        self.assertEqual(peer.process.returncode, 0)

    def test_rpc_error_is_not_a_successful_result(self):
        with self.assertRaisesRegex(RuntimeError, 'fixture rejection'):
            self.peer().call('fixture', {'error': True})

    def test_unexpected_eof_fails_without_replaying_request(self):
        peer = self.peer()
        with self.assertRaises(EOFError):
            peer.call('fixture', {'eof': True}, timeout=3)
        self.assertTrue(peer.close())

    def test_timeout_invalidates_pipe_so_late_reply_cannot_satisfy_next_request(self):
        peer = self.peer()
        with self.assertRaises(TimeoutError):
            peer.call('fixture', {'delay': .2}, timeout=.02)
        with self.assertRaises((RuntimeError, EOFError)):
            peer.call('other', {})

    def test_close_sends_eof_and_waits_for_process_exit(self):
        peer = self.peer()
        peer.call('fixture', {})
        self.assertTrue(peer.close())
        self.assertIsNotNone(peer.process.poll())
        self.assertTrue(peer.close())
        with self.assertRaises((RuntimeError, EOFError)):
            peer.call('fixture', {})

    def test_real_server_can_query_without_profile_and_exits_on_eof(self):
        peer = self.peer(real=True)
        result = peer.call('game_status', {})
        self.assertFalse(result['bound'])
        self.assertTrue(peer.close())
        self.assertEqual(peer.process.returncode, 0)


if __name__ == '__main__':
    unittest.main()
