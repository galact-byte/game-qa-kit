"""macOS 受管入口的准入、身份和所有权边界。"""
import importlib.abc
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock


class MacManagedContractTests(unittest.TestCase):
    def test_only_macos_arm64_exposes_managed_bridge_tools(self):
        from gameqakit import mcp_server, platform_support
        with mock.patch.dict(os.environ, {'GAMEQA_MACOS_MANAGED_VALIDATION': '1'}), \
             mock.patch.object(sys, 'platform', 'darwin'), \
             mock.patch('platform.machine', return_value='arm64'), \
             mock.patch('platform.mac_ver', return_value=('14.0', ('', '', ''), 'arm64')):
            names = {row['name'] for row in mcp_server._tool_list()}
            self.assertTrue({'game_inspect', 'game_prepare', 'game_start',
                             'game_stop', 'game_bind', 'game_unbind', 'game_status',
                             'control_status', 'control_action', 'control_result',
                             'control_pause', 'control_resume'} <= names)
            self.assertFalse(names & {'key', 'click', 'background_start'})
            self.assertTrue(platform_support.info()['features']['godot_managed'])
        for system, architecture in (('linux', 'arm64'), ('darwin', 'x86_64')):
            with mock.patch.object(sys, 'platform', system), mock.patch('platform.machine', return_value=architecture):
                self.assertNotIn('game_start', {row['name'] for row in mcp_server._tool_list()})

    def test_unvalidated_mac_managed_tools_remain_closed_by_default(self):
        from gameqakit import mcp_server
        with mock.patch.dict(os.environ, {'GAMEQA_MACOS_MANAGED_VALIDATION': ''}), \
             mock.patch.object(sys, 'platform', 'darwin'), \
             mock.patch('platform.machine', return_value='arm64'), \
             mock.patch('platform.mac_ver', return_value=('14.0', ('', '', ''), 'arm64')):
            self.assertNotIn('game_start', {row['name'] for row in mcp_server._tool_list()})

    def test_unregistered_runtime_cannot_supply_its_own_digest(self):
        from plugins.godot import prepare
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'godot-runtime-macos-arm64').write_bytes(b'not a registered runtime')
            (root / 'component.json').write_text(json.dumps({'runtime_sha256': 'caller supplied'}), encoding='utf-8')
            with mock.patch.object(sys, 'platform', 'darwin'):
                with self.assertRaisesRegex(ValueError, '登记'):
                    prepare.artifact_digests(root)

    def test_bridge_rejects_same_pid_with_changed_start_identity(self):
        from gameqakit.control import BridgeClient, write_connection
        from gameqakit.profile import GameProfile
        from gameqakit.sessions import write_json
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_connection(root, {'protocol': 1, 'session_id': 'same-pid', 'pid': 123,
                                   'exe_path': str(root / 'engine'), 'engine': 'godot', 'started_at': '1.2'})
            write_json(root / 'state.json', {'protocol': 1, 'session_id': 'same-pid',
                'pid': 123, 'heartbeat_unix_ms': int(time.time() * 1000), 'paused': True, 'held': []})
            with mock.patch.object(sys, 'platform', 'darwin'), \
                 mock.patch('gameqakit.process_identity.query', return_value={
                     'pid': 123, 'exe_path': str(root / 'engine'), 'started_at': '9.2'}):
                client = BridgeClient(root, pid=123, exe_path=str(root / 'engine'))
                try:
                    self.assertFalse(client.status()['connected'])
                finally:
                    client.close()

    def test_focus_guard_rejects_missing_stale_or_changed_evidence(self):
        from gameqakit.macos_focus import validate_focus
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = {'target_pid': 123, 'target_started_at': '1.2', 'original_frontmost': 456,
                     'frontmost': 456, 'target_activated': False, 'frontmost_changed': False,
                     'samples': 1, 'updated_at': time.time(), 'stopped': False}
            for change in (None, {'updated_at': time.time() - 2}, {'target_activated': True},
                           {'frontmost_changed': True}, {'frontmost': 999}, {'stopped': True},
                           {'target_started_at': '9.2'}, {'samples': 0}):
                with self.subTest(change=change):
                    path = root / 'focus-state.json'
                    if change is not None:
                        path.write_text(json.dumps({**state, **change}), encoding='utf-8')
                    with self.assertRaises((OSError, RuntimeError)):
                        validate_focus(root, 123, '1.2')
            path.write_text(json.dumps(state), encoding='utf-8')
            self.assertEqual(validate_focus(root, 123, '1.2')['frontmost'], 456)

    def test_mac_session_import_does_not_load_windows_apis(self):
        script = '''
import importlib.abc, sys
sys.platform = 'darwin'
class NoWindows(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in ('gameqakit.win32', 'gameqakit.desktop', 'gameqakit.inputs'):
            raise AssertionError('Windows import: ' + fullname)
sys.meta_path.insert(0, NoWindows())
from plugins.godot.session import GodotSession
from gameqakit.game_worker import session_for
from gameqakit.process_identity import query
'''
        run = subprocess.run([sys.executable, '-X', 'utf8', '-B', '-c', script],
                             capture_output=True, encoding='utf-8', timeout=15)
        self.assertEqual(run.returncode, 0, run.stderr)


