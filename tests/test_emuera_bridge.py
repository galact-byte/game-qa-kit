"""固定 Emuera 1.824 的真实入口、正常输入及独立生命周期回归。"""
from concurrent.futures import ThreadPoolExecutor
import ctypes
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from PIL import Image, ImageChops
from gameqakit.control import BridgeClient
from gameqakit.desktop import DesktopProcess
from gameqakit.sessions import read_json, write_json
from gameqakit.win32 import _pid_path

BASE = Path(__file__).resolve().parents[1]
PLUGIN = BASE / 'plugins/emuera'
COMPONENTS = os.environ.get('GAMEQAKIT_TEST_EMUERA', '')
RUNTIME_SHA256 = 'a9af3d1e8b7eb4b9323faf6da6d770addf507e4fd2ee0eb5cfc2e58603fa58e6'


@unittest.skipUnless(os.name == 'nt' and COMPONENTS,
                     '需 Windows 与 GAMEQAKIT_TEST_EMUERA 已核对组件副本')
class EmueraBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Path(COMPONENTS).resolve() / 'Emuera.exe'
        if hashlib.sha256(cls.source.read_bytes()).hexdigest() != RUNTIME_SHA256:
            raise RuntimeError('测试组件摘要不匹配')
        cls.build_temp = tempfile.TemporaryDirectory(prefix='gameqa-emuera-build-')
        cls.addClassCleanup(cls.build_temp.cleanup)
        cls.launcher_template = Path(cls.build_temp.name) / 'GameQaEmuera.exe'
        if not (PLUGIN / 'Launcher.cs').is_file():
            raise AssertionError('C# STA 启动器与目标内输入观察器尚未实现')
        subprocess.run([sys.executable, '-B', '-X', 'utf8', str(PLUGIN / 'build.py'),
                        '--output', str(cls.launcher_template)], check=True,
                       capture_output=True, text=True, encoding='utf-8')

        cls.testing_template = Path(cls.build_temp.name) / 'GameQaEmueraTesting.exe'
        subprocess.run([sys.executable, '-B', '-X', 'utf8', str(PLUGIN / 'build.py'),
                        '--output', str(cls.testing_template), '--testing'], check=True,
                       capture_output=True, text=True, encoding='utf-8')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='gameqa-emuera-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        evidence = os.environ.get('GAMEQAKIT_TEST_EVIDENCE')
        if evidence:
            self.addCleanup(lambda: shutil.copytree(self.root, Path(evidence) / self.id().split('.')[-1],
                dirs_exist_ok=True, ignore=shutil.ignore_patterns('*.exe', 'appdata', 'localappdata', 'tmp')))
        self.runtime = self.root / 'runtime'
        for name in ('csv', 'erb', 'sav', 'appdata', 'localappdata', 'tmp'):
            (self.runtime / name).mkdir(parents=True)
        self.exe = self.runtime / 'GameQaEmuera.exe'
        shutil.copy2(self.launcher_template, self.exe)
        shutil.copy2(self.source, self.runtime / 'Emuera.exe')
        (self.runtime / 'csv/GAMEBASE.csv').write_text(
            'タイトル,GameQA Emuera Bridge\nウィンドウタイトル,GameQA Emuera Bridge\nコード,731516\nバージョン,1000\n',
            encoding='utf-8-sig')
        shutil.copy2(BASE / 'plugins/emuera/samples/gameqa-emuera-fixture.erb', self.runtime / 'erb/bridge.erb')
        self.directory = self.root / 'bridge'
        self.directory.mkdir()
        self.process = None
        self.raw_target = None
        self.meta = None
        self.session = 'fixture-session'
        self.host_script = PLUGIN / 'host.py'

    def until(self, condition, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = condition()
            if value:
                return value
            time.sleep(.02)
        self.fail('Emuera 条件未达成：' + str(self.observer()))

    def observer(self):
        try:
            return read_json(self.directory / 'observer.json')
        except (OSError, ValueError):
            return {}

    def raw_start(self):
        self.raw_target = DesktopProcess([str(self.exe)], cwd=self.runtime, env={
            'GAMEQAKIT_ENGINE': str(self.runtime / 'Emuera.exe'),
            'GAMEQAKIT_BRIDGE_DIR': str(self.directory), 'GAMEQAKIT_SESSION_ID': self.session,
            'APPDATA': str(self.runtime / 'appdata'), 'LOCALAPPDATA': str(self.runtime / 'localappdata'),
            'TMP': str(self.runtime / 'tmp'), 'TEMP': str(self.runtime / 'tmp')})
        self.raw_target.__enter__()
        self.addCleanup(self.raw_target.close)
        self.raw_target.run()
        return self.until(lambda: self.observer().get('ready') and self.observer(), 15)

    def raw_key(self, ident, kind):
        write_json(self.directory / 'input.json', {'session_id': self.session, 'pid': self.raw_target.pid,
            'id': ident, 'kind': kind})
        return self.until(lambda: any(e['id'] == ident and e['kind'] == kind
                                     for e in self.observer().get('events', [])) and self.observer())

    def start(self, *, session='fixture-session', allow_error=False):
        self.assertTrue((PLUGIN / 'host.py').is_file(), '独立 v2 协议宿主尚未实现')
        if self.process is not None:
            self.directory = self.root / ('bridge-' + session)
            self.directory.mkdir()
        self.session = session
        self.log = (self.root / 'host.txt').open('ab')
        self.addCleanup(self.log.close)
        self.process = subprocess.Popen([sys.executable, '-B', '-X', 'utf8', str(self.host_script),
            '--runtime', str(self.runtime / 'Emuera.exe'), '--launcher', str(self.exe),
            '--directory', str(self.directory), '--session', session], cwd=BASE,
            env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}, stdout=self.log, stderr=subprocess.STDOUT)
        self.addCleanup(self.stop)
        self.until(lambda: (self.directory / 'connection.json').exists() or self.process.poll() is not None, 15)
        self.assertIsNone(self.process.poll(), (self.root / 'host.txt').read_text(encoding='utf-8'))
        self.meta = read_json(self.directory / 'connection.json')
        self.client = BridgeClient(self.directory, pid=self.meta['pid'], exe_path=str(self.exe))
        self.until(lambda: self.client.status().get('observation_seq', 0) > 0
            or ((self.directory / 'status.json').exists() and self.client.status().get('error'))
            or self.process.poll() is not None, 15)
        self.assertIsNone(self.process.poll(), (self.root / 'host.txt').read_text(encoding='utf-8'))
        state = self.client.status()
        if not allow_error:
            self.assertTrue(state['connected'], str(state))
            self.assertFalse(state.get('error'), str(state))
        return state

    def stop(self):
        if self.process is not None:
            if self.process.poll() is None:
                write_json(self.directory / 'stop.json', {'session_id': self.session})
                try:
                    self.process.wait(4)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(3)
            if self.meta:
                self.until(lambda: not _pid_path(self.meta['pid']), 3)

    def request(self, ident, action_payload, **changes):
        value = dict(protocol=2, session_id=self.session, id=ident, payload=action_payload,
            pause_id='', after_observation_seq=self.client.status()['observation_seq'], created_at=time.time())
        value.update(changes)
        write_json(self.directory / 'lease.json', {'session_id': self.session, 'expires_at': time.time() + 1.5})
        write_json(self.directory / 'requests' / (ident + '.json'), value)
        write_json(self.directory / 'request.json', value)

    def test_initial_pause_and_continuing_observations(self):
        state = self.start()
        self.assertTrue(state['paused'])
        self.assertEqual(state['held'], [])
        self.assertEqual(state['capabilities'], ['confirm', 'wait'])
        self.assertEqual(state['buttons'], ['ok'])
        self.assertEqual(state['pause_scope'], 'input')
        self.assertIs(state['simulation_pause'], False)
        self.assertNotIn('frame', state)
        self.until(lambda: self.client.status().get('observation_seq', 0) > state['observation_seq'])
        with self.assertRaisesRegex(RuntimeError, '暂停'):
            self.client.action('confirm', duration_ms=60)
        self.assertEqual(self.observer()['events'], [])

    def test_enter_changes_screen_and_receipt_requires_release(self):
        state = self.start()
        before_path = self.directory / state['observation_screenshot']
        shutil.copy2(before_path, self.root / 'before.png')
        with Image.open(before_path) as img:
            before = img.convert('RGB')
        self.client.resume()
        receipt = self.client.action('confirm', duration_ms=120, command_id='confirm-one')
        self.assertTrue(receipt['press_consumed'])
        self.assertTrue(receipt['release_consumed'])
        self.assertEqual(receipt['held'], [])
        self.assertGreaterEqual(receipt['completed_monotonic_ms'] - receipt['started_monotonic_ms'], 120)
        self.assertEqual([e['kind'] for e in self.observer()['events']], ['down', 'up'])
        with Image.open(receipt['screenshot']) as img:
            after = img.convert('RGB')
        self.assertIsNotNone(ImageChops.difference(before, after).getbbox())
        self.assertGreater(sum(1 for r, g, b in after.getdata() if 30 <= r <= 95 and g > 160 and 70 <= b <= 170), 100)
        self.assertEqual(hashlib.sha256(Path(receipt['screenshot']).read_bytes()).hexdigest(), receipt['screenshot_sha256'])
        again = self.client.action('confirm', duration_ms=120, command_id='confirm-one')
        self.assertEqual(again['screenshot_sha256'], receipt['screenshot_sha256'])
        self.assertEqual(len(self.observer()['events']), 2)
        self.client.action('confirm', duration_ms=50, command_id='second-confirm')
        self.client.action('confirm', duration_ms=50, command_id='restart-original')
        self.client.action('confirm', duration_ms=50, command_id='after-restart')
        self.assertEqual(len(self.observer()['events']), 8)

    def test_wrong_runtime_hash_rejected_before_launch(self):
        runtime = self.runtime / 'Emuera.exe'
        with runtime.open('ab') as stream:
            stream.write(b'wrong-build')
        process = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(PLUGIN / 'host.py'),
            '--runtime', str(runtime), '--launcher', str(self.exe),
            '--directory', str(self.directory), '--session', self.session],
            cwd=BASE, capture_output=True, timeout=4)
        self.assertNotEqual(process.returncode, 0)
        self.assertIn('运行时摘要未验证', process.stderr.decode('utf-8'))
        self.assertFalse((self.directory / 'connection.json').exists())
        with DesktopProcess([str(self.exe)], cwd=self.runtime, env={
                'GAMEQAKIT_ENGINE': str(runtime), 'GAMEQAKIT_BRIDGE_DIR': str(self.directory),
                'GAMEQAKIT_SESSION_ID': self.session}) as target:
            target.run()
            self.until(lambda: not target.alive(), 4)
        self.assertIn('Unvalidated runtime digest',
            (self.directory / 'launcher-error.txt').read_text(encoding='utf-8'))
        self.assertFalse((self.directory / 'observer.json').exists())

    def test_wait_and_unsupported_actions(self):
        self.start()
        for receipt in (self.client.resume(), self.client.action('wait', duration_ms=100)):
            for field in ('consumed', 'press_consumed', 'release_consumed'):
                self.assertIs(receipt[field], False)
        for action, options in [('move', {'direction': 'right', 'duration_ms': 50}),
                                ('cancel', {'duration_ms': 50}), ('confirm', {'frames': 1}),
                                ('click', {'x': 10, 'y': 10, 'duration_ms': 50})]:
            with self.assertRaises((ValueError, RuntimeError)):
                self.client.action(action, **options)
        self.assertFalse(self.observer()['events'])

    def test_manual_pause_waits_for_target_release(self):
        self.start()
        self.client.resume()
        with ThreadPoolExecutor(max_workers=1) as pool:
            running = pool.submit(self.client.action, 'confirm', duration_ms=1000, command_id='held')
            self.until(lambda: self.observer().get('held'))
            self.client.pause()
            self.until(lambda: self.client.status().get('paused') and not self.client.status().get('held'))
            with self.assertRaises(RuntimeError):
                running.result(4)
        receipt = self.client.result('held')
        self.assertEqual(receipt['status'], 'failed')
        self.assertTrue(receipt['press_consumed'])
        self.assertTrue(receipt['release_consumed'])
        self.assertFalse(self.observer()['held'])

    def test_lease_expiry_releases_input(self):
        self.start()
        self.client.resume()
        self.request('lease-held', {'action': 'confirm', 'duration_ms': 1000})
        write_json(self.directory / 'lease.json', {'session_id': self.session, 'expires_at': time.time() + .3})
        self.until(lambda: self.observer().get('held'))
        self.until(lambda: (self.directory / 'results/lease-held.json').exists())
        receipt = self.client.result('lease-held')
        self.assertEqual(receipt['status'], 'failed')
        self.assertTrue(receipt['release_consumed'])
        self.assertFalse(self.observer()['held'])

    def test_bad_identity_generation_and_expired_request_send_no_input(self):
        self.start()
        self.client.resume()
        self.request('bad-session', {'action': 'confirm', 'duration_ms': 50}, session_id='old-session')
        self.until(lambda: self.client.status().get('error'))
        self.assertEqual(self.observer()['events'], [])

    def set_testing_mode(self, **values):
        if self.process is None:
            shutil.copy2(self.testing_template, self.exe)
        write_json(self.directory / 'test-mode.json', values)

    def test_stale_pause_and_expired_request_send_no_input(self):
        for index, changes in enumerate(({'pause_id': 'stale'}, {'created_at': time.time() - 5},
                                          {'after_observation_seq': 99999})):
            if self.process is not None:
                self.stop()
            self.start(session='bad-' + str(index))
            self.client.resume()
            self.request('stale', {'action': 'confirm', 'duration_ms': 50}, **changes)
            self.until(lambda: self.client.status().get('error'))
            self.assertFalse(self.observer()['events'])

    def test_pid_and_path_binding_fail_before_input(self):
        self.start()
        for identity in ({'pid': self.meta['pid'] + 1, 'exe_path': str(self.exe)},
                         {'pid': self.meta['pid'], 'exe_path': str(self.runtime / 'different.exe')}):
            wrong = BridgeClient(self.directory, **identity)
            with self.assertRaises((RuntimeError, ValueError)):
                wrong.resume()
        self.assertFalse(self.observer()['events'])

    def test_raw_unsupported_action_and_unsafe_id_never_send_input(self):
        for index, changes in enumerate(({'payload': {'action': 'cancel', 'duration_ms': 50}}, {'id': '../escape'})):
            if self.process is not None:
                self.stop()
            self.start(session='invalid-' + str(index))
            self.client.resume()
            self.request('raw', {'action': 'confirm', 'duration_ms': 50}, **changes)
            self.until(lambda: self.client.status().get('error'))
            self.assertFalse(self.observer()['events'])
            self.assertFalse((self.directory / 'escape.json').exists())

    def test_black_white_and_speck_initial_images_send_no_input(self):
        for index, colour in enumerate(('black', 'white', 'speck')):
            if self.process is not None:
                self.stop()
                self.process = None
                self.directory = self.root / ('blank-' + str(index))
                self.directory.mkdir()
            self.set_testing_mode(blank=colour)
            state = self.start(allow_error=True)
            self.assertIn('取图', state['error'])
            self.assertEqual(state['observation_seq'], 0)
            self.assertFalse(self.observer()['events'])

    def test_consumption_retained_when_action_turns_screen_black(self):
        self.set_testing_mode(action='blank-after')
        self.start()
        self.client.resume()
        with self.assertRaisesRegex(RuntimeError, '取图'):
            self.client.action('confirm', duration_ms=70, command_id='blank-action')
        receipt = self.client.result('blank-action')
        self.assertEqual(receipt['status'], 'failed')
        self.assertTrue(receipt['press_consumed'])
        self.assertTrue(receipt['release_consumed'])
        self.assertFalse(self.observer()['held'])

    def test_screenshot_write_failure_retains_consumption(self):
        self.start()
        self.client.resume()
        (self.directory / 'screenshots/no-write.png').mkdir()
        with self.assertRaises(RuntimeError):
            self.client.action('confirm', duration_ms=60, command_id='no-write')
        receipt = self.client.result('no-write')
        self.assertEqual(receipt['status'], 'failed')
        self.assertTrue(receipt['press_consumed'])
        self.assertTrue(receipt['release_consumed'])
        self.assertTrue(self.client.status()['paused'])

    def test_persistent_control_read_error_releases_input(self):
        self.start()
        self.client.resume()
        self.request('read-error', {'action': 'confirm', 'duration_ms': 1000})
        self.until(lambda: self.observer().get('held'))
        (self.directory / 'lease.json').write_text('{', encoding='utf-8')
        self.until(lambda: (self.directory / 'results/read-error.json').exists())
        receipt = self.client.result('read-error')
        self.assertEqual(receipt['status'], 'failed')
        self.assertTrue(receipt['release_consumed'])
        self.assertFalse(self.observer()['held'])

    def test_stalled_ui_is_killed_by_independent_host(self):
        self.set_testing_mode(action='block-down')
        self.start()
        self.client.resume()
        started = time.monotonic()
        self.request('stalled', {'action': 'confirm', 'duration_ms': 60})
        self.until(lambda: self.process.poll() is not None, 3)
        self.assertLess(time.monotonic() - started, 1.7)
        self.assertEqual(read_json(self.directory / 'termination.json')['reason'], 'activity_timeout')
        self.assertFalse(_pid_path(self.meta['pid']))
        self.assertFalse((self.directory / 'results/stalled.json').exists())

    def test_unconfirmed_release_with_repeated_pause_and_read_error_keeps_deadline(self):
        self.set_testing_mode(action='block-up')
        self.start()
        self.client.resume()
        self.request('no-release', {'action': 'confirm', 'duration_ms': 1000})
        self.until(lambda: self.observer().get('held'))
        started = time.monotonic()
        (self.directory / 'lease.json').write_text('{', encoding='utf-8')
        for index in range(8):
            if self.process.poll() is not None:
                break
            write_json(self.directory / 'pause.json', {'session_id': self.session, 'id': 'pause-' + str(index)})
            time.sleep(.08)
        self.until(lambda: self.process.poll() is not None, 2)
        self.assertLess(time.monotonic() - started, 1.7)
        self.assertFalse(_pid_path(self.meta['pid']))
        termination = read_json(self.directory / 'termination.json')
        self.assertTrue(termination['press_consumed'])
        self.assertFalse(termination['release_consumed'])
        self.assertFalse((self.directory / 'results/no-release.json').exists())

    def test_shutdown_releases_active_input_before_exit(self):
        self.start()
        self.client.resume()
        self.request('shutdown', {'action': 'confirm', 'duration_ms': 1000})
        self.until(lambda: self.observer().get('held'))
        started = time.monotonic()
        self.stop()
        self.assertLess(time.monotonic() - started, 1.6)
        self.assertEqual(self.process.returncode, 0)
        receipt = read_json(self.directory / 'results/shutdown.json')
        self.assertFalse(receipt['ok'])
        self.assertTrue(receipt['press_consumed'])
        self.assertTrue(receipt['release_consumed'])
        self.assertFalse(receipt['held'])
        self.assertEqual(read_json(self.directory / 'termination.json')['reason'], 'stop_requested')

    def test_host_termination_kills_owned_job(self):
        self.start()
        self.process.kill()
        self.process.wait(3)
        self.until(lambda: not _pid_path(self.meta['pid']), 2)

    def test_capture_hang_is_bounded_and_worker_is_reaped(self):
        stub = self.root / 'capture-stub.py'
        stub.write_text('from pathlib import Path\nimport sys,time,runpy\n'
            'if (Path(sys.argv[1])/"hang-capture").exists():\n time.sleep(60)\n'
            f'runpy.run_path({str(PLUGIN / "capture_worker.py")!r},run_name="__main__")\n', encoding='utf-8')
        self.host_script = self.root / 'host-fixture.py'
        self.host_script.write_text('import runpy,sys\n'
            f'host=runpy.run_path({str(PLUGIN / "host.py")!r})\n'
            f'host["Supervisor"].capture_worker={str(stub)!r}\n'
            'from pathlib import Path\n'
            'host["run"](Path(sys.argv[2]),Path(sys.argv[4]),Path(sys.argv[6]),sys.argv[8])\n', encoding='utf-8')
        self.start()
        self.client.resume()
        (self.directory / 'hang-capture').touch()
        started = time.monotonic()
        self.request('capture-hung', {'action': 'confirm', 'duration_ms': 60})
        self.until(lambda: self.process.poll() is not None, 5)
        self.assertLess(time.monotonic() - started, 3.9)
        ended = read_json(self.directory / 'termination.json')
        self.assertEqual(ended['reason'], 'capture_timeout')
        self.assertTrue(ended['capture_worker_pids'])
        for pid in ended['capture_worker_pids']:
            self.assertFalse(_pid_path(pid))
        self.assertFalse(_pid_path(self.meta['pid']))
        receipt_path = self.directory / 'results/capture-hung.json'
        if receipt_path.exists():
            receipt = read_json(receipt_path)
            self.assertFalse(receipt['ok'])
            self.assertTrue(receipt['press_consumed'])
            self.assertTrue(receipt['release_consumed'])
            self.assertNotIn('screenshot', receipt)

    def test_normal_save_load_in_process_and_across_sessions(self):
        shutil.copy2(BASE / 'tests/emuera_fixture/storage.erb', self.runtime / 'erb/bridge.erb')
        self.start()
        self.client.resume()
        saved = self.client.action('confirm', duration_ms=70, command_id='save-normal')
        self.assertTrue(saved['release_consumed'])
        save = self.runtime / 'save01.sav'
        self.assertTrue(save.is_file())
        digest = hashlib.sha256(save.read_bytes()).hexdigest()
        self.client.action('confirm', duration_ms=70, command_id='change-normal')
        loaded = self.client.action('confirm', duration_ms=70, command_id='load-normal')
        self.assertTrue(loaded['release_consumed'])
        self.assert_loaded_image(loaded['screenshot'])
        self.stop()
        self.start(session='storage-new-session')
        self.assertTrue(self.client.status()['paused'])
        self.client.resume()
        loaded = self.client.action('confirm', duration_ms=70, command_id='load-new-session')
        self.assert_loaded_image(loaded['screenshot'])
        self.assertEqual(hashlib.sha256(save.read_bytes()).hexdigest(), digest)
        self.assertFalse(self.observer()['held'])
        write_json(self.root / 'storage-ledger.json', {'kind': 'self-authored-original-input',
            'slot': 1, 'file': 'runtime/save01.sav', 'before': 'absent', 'sha256': digest,
            'restore': 'Dispose only the generated runtime; no original game or user save was copied or modified.',
            'in_process': True, 'new_session': True, 'save_modified_by_bridge': False})
        self.stop()
        # 保留本批生成的原始存档证据，防止通用临时副本清理把它一起丢掉。
        shutil.copy2(save, self.root / 'self-authored-save01.sav')

    def assert_loaded_image(self, path):
        with Image.open(path) as image:
            colours = list(image.convert('RGB').getdata())
        self.assertGreater(sum(1 for r, g, b in colours if 30 < r < 100 and 130 < g < 210 and b > 220), 120)
        self.assertLess(sum(1 for r, g, b in colours if r > 200 and g < 100 and b < 100), 50)

    def test_original_entry_paths_and_post_dispatch_consumption(self):
        u = ctypes.WinDLL('user32')
        u.GetForegroundWindow.restype = ctypes.c_void_p
        foreground = u.GetForegroundWindow()
        state = self.raw_start()
        self.assertEqual(Path(state['application_path']), self.exe)
        self.assertEqual(Path(state['engine_directory']), self.runtime)
        self.assertEqual(state['engine_name'], self.exe.stem)
        self.assertEqual(state['pid'], self.raw_target.pid)
        self.assertEqual(state['events'], [])
        first = self.raw_key('first-down', 'down')
        self.assertTrue(first['held'])
        first = self.raw_key('first-up', 'up')
        self.assertFalse(first['held'])
        self.assertEqual([e['kind'] for e in first['events']], ['down', 'up'])
        # 第二次输入验证原引擎内部重挂 KeyDown 处理器后，观察器仍在原处理完成后回执。
        self.raw_key('second-down', 'down')
        second = self.raw_key('second-up', 'up')
        self.assertEqual([e['kind'] for e in second['events']], ['down', 'up', 'down', 'up'])
        self.assertTrue(all(e['after_original_handler'] for e in second['events']))
        self.assertEqual(u.GetForegroundWindow(), foreground)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), RUNTIME_SHA256)


if __name__ == '__main__':
    unittest.main()
