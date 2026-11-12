"""Wolf 共享内存与真实 x86 发布者的跨进程回归。"""
import importlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == 'nt', '需要 Windows 命名共享内存')
class WolfIpcTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        parent = Path('E:/DevData/gameqa-wolf/tmp')
        parent.mkdir(parents=True, exist_ok=True)
        cls.tmp = tempfile.TemporaryDirectory(dir=parent)
        cls.root = Path(cls.tmp.name)
        cls.exe = cls.root / 'IpcFixture.exe'
        cls.ready = (ROOT / 'plugins/wolf/Realtime.hpp').is_file()
        if not cls.ready:
            return
        args = ['cl', '/nologo', '/EHsc', '/W4', '/WX', '/std:c++17', '/utf-8',
                '/DWIN32_LEAN_AND_MEAN', '/DNOMINMAX', '/I' + str(ROOT / 'plugins/wolf'),
                str(ROOT / 'tests/wolf/IpcFixture.cpp'), '/Fe:' + str(cls.exe), '/link', 'advapi32.lib']
        script = cls.root / 'compile.cmd'
        script.write_text('@echo off\ncall "E:/vsCode/VSBuildTools/VC/Auxiliary/Build/vcvars32.bat" >nul\n'
                          'if errorlevel 1 exit /b 1\n' + subprocess.list2cmdline(args), encoding='utf-8')
        subprocess.run(['cmd', '/d', '/c', str(script)], cwd=cls.root, check=True, timeout=45)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.assertTrue(self.ready, 'Wolf 无文件控制通道尚未实现')
        self.module = importlib.import_module('gameqakit.wolf_ipc')
        self.session = uuid.uuid4().hex
        self.process = subprocess.Popen([str(self.exe), self.session], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, text=True)
        self.addCleanup(self.stop)
        self.assertEqual(self.process.stdout.readline().strip(), 'ready')
        self.mailbox = self.module.WolfMailbox(self.process.pid, self.session)
        self.addCleanup(self.mailbox.close)

    def stop(self):
        if self.process.poll() is None:
            self.process.stdin.write('exit\n')
            self.process.stdin.flush()
            try:
                self.process.wait(3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.process.stdin.close()
        self.process.stdout.close()

    def test_client_renews_while_image_validation_blocks(self):
        from unittest.mock import patch
        from PIL import Image
        from gameqakit.control import BridgeClient, write_connection
        self.mailbox.close()
        self.stop()
        self.process = subprocess.Popen([str(self.exe), self.session, str(self.exe)],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.assertEqual(self.process.stdout.readline().strip(), 'ready')
        self.mailbox = self.module.WolfMailbox(self.process.pid, self.session)
        self.addCleanup(self.mailbox.close)
        directory = self.root / self.session
        directory.mkdir()
        meta = {'protocol': 1, 'pid': self.process.pid, 'session_id': self.session,
                'exe_path': str(self.exe), 'command_format': 'ini', 'control_transport': 'wolf-shm-v1'}
        write_connection(directory, meta)
        (directory / 'screenshots').mkdir()
        Image.new('RGB', (8, 8), 'blue').save(directory / 'screenshots/slow-image.png')
        client = BridgeClient(directory, pid=self.process.pid, exe_path=str(self.exe))
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline and not client.status().get('connected'):
            time.sleep(.01)
        self.assertTrue(client.status().get('connected'), '共享内存状态尚未接入客户端')
        original = Image.open
        delayed = False
        def slow_open(*args, **kwargs):
            nonlocal delayed
            if not delayed:
                delayed = True
                time.sleep(1.8)
            return original(*args, **kwargs)
        with patch('gameqakit.control.Image.open', side_effect=slow_open):
            result = client.resume(command_id='slow-image', timeout=4)
        self.assertEqual(result['status'], 'done')
        self.assertFalse(client.status()['lease_expired'], '图像校验阻塞了续租')
        # Once bound, status and manual pause must not read control files again.
        with patch('gameqakit.control._read', side_effect=AssertionError('control disk read')):
            self.assertTrue(client.status()['connected'])
            self.assertEqual(client.pause()['state'], 'pause_requested')

    def test_pause_generation_survives_transient_mailbox_contention(self):
        import threading
        from unittest.mock import patch
        from gameqakit.control import BridgeClient
        self.mailbox.write('pause', '[bridge]\nsession_id=' + self.session + '\nid=human-stop\n')
        entered, release = threading.Event(), threading.Event()
        lock = self.mailbox.locks[2]
        def reader_holding_lock():
            self.mailbox.kernel.WaitForSingleObject(lock, 0)
            entered.set()
            try:
                release.wait(2)
            finally:
                self.mailbox.kernel.ReleaseMutex(lock)
        worker = threading.Thread(target=reader_holding_lock)
        worker.start()
        original_read = self.mailbox.read
        def contended_read(name):
            value = original_read(name)
            release.set()
            worker.join(1)
            return value
        try:
            self.assertTrue(entered.wait(1))
            client = BridgeClient(self.root, exe_path=str(self.exe))
            client._ipc = self.mailbox
            meta = {'control_transport': 'wolf-shm-v1', 'session_id': self.session}
            with patch.object(self.mailbox, 'read', side_effect=contended_read):
                self.assertEqual(client._pause_id(meta), 'human-stop')
        finally:
            release.set()
            worker.join(2)

    def test_unknown_transport_is_rejected_without_file_fallback(self):
        from gameqakit.control import BridgeClient, write_connection
        directory = self.root / self.session
        write_connection(directory, {'protocol': 1, 'pid': self.process.pid,
            'session_id': self.session, 'exe_path': str(self.exe),
            'command_format': 'ini', 'control_transport': 'unknown-v2'})
        state = BridgeClient(directory, exe_path=str(self.exe)).status()
        self.assertFalse(state['connected'])
        self.assertIn('控制通道', state['error'])

    def test_writer_lock_contention_and_abandonment_are_bounded(self):
        import threading
        from ctypes import wintypes as W
        kernel = self.mailbox.kernel
        kernel.OpenThread.argtypes = [W.DWORD, W.BOOL, W.DWORD]
        kernel.OpenThread.restype = W.HANDLE
        entered, release = threading.Event(), threading.Event()
        lock = self.mailbox.locks[2]
        def abandoned_writer():
            self.assertEqual(self.mailbox.kernel.WaitForSingleObject(lock, 0), 0)
            entered.set()
            release.wait(2)
        writer = threading.Thread(target=abandoned_writer)
        writer.start()
        thread_handle = kernel.OpenThread(0x00100000, False, writer.native_id)
        self.assertTrue(thread_handle)
        self.addCleanup(kernel.CloseHandle, thread_handle)
        try:
            self.assertTrue(entered.wait(1))
            started = time.monotonic()
            self.assertEqual(self.mailbox.read('pause'), '')
            self.assertLess(time.monotonic() - started, .1)
            with self.assertRaisesRegex(RuntimeError, 'ipc_writer_busy'):
                self.mailbox.write('pause', 'cannot-steal')
        finally:
            release.set()
            writer.join()
            # Python join can finish before Windows marks its mutex owner abandoned.
            self.assertEqual(kernel.WaitForSingleObject(thread_handle, 1000), 0)
        with self.assertRaisesRegex(RuntimeError, 'ipc_writer_abandoned'):
            self.mailbox.read('pause')

    def test_native_and_python_exchange_complete_messages(self):
        payload = '[bridge]\nsession_id=' + self.session + '\nid=正常输入\n'
        self.mailbox.write('request', payload)
        deadline = time.monotonic() + 2
        result = ''
        while time.monotonic() < deadline:
            result = self.mailbox.read('result')
            if result:
                break
            time.sleep(.005)
        self.assertEqual(result, payload)
        self.assertEqual(self.mailbox.read('status'), 'native-ready')

    def test_wrong_session_and_missing_server_do_not_create_mapping(self):
        with self.assertRaises(OSError):
            self.module.WolfMailbox(self.process.pid, uuid.uuid4().hex)
        for session in ('', '../other', 'a' * 65):
            with self.assertRaises(ValueError):
                self.module.WolfMailbox(self.process.pid, session)

    def test_partial_or_oversized_publication_is_bounded_and_not_consumed(self):
        import ctypes
        self.mailbox.write('pause', 'valid')
        with self.assertRaises(ValueError):
            self.mailbox.write('pause', 'x' * 65536)
        self.assertEqual(self.mailbox.read('pause'), 'valid')
        # Simulate a writer killed between the odd/even commit sequence.
        address = self.mailbox.address + 2 * 65536
        ctypes.c_long.from_address(address).value |= 1
        started = time.monotonic()
        self.assertEqual(self.mailbox.read('pause'), '')
        self.assertLess(time.monotonic() - started, .1)
        with self.assertRaises(RuntimeError):
            self.mailbox.write('pause', 'must-not-steal')


if __name__ == '__main__':
    unittest.main()