@unittest.skipUnless(sys.platform == 'darwin', '需 macOS 原生进程组与 libproc')
class MacOwnedProcessTests(unittest.TestCase):
    def test_target_waits_at_gate_and_close_reaps_it(self):
        from gameqakit.macos_process import MacOSProcess
        from gameqakit.process_identity import query
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / 'ran'
            command = [sys.executable, '-c',
                       'import pathlib,time;pathlib.Path(__import__("sys").argv[1]).write_text("yes");time.sleep(30)', str(output)]
            with MacOSProcess(command, cwd=root) as host:
                self.assertFalse(output.exists())
                self.assertTrue(host.alive())
                host.run()
                deadline = time.monotonic() + 5
                while not output.exists() and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue(output.exists())
                pid = host.pid
            self.assertIsNone(query(pid))

    def test_close_without_run_never_executes_target(self):
        from gameqakit.macos_process import MacOSProcess
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'must-not-exist'
            with MacOSProcess([sys.executable, '-c', f'open({str(output)!r},"w").write("ran")'], cwd=directory):
                pass
            self.assertFalse(output.exists())

    def test_closing_one_session_keeps_other_owned_target_alive(self):
        from gameqakit.macos_process import MacOSProcess
        from gameqakit.process_identity import query
        command = [sys.executable, '-c', 'import time; time.sleep(30)']
        with MacOSProcess(command) as first, MacOSProcess(command) as second:
            first.run()
            second.run()
            first.close()
            self.assertIsNone(query(first.pid))
            self.assertTrue(second.alive())
            self.assertIsNotNone(query(second.pid))
        self.assertIsNone(query(second.pid))

    def test_close_reaps_target_which_ignores_sigterm(self):
        from gameqakit.macos_process import MacOSProcess
        from gameqakit.process_identity import query
        with tempfile.TemporaryDirectory() as directory:
            ready = Path(directory) / 'ready'
            script = ('import signal,time,pathlib; signal.signal(signal.SIGTERM,signal.SIG_IGN); '
                      f'pathlib.Path({str(ready)!r}).touch(); time.sleep(30)')
            with MacOSProcess([sys.executable, '-c', script], cwd=directory) as host:
                host.run()
                deadline = time.monotonic() + 5
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue(ready.exists())
            self.assertIsNone(query(host.pid))

    def test_creator_kill_reaps_running_target_and_child(self):
        from gameqakit.process_identity import query
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            owner = subprocess.Popen([sys.executable, '-m', 'tests.test_macos_managed', '--owner', str(root)])
            try:
                ready = root / 'pids.json'
                deadline = time.monotonic() + 10
                while not ready.exists() and owner.poll() is None and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue(ready.exists(), 'owner did not start target')
                pids = json.loads(ready.read_text())
                owner.kill()
                owner.wait(timeout=5)
                deadline = time.monotonic() + 5
                while any(query(pid) for pid in pids) and time.monotonic() < deadline:
                    time.sleep(.05)
                self.assertTrue(all(query(pid) is None for pid in pids), pids)
            finally:
                if owner.poll() is None:
                    owner.kill()
                owner.wait(timeout=5)


def _owner(root):
    from gameqakit.macos_process import MacOSProcess
    root = Path(root)
    script = ('import subprocess,sys,os,json,pathlib,time;'
              'c=subprocess.Popen([sys.executable,"-c","import time;time.sleep(30)"]);'
              f'pathlib.Path({str(root / "pids.json")!r}).write_text(json.dumps([os.getpid(),c.pid]));'
              'time.sleep(30)')
    with MacOSProcess([sys.executable, '-c', script], cwd=root) as host:
        host.run()
        time.sleep(30)


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--owner':
        _owner(sys.argv[2])
    else:
        unittest.main()
