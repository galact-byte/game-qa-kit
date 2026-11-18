"""固定 TyranoScript Ver5 / Electron 7.1.2 自写工程的真实后台桥回归。"""
from concurrent.futures import ThreadPoolExecutor
import ctypes
import hashlib
import json
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
from gameqakit.sessions import read_json, write_json
from gameqakit.win32 import _pid_path

BASE = Path(__file__).resolve().parents[1]
PLUGIN = BASE / 'plugins/tyrano'
COMPONENTS = os.environ.get('GAMEQAKIT_TEST_TYRANO', '')


@unittest.skipUnless(os.name == 'nt' and COMPONENTS,
                     '需 Windows 与 GAMEQAKIT_TEST_TYRANO 已核对组件副本')
class TyranoBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.components = Path(COMPONENTS).resolve()
        cls.runtime_temp = tempfile.TemporaryDirectory(prefix='gameqa-tyrano-runtime-')
        cls.addClassCleanup(cls.runtime_temp.cleanup)
        cls.runtime = Path(cls.runtime_temp.name)
        # 只复用公共组件与固定运行时，不带探针场景、用户数据或存档。
        for source in cls.components.iterdir():
            if source.is_file() and (source.suffix in ('.exe', '.dll', '.bin', '.pak', '.dat')
                                     or source.name.startswith('LICENSE')):
                shutil.copy2(source, cls.runtime / source.name)
        shutil.copytree(cls.components / 'locales', cls.runtime / 'locales')
        cls.app = cls.runtime / 'resources/app'
        shutil.copytree(cls.components / 'resources/app/tyrano', cls.app / 'tyrano')
        shutil.copy2(cls.components / 'resources/app/index.html', cls.app / 'index.html')
        (cls.app / 'data/scenario').mkdir(parents=True)
        (cls.app / 'data/system').mkdir(parents=True)
        (cls.app / 'package.json').write_text(json.dumps({
            'name': 'gameqa-tyrano-fixture', 'version': '1.0.0', 'main': 'main.js'}), encoding='utf-8')
        for name in ('Fixture.js', 'MainBridge.js', 'Observer.js'):
            shutil.copy2(PLUGIN / name, cls.app / ('main.js' if name == 'Fixture.js' else name))
        shutil.copy2(PLUGIN / 'samples/gameqa-tyrano-fixture.ks', cls.app / 'data/scenario/first.ks')
        (cls.app / 'data/scenario/make.ks').write_text('[return]\n', encoding='utf-8')
        (cls.app / 'data/system/KeyConfig.js').write_text('''var __tyrano_key_config = {
            key: {13: function() {
                const fs = require('fs'), path = require('path');
                const root = process.env.GAMEQA_FIXTURE_ROOT;
                const mode = fs.existsSync(path.join(root, 'mode.json'))
                    ? JSON.parse(fs.readFileSync(path.join(root, 'mode.json'), 'utf8')).action : 'next';
                TYRANO.kag.key_mouse[mode]();
            }}, mouse: {}, gesture: {}, system_key_event: false
        };''', encoding='utf-8')

    def setUp(self):
        self.assertTrue((PLUGIN / 'host.py').is_file(), '独立 Tyrano 宿主尚未实现')
        self.assertTrue((PLUGIN / 'MainBridge.js').is_file(), 'Tyrano 主进程桥尚未实现')
        self.temp = tempfile.TemporaryDirectory(prefix='gameqa-tyrano-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        evidence = os.environ.get('GAMEQAKIT_TEST_EVIDENCE')
        if evidence:
            self.addCleanup(lambda: shutil.copytree(self.root, Path(evidence) / self.id().split('.')[-1],
                dirs_exist_ok=True, ignore=shutil.ignore_patterns('cache', 'tmp', 'appdata', 'localappdata')))
        self.directory = self.root / 'bridge'
        self.directory.mkdir()
        self.exe = self.runtime / 'engine-runtime.exe'
        self.process = None
        self.storage = 'webstorage'

    def until(self, condition, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = condition()
            if value:
                return value
            time.sleep(.02)
        logs = '\n'.join(p.read_text(encoding='utf-8', errors='replace')[-2500:]
                         for p in self.root.glob('*.txt'))
        self.fail('Tyrano 条件未达成：' + logs)

    def fixture(self):
        try:
            return read_json(self.root / 'fixture.json')
        except (OSError, ValueError):
            return {}

    def start(self, *, session='fixture-session', allow_error=False):
        if self.process is not None:
            self.directory = self.root / ('bridge-' + session)
            self.directory.mkdir()
        config = (self.components / 'resources/app/data/system/Config.tjs').read_text(encoding='utf-8')
        config += '\n;projectID=gameqa_tyrano_fixture\n;configSave=' + self.storage + '\n'
        (self.app / 'data/system/Config.tjs').write_text(config, encoding='utf-8')
        self.log = (self.root / 'host.txt').open('ab')
        self.addCleanup(self.log.close)
        self.process = subprocess.Popen([sys.executable, '-B', '-X', 'utf8', str(PLUGIN / 'host.py'),
            '--runtime', str(self.exe), '--directory', str(self.directory), '--session', session,
            '--data-root', str(self.root / 'runtime')],
            cwd=BASE, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1',
                'GAMEQA_FIXTURE_ROOT': str(self.root)}, stdout=self.log, stderr=subprocess.STDOUT)
        self.addCleanup(self.stop)
        self.until(lambda: (self.directory / 'connection.json').exists() or self.process.poll() is not None, 15)
        self.assertIsNone(self.process.poll(), (self.root / 'host.txt').read_text(encoding='utf-8'))
        self.meta = read_json(self.directory / 'connection.json')
        self.client = BridgeClient(self.directory, pid=self.meta['pid'], exe_path=str(self.exe))
        self.until(lambda: self.client.status().get('observation_seq', 0) > 0
            or (self.client.status().get('error') if (self.directory / 'status.json').exists() else False)
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
                write_json(self.directory / 'stop.json', {'session_id': self.meta['session_id']})
                try:
                    self.process.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=3)
            if hasattr(self, 'meta'):
                self.until(lambda: not _pid_path(self.meta['pid']), 4)

    def request(self, ident, payload, **changes):
        value = dict(protocol=2, session_id=self.meta['session_id'], id=ident, payload=payload,
            pause_id='', after_observation_seq=self.client.status()['observation_seq'])
        value.update(changes)
        write_json(self.directory / 'lease.json', {'session_id': self.meta['session_id'],
            'expires_at': time.time() + 1.5})
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
        (self.root / 'background').touch()
        self.until(lambda: self.client.status().get('observation_seq', 0) > state['observation_seq'])
        with self.assertRaisesRegex(RuntimeError, '暂停'):
            self.client.action('confirm', duration_ms=60)
        self.assertFalse(self.fixture().get('events'))

    def test_enter_advances_scene_and_release_precedes_receipt(self):
        state = self.start()
        shutil.copy2(self.directory / state['observation_screenshot'], self.root / 'before.png')
        with Image.open(self.directory / state['observation_screenshot']) as img:
            before = img.convert('RGB')
        self.client.resume()
        receipt = self.client.action('confirm', duration_ms=120, command_id='confirm-one')
        self.assertTrue(receipt['press_consumed'])
        self.assertTrue(receipt['release_consumed'])
        self.assertGreaterEqual(receipt['completed_monotonic_ms'] - receipt['started_monotonic_ms'], 120)
        self.until(lambda: not self.fixture().get('keyHeld') and len(self.fixture().get('events', [])) == 2)
        self.assertEqual(self.fixture()['phase'], 1)
        self.assertEqual([e['type'] for e in self.fixture()['events']], ['keydown', 'keyup'])
        self.assertTrue(all(e['trusted'] for e in self.fixture()['events']))
        with Image.open(receipt['screenshot']) as img:
            self.assertIsNotNone(ImageChops.difference(before, img.convert('RGB')).getbbox())
        self.assertEqual(hashlib.sha256(Path(receipt['screenshot']).read_bytes()).hexdigest(), receipt['screenshot_sha256'])
        again = self.client.action('confirm', duration_ms=120, command_id='confirm-one')
        self.assertEqual(again['screenshot_sha256'], receipt['screenshot_sha256'])
        self.assertEqual(self.fixture()['phase'], 1)

    def test_wait_resume_and_unsupported_actions(self):
        self.start()
        for receipt in (self.client.resume(), self.client.action('wait', duration_ms=100)):
            for field in ('consumed', 'press_consumed', 'release_consumed'):
                self.assertIs(receipt[field], False)
        for action, options in [('move', {'direction': 'right', 'duration_ms': 50}),
                                ('cancel', {'duration_ms': 50}), ('confirm', {'frames': 1}),
                                ('click', {'x': 10, 'y': 10, 'duration_ms': 50})]:
            with self.assertRaises((ValueError, RuntimeError)):
                self.client.action(action, **options)
        self.assertFalse(self.fixture().get('events'))

    def test_manual_pause_waits_for_real_release(self):
        self.start()
        self.client.resume()
        with ThreadPoolExecutor(max_workers=1) as pool:
            running = pool.submit(self.client.action, 'confirm', duration_ms=1000, command_id='held')
            self.until(lambda: self.fixture().get('keyHeld'))
            self.client.pause()
            self.until(lambda: self.client.status().get('paused') and not self.client.status().get('held'))
            with self.assertRaises(RuntimeError):
                running.result(4)
        receipt = self.client.result('held')
        self.assertEqual(receipt['status'], 'failed')
        self.assertTrue(receipt['press_consumed'])
        self.assertTrue(receipt['release_consumed'])
        self.until(lambda: not self.fixture()['keyHeld'])

    def test_lease_expiry_releases_without_client(self):
        self.start()
        self.client.resume()
        self.request('lease-held', {'action': 'confirm', 'duration_ms': 1000})
        write_json(self.directory / 'lease.json', {'session_id': self.meta['session_id'], 'expires_at': time.time() + .3})
        self.until(lambda: self.fixture().get('keyHeld'))
        self.until(lambda: (self.directory / 'results/lease-held.json').exists())
        receipt = self.client.result('lease-held')
        self.assertEqual(receipt['status'], 'failed')
        self.assertTrue(receipt['release_consumed'])
        self.until(lambda: not self.fixture()['keyHeld'])

    def test_bad_session_request_is_rejected_without_input(self):
        self.start()
        self.client.resume()
        self.request('bad-session', {'action': 'confirm', 'duration_ms': 50}, session_id='old-session')
        self.until(lambda: self.client.status().get('error'))
        self.assertFalse(self.fixture().get('events'))

    def test_old_pause_generation_is_rejected_without_input(self):
        self.start()
        self.client.resume()
        self.client.pause()
        self.until(lambda: self.client.status().get('paused'))
        self.request('old-pause', {'action': 'resume'})
        self.until(lambda: self.client.status().get('error'))
        self.assertTrue(self.client.status()['paused'])
        self.assertFalse(self.fixture().get('events'))

    def test_unsafe_raw_command_id_cannot_write_outside_results(self):
        self.start()
        self.client.resume()
        self.request('raw-id', {'action': 'confirm', 'duration_ms': 50}, id='../escape')
        self.until(lambda: self.client.status().get('error'))
        self.assertFalse((self.directory / 'escape.json').exists())
        self.assertFalse(self.fixture().get('events'))

    def test_black_initial_observation_never_sends_input(self):
        (self.root / 'blank').touch()
        state = self.start(allow_error=True)
        self.assertIn('blank', state['error'])
        self.assertEqual(state['observation_seq'], 0)
        self.assertFalse(self.fixture().get('events'))

    def test_white_initial_observation_never_sends_input(self):
        (self.root / 'white').touch()
        state = self.start(allow_error=True)
        self.assertIn('blank', state['error'])
        self.assertEqual(state['observation_seq'], 0)

    def test_one_pixel_does_not_bypass_black_frame_rule(self):
        (self.root / 'blank').touch()
        (self.root / 'speck').touch()
        self.assertIn('blank', self.start(allow_error=True)['error'])

    def test_consumption_retained_if_action_makes_screen_black(self):
        (self.root / 'blank-after-confirm').touch()
        self.start()
        self.client.resume()
        with self.assertRaisesRegex(RuntimeError, 'blank'):
            self.client.action('confirm', duration_ms=70, command_id='blank-action')
        receipt = self.client.result('blank-action')
        self.assertEqual(receipt['status'], 'failed')
        self.assertTrue(receipt['press_consumed'])
        self.assertTrue(receipt['release_consumed'])
        self.assertEqual(self.fixture()['phase'], 1)

    def test_screenshot_write_failure_keeps_consumption(self):
        self.start()
        self.client.resume()
        (self.directory / 'screenshots/fail-write.png').mkdir()
        with self.assertRaises(RuntimeError):
            self.client.action('confirm', duration_ms=60, command_id='fail-write')
        receipt = self.client.result('fail-write')
        self.assertEqual(receipt['status'], 'failed')
        self.assertTrue(receipt['press_consumed'])
        self.assertTrue(receipt['release_consumed'])
        self.assertTrue(self.client.status()['paused'])

    def test_malformed_control_releases_held_input(self):
        self.start()
        self.client.resume()
        self.request('bad-file', {'action': 'confirm', 'duration_ms': 1000})
        self.until(lambda: self.fixture().get('keyHeld'))
        (self.directory / 'lease.json').write_text('{', encoding='utf-8')
        self.until(lambda: (self.directory / 'results/bad-file.json').exists())
        receipt = self.client.result('bad-file')
        self.assertFalse(receipt['ok'])
        self.assertTrue(receipt['release_consumed'])
        self.until(lambda: not self.fixture()['keyHeld'])

    def test_unconfirmed_release_and_persistent_read_error_kills_job(self):
        (self.root / 'drop-release').touch()
        self.start()
        self.client.resume()
        self.request('no-release', {'action': 'confirm', 'duration_ms': 1000})
        self.until(lambda: self.fixture().get('keyHeld'))
        start = time.monotonic()
        (self.directory / 'lease.json').write_text('{', encoding='utf-8')
        self.until(lambda: self.process.poll() is not None, 3)
        self.assertLess(time.monotonic() - start, 1.6)
        self.assertFalse(_pid_path(self.meta['pid']))
        self.assertFalse((self.directory / 'results/no-release.json').exists())

    def test_renderer_stall_is_recovered_by_independent_host(self):
        self.check_stall('renderer-stall')

    def test_main_stall_is_recovered_by_independent_host(self):
        self.check_stall('main-stall')

    def check_stall(self, marker):
        (self.root / marker).touch()
        self.start()
        self.client.resume()
        start = time.monotonic()
        self.request('stalled', {'action': 'confirm', 'duration_ms': 70})
        self.until(lambda: self.process.poll() is not None, 3)
        self.assertLess(time.monotonic() - start, 1.7)
        self.assertFalse(_pid_path(self.meta['pid']))
        self.assertFalse((self.directory / 'results/stalled.json').exists())

    def test_shutdown_releases_active_input_before_exit(self):
        self.start()
        self.client.resume()
        self.request('shutdown-held', {'action': 'confirm', 'duration_ms': 1000})
        self.until(lambda: self.fixture().get('keyHeld'))
        started = time.monotonic()
        self.stop()
        self.assertLess(time.monotonic() - started, 1.6)
        self.assertEqual(self.process.returncode, 0)
        receipt = read_json(self.directory / 'results/shutdown-held.json')
        self.assertFalse(receipt['ok'])
        self.assertTrue(receipt['press_consumed'])
        self.assertTrue(receipt['release_consumed'])
        self.assertEqual(receipt['held'], [])
        self.assertEqual(read_json(self.directory / 'termination.json')['reason'], 'stop_requested')

    def test_shutdown_veto_is_bounded_by_independent_host(self):
        (self.root / 'prevent-quit').touch()
        self.start()
        started = time.monotonic()
        self.stop()
        self.assertLess(time.monotonic() - started, 1.6)
        self.assertEqual(read_json(self.directory / 'termination.json')['reason'], 'shutdown_timeout')
        self.assertFalse(_pid_path(self.meta['pid']))

    def test_capture_stall_is_bounded_by_independent_host(self):
        self.start()
        self.client.resume()
        self.request('capture-hung', {'action': 'confirm', 'duration_ms': 300})
        self.until(lambda: self.fixture().get('keyHeld'))
        (self.root / 'capture-stall').touch()
        self.until(lambda: read_json(self.directory / 'watchdog.json').get('capture_pending'))
        started = time.monotonic()
        self.until(lambda: self.process.poll() is not None, 4)
        elapsed = time.monotonic() - started
        self.assertGreater(elapsed, 2.7)
        self.assertLess(elapsed, 3.7)
        self.assertEqual(read_json(self.directory / 'termination.json')['reason'], 'capture_timeout')
        self.assertFalse(_pid_path(self.meta['pid']))
        self.assertFalse((self.directory / 'results/capture-hung.json').exists())
        self.assertFalse(self.fixture()['keyHeld'])

    def test_host_termination_kills_owned_process_tree(self):
        self.start()
        self.process.kill()
        self.process.wait(3)
        self.until(lambda: not _pid_path(self.meta['pid']), 2)

    def test_file_storage_normal_save_load_across_sessions(self):
        self.storage = 'file'
        self.check_save_load()
        saves = list(self.runtime.glob('*.sav'))
        self.assertTrue(saves)
        for save in saves:
            shutil.copy2(save, self.root / save.name)
            save.unlink()

    def test_localstorage_normal_save_load_across_sessions(self):
        self.check_save_load()
        self.assertTrue(list((self.root / 'runtime/userdata').rglob('*.log')))

    def check_save_load(self):
        self.start()
        self.client.resume()
        self.client.action('confirm', duration_ms=70)
        self.assertEqual(self.fixture()['phase'], 1)
        write_json(self.root / 'mode.json', {'action': 'qsave'})
        self.client.action('confirm', duration_ms=70)
        write_json(self.root / 'mode.json', {'action': 'next'})
        self.client.action('confirm', duration_ms=70)
        self.assertEqual(self.fixture()['phase'], 2)
        write_json(self.root / 'mode.json', {'action': 'qload'})
        self.client.action('confirm', duration_ms=70)
        self.until(lambda: self.fixture().get('phase') == 1)
        self.stop()
        self.start(session='new-session')
        self.assertTrue(self.client.status()['paused'])
        self.assertEqual(self.client.status()['session_id'], 'new-session')
        self.client.resume()
        self.client.action('confirm', duration_ms=70)
        self.until(lambda: self.fixture().get('phase') == 1)
        self.assertFalse(self.fixture()['keyHeld'])
        self.assertFalse((self.directory / 'results/no-release.json').exists())


if __name__ == '__main__':
    unittest.main()
