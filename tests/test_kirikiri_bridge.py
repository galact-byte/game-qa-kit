"""固定 KiriKiri 自写工程；继承共用宿主行为契约并扩展键查询实证。"""
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
from gameqakit.sessions import read_json, write_json
from gameqakit.win32 import _pid_path, window_pid
from tests import test_emuera_bridge as common

BASE = Path(__file__).resolve().parents[1]
PLUGIN = BASE / 'plugins/kirikiri'
COMPONENT = os.environ.get('GAMEQAKIT_TEST_KIRIKIRI', '')
DIGEST = '8db00ce0cd63748296d4e13b6916a4f1b5b166078f806d731202d15b210efe11'


@unittest.skipUnless(os.name == 'nt' and COMPONENT, '需 Windows 与 GAMEQAKIT_TEST_KIRIKIRI 固定 EXE')
class KiriKiriBridgeTests(common.EmueraBridgeTests):
    __unittest_skip__ = not (os.name == 'nt' and COMPONENT)

    @classmethod
    def setUpClass(cls):
        cls.source = Path(COMPONENT).resolve()
        if hashlib.sha256(cls.source.read_bytes()).hexdigest() != DIGEST:
            raise RuntimeError('固定 KiriKiri 运行时摘要不匹配')

    def setUp(self):
        self.assertTrue((PLUGIN / 'host.py').is_file(), '独立 KiriKiri 宿主尚未实现')
        self.temp = tempfile.TemporaryDirectory(prefix='gameqa-kirikiri-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        evidence = os.environ.get('GAMEQAKIT_TEST_EVIDENCE')
        if evidence:
            self.addCleanup(lambda root=self.root: shutil.copytree(root, Path(evidence) / self.id().split('.')[-1],
                dirs_exist_ok=True, ignore=shutil.ignore_patterns('*.exe', 'appdata', 'localappdata', 'tmp')))
        self.runtime = self.root / 'runtime'
        self.runtime.mkdir()
        self.exe = self.runtime / 'runtime.exe'
        shutil.copy2(self.source, self.exe)
        shutil.copy2(PLUGIN / 'samples/gameqa-kirikiri-fixture.tjs', self.runtime / 'startup.tjs')
        shutil.copy2(PLUGIN / 'GameQaBridge.tjs', self.runtime / 'GameQaBridge.tjs')
        self.directory = self.root / 'bridge'
        self.directory.mkdir()
        self.process = self.raw_target = self.meta = None
        self.session = 'fixture-session'
        self.host_script = PLUGIN / 'host.py'

    def start(self, *, session='fixture-session', allow_error=False):
        if self.process is not None:
            self.directory = self.root / ('bridge-' + session)
            self.directory.mkdir()
        self.session = session
        self.log = (self.root / 'host.txt').open('ab')
        self.addCleanup(self.log.close)
        self.process = subprocess.Popen([sys.executable, '-B', '-X', 'utf8', str(self.host_script),
            '--runtime', str(self.exe), '--directory', str(self.directory), '--session', session],
            cwd=BASE, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'},
            stdout=self.log, stderr=subprocess.STDOUT)
        self.addCleanup(self.stop)
        self.until(lambda: (self.directory / 'connection.json').exists() or self.process.poll() is not None, 15)
        if not allow_error:
            self.assertIsNone(self.process.poll(), (self.root / 'host.txt').read_text(encoding='utf-8'))
        self.meta = read_json(self.directory / 'connection.json')
        self.client = BridgeClient(self.directory, pid=self.meta['pid'], exe_path=str(self.exe))
        self.until(lambda: self.client.status().get('observation_seq', 0) > 0
            or self.client.status().get('error') or self.process.poll() is not None, 15)
        if not allow_error:
            self.assertIsNone(self.process.poll(), (self.root / 'host.txt').read_text(encoding='utf-8'))
        state = self.client.status()
        if not allow_error:
            self.assertTrue(state['connected'], str(state))
            self.assertFalse(state.get('error'), str(state))
        return state

    def set_testing_mode(self, **values):
        # Only the authored fixture reads this data; production host has no fault switch.
        (self.runtime / 'test-mode.tjs').write_text(
            ''.join(k + '=' + v + '\n' for k, v in values.items()), encoding='ascii')

    def test_idle_observations_preserve_actual_canvas(self):
        state = self.start()
        for _ in range(6):
            with Image.open(self.directory / state['observation_screenshot']) as image:
                self.assertEqual(image.convert('RGB').getpixel((0, 0)), (108, 52, 131))
                self.assertEqual(image.convert('RGB').getpixel((64, 128)), (52, 73, 94))
            previous = state['observation_seq']
            self.until(lambda: self.client.status().get('observation_seq', 0) > previous
                or self.process.poll() is not None, 3)
            state = self.client.status()
            self.assertFalse(state.get('error'), str(state))
        self.assertFalse(self.observer()['events'])
        self.assertTrue(state['paused'])

    def test_original_entry_paths_and_post_dispatch_consumption(self):
        u = ctypes.WinDLL('user32')
        u.GetForegroundWindow.restype = ctypes.c_void_p
        before = u.GetForegroundWindow()
        self.start()
        self.client.resume()
        for index in range(2):
            result = self.client.action('confirm', duration_ms=120, command_id='input-' + str(index))
            self.assertTrue(result['press_consumed'])
            self.assertTrue(result['release_consumed'])
        state = self.observer()
        self.assertEqual(state['engine'], 'kirikiri')
        self.assertEqual(Path(state['application_path']), self.exe)
        self.assertEqual(Path(state['data_path']), self.runtime / 'savedata')
        self.assertTrue(state['wrapped'])
        self.assertTrue(all(e['after_original_handler'] for e in state['events']))
        self.assertEqual([e['query'] for e in state['events']], [1, 0, 1, 0])
        self.assertEqual([e['handler_query'] for e in state['events']], [1, 0, 1, 0])
        self.assertTrue(all(e['other_key'] == e['other_native'] for e in state['events']))
        after = u.GetForegroundWindow()
        try:
            foreground_pid = window_pid(after) if after else None
        except RuntimeError:
            foreground_pid = None
        self.assertNotEqual(foreground_pid, self.meta['pid'])
        write_json(self.root / 'foreground.json', dict(before=before, after=after,
            target_pid=self.meta['pid'], foreground_pid=foreground_pid))
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), DIGEST)

    def test_control_read_contention_preserves_target_and_input(self):
        self.start()
        self.client.resume()
        first = self.client.action('confirm', duration_ms=60, command_id='before-contention')
        self.assertTrue(first['release_consumed'])
        self.client.pause()
        before = self.observer()
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
        kernel.CreateFileW.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        deadline = time.monotonic() + .2
        while True:
            handle = kernel.CreateFileW(str(self.directory / 'target-input.tjs'), 0x80000000,
                0, None, 3, 0x80, None)
            if handle != ctypes.c_void_p(-1).value:
                break
            if time.monotonic() >= deadline:
                raise ctypes.WinError(ctypes.get_last_error())
            time.sleep(.005)
        try:
            self.until(lambda: self.process.poll() is not None
                or self.observer().get('seq', 0) >= before['seq'] + 3, .6)
            self.assertIsNone(self.process.poll(), '共享读取冲突导致目标退出')
            self.assertEqual(self.observer()['events'], before['events'])
            self.assertFalse(self.observer()['held'])
        finally:
            kernel.CloseHandle(handle)
        self.client.resume()
        after = self.client.action('confirm', duration_ms=60, command_id='after-contention')
        self.assertTrue(after['press_consumed'])
        self.assertTrue(after['release_consumed'])
        self.assertEqual(len(self.observer()['events']), len(before['events']) + 2)

    def test_target_control_is_data_and_rejects_script_expressions(self):
        self.start()
        marker = (self.runtime / 'executed.tjs').as_posix()
        payload = '(function(){var x=%[executed:1];(Dictionary.saveStruct incontextof x)("' + marker + '","w");return %[];})()'
        (self.directory / 'target-input.tjs').write_text(payload, encoding='utf-8')
        time.sleep(.3)
        self.assertFalse((self.runtime / 'executed.tjs').exists(), '控制数据被当作 TJS 执行')
        self.assertFalse(self.observer().get('events'))

    def test_wrong_runtime_hash_rejected_before_launch(self):
        with self.exe.open('ab') as stream:
            stream.write(b'wrong-build')
        result = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(PLUGIN / 'host.py'),
            '--runtime', str(self.exe), '--directory', str(self.directory), '--session', self.session],
            cwd=BASE, capture_output=True, timeout=4)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('运行时摘要未验证', result.stderr.decode('utf-8'))
        self.assertFalse((self.directory / 'connection.json').exists())

    def test_normal_save_load_in_process_and_across_sessions(self):
        self.start()
        self.client.resume()
        saved = self.client.action('confirm', duration_ms=70, command_id='save-normal')
        self.assertTrue(saved['release_consumed'])
        save = self.runtime / 'savedata/slot01.tjs'
        self.assertTrue(save.is_file())
        digest = hashlib.sha256(save.read_bytes()).hexdigest()
        self.client.action('confirm', duration_ms=70, command_id='change-normal')
        self.assertEqual(self.observer()['value'], 999)
        loaded = self.client.action('confirm', duration_ms=70, command_id='load-normal')
        self.assertEqual(self.observer()['value'], 123)
        self.assert_loaded_image(loaded['screenshot'])
        self.stop()
        self.assertTrue(read_json(self.directory / 'detached.json')['restored'])
        self.start(session='storage-new-session')
        self.assertTrue(self.client.status()['paused'])
        self.client.resume()
        loaded = self.client.action('confirm', duration_ms=70, command_id='load-new-session')
        self.assertEqual(self.observer()['value'], 123)
        self.assert_loaded_image(loaded['screenshot'])
        self.assertEqual(hashlib.sha256(save.read_bytes()).hexdigest(), digest)
        self.stop()
        shutil.copy2(save, self.root / 'self-authored-slot01.tjs')
        write_json(self.root / 'storage-ledger.json', dict(slot=1, before='absent',
            file='runtime/savedata/slot01.tjs', sha256=digest, in_process=True, new_session=True,
            restore='Remove only generated fixture data; no original save copied or modified.'))

    def test_original_handler_error_does_not_acknowledge_consumption(self):
        self.set_testing_mode(action='handler-error')
        self.start()
        self.client.resume()
        self.request('original-failed', {'action': 'confirm', 'duration_ms': 60})
        self.until(lambda: self.process.poll() is not None, 4)
        self.assertFalse(self.observer().get('events'))
        ended = read_json(self.directory / 'termination.json')
        self.assertFalse(ended['press_consumed'])
        self.assertFalse(ended['release_consumed'])
        self.assertFalse(_pid_path(self.meta['pid']))

    def test_detach_preserves_bindings_replaced_by_game(self):
        from plugins.kirikiri.host import read_struct
        self.set_testing_mode(action='replace-bindings')
        self.start()
        self.client.resume()
        self.request('replace-bindings', {'action': 'confirm', 'duration_ms': 60})
        self.until(lambda: self.process.poll() is not None, 4)
        report = read_struct(self.runtime / 'replacement-preserved.tjs')
        self.assertEqual(report, {'query': 37, 'down': 1, 'up': 1})
        detached = read_struct(self.directory / 'detached.tjs')
        self.assertEqual(detached, {'restored': 0, 'query_preserved': 1, 'down_preserved': 1, 'up_preserved': 1})

    def test_status_publication_failure_still_releases_input_and_bounds_shutdown(self):
        self.start()
        self.client.resume()
        self.request('status-write-failure', {'action': 'confirm', 'duration_ms': 1000})
        self.until(lambda: any(event['kind'] == 'down' for event in self.observer().get('events', [])), 2)
        # 阻塞宿主自己的临时状态路径，不修改目标文件或放宽保护期限。
        def block_publication():
            try:
                (self.directory / 'status.json.part').mkdir()
            except FileExistsError:
                # 宿主可能正在发布；等它原子替换后再占用路径。
                return False
            return True

        self.until(block_publication, .25)
        self.until(lambda: any(event['kind'] == 'up' for event in self.observer().get('events', [])), 2)
        observed = self.observer()
        self.assertFalse(observed['held'])
        self.assertEqual(len(observed['events']), 2)
        self.assertFalse((self.directory / 'results/status-write-failure.json').exists())
        started = time.monotonic()
        self.stop()
        self.assertLess(time.monotonic() - started, 2)
        self.assertFalse(_pid_path(self.meta['pid']))

    def test_truncated_observer_stops_progress_without_resetting_ui_deadline(self):
        self.set_testing_mode(action='block-down')
        self.start()
        self.client.resume()
        self.request('partial-observer', {'action': 'confirm', 'duration_ms': 60})
        time.sleep(.1)
        (self.directory / 'target-observer.tjs').write_text('%["seq"=>', encoding='ascii')
        started = time.monotonic()
        self.until(lambda: self.process.poll() is not None, 3)
        self.assertLess(time.monotonic() - started, 2)
        ended = read_json(self.directory / 'termination.json')
        self.assertEqual(ended['reason'], 'activity_timeout')
        self.assertFalse(self.observer().get('events'))

    def test_internal_record_rejects_identity_fields_and_truncation(self):
        cases = {
            'wrong-pid': 'GQK1\nseq=2\nsession=fixture-session\npid=1\nid=bad\nkind=down\nend=2\n',
            'wrong-session': 'GQK1\nseq=2\nsession=old\npid={pid}\nid=bad\nkind=down\nend=2\n',
            'truncated': 'GQK1\nseq=2\nsession=fixture-session\npid={pid}\nid=bad\nkind=down\n',
            'unknown-field': 'GQK1\nseq=2\nsession=fixture-session\npid={pid}\nid=bad\nkind=down\nextra=1\nend=2\n',
            'non-integer': 'GQK1\nseq=2.5\nsession=fixture-session\npid={pid}\nid=bad\nkind=down\nend=2.5\n',
            'unsafe-id': 'GQK1\nseq=2\nsession=fixture-session\npid={pid}\nid=../bad\nkind=down\nend=2\n',
        }
        for index, (name, record) in enumerate(cases.items()):
            with self.subTest(name=name):
                case_session = 'record-case-' + str(index)
                self.start(session=case_session)
                (self.directory / 'target-input.tjs').write_text(
                    record.replace('{pid}', str(self.meta['pid'])).replace('fixture-session', case_session), encoding='ascii')
                self.until(lambda: self.process.poll() is not None, 3)
                self.assertFalse(self.observer().get('events'))
                self.assertFalse(_pid_path(self.meta['pid']))

    def test_internal_record_replay_does_not_repeat_input(self):
        self.start()
        self.client.resume()
        self.client.action('confirm', duration_ms=60, command_id='replay')
        record = (self.directory / 'target-input.tjs').read_text(encoding='ascii')
        (self.directory / 'target-input.tjs').write_text(record, encoding='ascii')
        time.sleep(.1)
        self.assertEqual(len(self.observer()['events']), 2)
        lines = record.splitlines()
        lines[1] = 'seq=1'
        lines[-1] = 'end=1'
        (self.directory / 'target-input.tjs').write_text('\n'.join(lines) + '\n', encoding='ascii')
        self.until(lambda: self.process.poll() is not None, 3)
        self.assertEqual(len(self.observer()['events']), 2)

    def test_non_fixture_resources_and_used_session_rejected_before_launch(self):
        for name in ('data.xp3', 'plugin.dll', 'runtime.cf'):
            extra = self.runtime / name
            extra.write_text('unauthorized', encoding='ascii')
            self.addCleanup(lambda path=extra: path.unlink(missing_ok=True))
            result = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(PLUGIN / 'host.py'),
                '--runtime', str(self.exe), '--directory', str(self.directory), '--session', self.session],
                cwd=BASE, capture_output=True, timeout=4)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('工程目录含未授权资源', result.stderr.decode('utf-8'))
            self.assertFalse((self.directory / 'connection.json').exists())
            extra.unlink()
        (self.directory / 'old-evidence.json').write_text('{}', encoding='ascii')
        result = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(PLUGIN / 'host.py'),
            '--runtime', str(self.exe), '--directory', str(self.directory), '--session', self.session],
            cwd=BASE, capture_output=True, timeout=4)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('必须使用全新的空会话目录', result.stderr.decode('utf-8'))
        self.assertFalse((self.directory / 'connection.json').exists())

    def test_capture_hang_is_bounded_and_worker_is_reaped(self):
        stub = self.root / 'capture-stub.py'
        worker = PLUGIN / 'capture_worker.py'
        stub.write_text('from pathlib import Path\nimport sys,time,runpy\n'
            'if (Path(sys.argv[1])/"hang-capture").exists():\n time.sleep(60)\n'
            f'runpy.run_path({str(worker)!r},run_name="__main__")\n', encoding='utf-8')
        self.host_script = self.root / 'host-fixture.py'
        self.host_script.write_text('import runpy,sys\n'
            f'host=runpy.run_path({str(PLUGIN / "host.py")!r})\n'
            f'host["Supervisor"].capture_worker={str(stub)!r}\n'
            'from pathlib import Path\n'
            'host["run"](Path(sys.argv[2]),Path(sys.argv[4]),sys.argv[6])\n', encoding='utf-8')
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


if __name__ == '__main__':
    unittest.main()
