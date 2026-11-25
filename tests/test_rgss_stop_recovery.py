"""过期状态下的 VX Ace 停止请求仍须走真实暂停通道与新鲜确认。"""
import os
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest import mock

from gameqakit.control import BridgeClient, write_connection
from gameqakit.sessions import write_json
from tests import test_rgss_admission as fixtures


class RgssStopRecoveryTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.RgssAdmissionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.session = fixture.prepared()
        self.directory = self.session.root / 'bridge'
        self.state = {'protocol': 1, 'session_id': 'stop-recovery', 'pid': os.getpid(),
                      'exe_path': sys.executable, 'frame': 10, 'paused': False,
                      'held': ['right'], 'capabilities': ['move'], 'updated_at': time.time() - 3}
        write_connection(self.directory, {key: self.state[key] for key in
                         ('protocol', 'session_id', 'pid', 'exe_path')} | {'command_format': 'ini'})
        write_json(self.directory / 'status.json', self.state)
        self.session.bridge = BridgeClient(self.directory, pid=os.getpid(), exe_path=sys.executable)
        # 不创建游戏；真实通道绑定本测试进程，生命周期宿主代表已退出的自有进程。
        self.session.host = mock.Mock(pid=123456789)

    def test_expired_status_can_recover_only_after_pause_and_fresh_release(self):
        self.assertFalse(self.session.bridge.status()['connected'])
        published = threading.Event()
        stop = threading.Event()

        def plugin():
            while not stop.wait(.005):
                if (self.directory / 'pause.ini').is_file():
                    write_json(self.directory / 'status.json',
                               self.state | {'updated_at': time.time(), 'paused': True, 'held': []})
                    published.set()
                    return

        worker = threading.Thread(target=plugin)
        worker.start()
        try:
            result = self.session.stop()
        finally:
            stop.set()
            worker.join(timeout=1)
        self.assertTrue(published.is_set(), '过期状态阻止了优先暂停请求')
        self.assertEqual(result['state'], 'stopped')
        self.assertTrue(result['release_confirmed'])
        self.assertFalse(result['runtime_retained'])

    def test_expired_status_without_recovery_never_confirms_release(self):
        result = self.session.stop()
        self.assertTrue((self.directory / 'pause.ini').is_file(), '过期时仍应发出暂停请求')
        self.assertEqual(result['state'], 'cleanup_failed')
        self.assertFalse(result['release_confirmed'])
        self.assertTrue(result['runtime_retained'])

    def test_wrong_target_never_receives_pause_or_confirms_release(self):
        self.session.bridge.exe_path = Path(sys.executable).with_name('other.exe')
        result = self.session.stop()
        self.assertFalse((self.directory / 'pause.ini').exists())
        self.assertEqual(result['state'], 'cleanup_failed')
        self.assertFalse(result['release_confirmed'])


if __name__ == '__main__':
    unittest.main()
