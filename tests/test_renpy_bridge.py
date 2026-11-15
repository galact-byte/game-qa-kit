"""Ren'Py 实验桥的真实运行回归；只运行自写工程。"""
from pathlib import Path
import ctypes
import hashlib
import json
import os
import shutil
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from PIL import Image, ImageChops
from gameqakit.control import BridgeClient, write_connection
from gameqakit.desktop import DesktopProcess
from gameqakit.sessions import read_json, write_json

BRIDGE = Path(__file__).resolve().parents[1] / 'plugins/renpy/GameQaBridge.rpy'
RUNTIME = os.environ.get('GAMEQAKIT_TEST_RENPY', '')

FIXTURE = '''define config.save_directory = "gameqa-renpy-fixture"
init python:
    import json
    import os
    import pygame_sdl2 as pygame
    import time
    config.name = "GameQA RenPy Fixture"
    config.screen_width = 320
    config.screen_height = 240
    config.autosave_on_quit = False
    config.autosave_frequency = None
    config.developer = False
    fixture_x = 60
    fixture_held = False
    fixture_confirms = 0
    fixture_cancels = 0
    fixture_events = []
    fixture_mp = MultiPersistent("gameqa-renpy-fixture", save_on_quit=False)
    class Fixture(renpy.Displayable):
        def render(self, width, height, st, at):
            result = renpy.Render(320, 240)
            blank = os.path.exists(os.path.join(os.environ["GAMEQA_FIXTURE_ROOT"], "blank"))
            result.canvas().rect("#405060" if os.path.exists(os.path.join(os.environ["GAMEQA_FIXTURE_ROOT"], "background")) else "#000000" if blank else "#203040", (0, 0, 320, 240))
            result.canvas().rect("#000000" if blank else "#ff8020", (fixture_x, 100, 32, 32))
            if os.path.exists(os.path.join(os.environ["GAMEQA_FIXTURE_ROOT"], "speck")):
                result.canvas().rect("#ffffff", (2, 2, 1, 1))
            renpy.redraw(self, .05)
            return result
        def event(self, ev, x, y, st):
            global fixture_x, fixture_held, fixture_confirms, fixture_cancels
            if ev.type in (pygame.KEYDOWN, pygame.KEYUP):
                if ev.key == pygame.K_RIGHT:
                    fixture_held = ev.type == pygame.KEYDOWN
                    if fixture_held:
                        fixture_x += 80
                if ev.type == pygame.KEYDOWN and ev.key == pygame.K_RETURN:
                    fixture_confirms += 1
                    if os.path.exists(os.path.join(os.environ["GAMEQA_FIXTURE_ROOT"], "blank-after-confirm")):
                        open(os.path.join(os.environ["GAMEQA_FIXTURE_ROOT"], "blank"), "w").close()
                    if os.path.exists(os.path.join(os.environ["GAMEQA_FIXTURE_ROOT"], "stall")):
                        time.sleep(3)
                    if os.path.exists(os.path.join(os.environ["GAMEQA_FIXTURE_ROOT"], "save")):
                        renpy.save("1-1", "GameQA fixture")
                        persistent.gameqa_fixture = "isolated"
                        renpy.save_persistent()
                        fixture_mp.marker = "isolated"
                        fixture_mp.save()
                        with open(os.path.join(os.environ["GAMEQA_FIXTURE_ROOT"], "paths.json"), "w") as stream:
                            json.dump(dict(locations=[item.directory for item in renpy.loadsave.location.locations],
                                savedir=config.savedir, multipersistent=fixture_mp._filename,
                                tokens=renpy.savetoken.token_dir), stream)
                if ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
                    fixture_cancels += 1
                    if os.path.exists(os.path.join(os.environ["GAMEQA_FIXTURE_ROOT"], "load")):
                        renpy.load("1-1")
                if ev.type == pygame.KEYDOWN and os.path.exists(os.path.join(os.environ["GAMEQA_FIXTURE_ROOT"], "drop-draw")):
                    renpy.test.testexecution.execute = lambda: None
                fixture_events.append(dict(type="down" if ev.type == pygame.KEYDOWN else "up", key=ev.key))
                renpy.redraw(self, 0)
            with open(os.path.join(os.environ["GAMEQA_FIXTURE_ROOT"], "fixture.json"), "w") as stream:
                json.dump(dict(x=fixture_x, held=fixture_held, confirms=fixture_confirms,
                    cancels=fixture_cancels, events=fixture_events), stream)
            return None
    fixture_displayable = Fixture()
screen fixture():
    add fixture_displayable
label main_menu:
    jump start
label start:
    call screen fixture
'''


@unittest.skipUnless(os.name == 'nt' and RUNTIME, '需 Windows 与 GAMEQAKIT_TEST_RENPY 已有复制运行时目录')
class RenpyBridgeTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(BRIDGE.is_file(), 'RenPy 控制桥尚未实现')
        self.temp = tempfile.TemporaryDirectory(prefix='gameqa-renpy-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        evidence = os.environ.get('GAMEQAKIT_TEST_EVIDENCE')
        if evidence:
            self.addCleanup(lambda: shutil.copytree(self.root, Path(evidence) / self.id().split('.')[-1],
                dirs_exist_ok=True, ignore=shutil.ignore_patterns('appdata', 'localappdata', 'tmp', '*.rpyc')))
        for name in ('game', 'bridge', 'tmp', 'appdata', 'localappdata', 'saves', 'multipersistent'):
            (self.root / name).mkdir()
        (self.root / 'game/script.rpy').write_text(FIXTURE, encoding='utf-8')
        shutil.copy2(BRIDGE, self.root / 'game/GameQaBridge.rpy')
        self.directory = self.root / 'bridge'
        self.exe = Path(RUNTIME).resolve() / 'lib/py3-windows-x86_64/pythonw.exe'

    def until(self, condition, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = condition()
            if value:
                return value
            time.sleep(.02)
        logs = '\n'.join(path.read_text(encoding='utf-8', errors='replace')[-3000:]
                         for path in self.root.glob('*.txt'))
        self.fail('RenPy 条件未达成：' + logs)

    def start(self, *, wrong_session=False, engine='renpy', allow_error=False):
        self.host = DesktopProcess([str(self.exe), str(Path(RUNTIME).resolve() / 'Eternum.py'),
            str(self.root), '--savedir', str(self.root / 'saves/main')], cwd=self.root, env={
                'GAMEQAKIT_BRIDGE_DIR': str(self.directory), 'GAMEQAKIT_SESSION_ID': 'fixture-session',
                'GAMEQA_FIXTURE_ROOT': str(self.root), 'RENPY_PATH_TO_SAVES': str(self.root / 'saves'),
                'RENPY_MULTIPERSISTENT': str(self.root / 'multipersistent'),
                'APPDATA': str(self.root / 'appdata'), 'LOCALAPPDATA': str(self.root / 'localappdata'),
                'TMP': str(self.root / 'tmp'), 'TEMP': str(self.root / 'tmp')})
        self.host.__enter__()
        self.addCleanup(self.host.close)
        write_connection(self.directory, {'protocol': 2, 'session_id': 'other-session' if wrong_session else 'fixture-session',
            'pid': self.host.pid, 'exe_path': str(self.exe), 'engine': engine})
        self.client = BridgeClient(self.directory, pid=self.host.pid, exe_path=str(self.exe))
        self.host.run()
        self.until(lambda: (self.client.status().get('observation_seq', 0) > 0
            or self.client.status().get('error') if (self.directory / 'status.json').exists() else False)
            or not self.host.alive(), timeout=15)
        self.assertTrue(self.host.alive(), '引擎在发布状态前退出')
        state = self.client.status()
        if not allow_error:
            self.assertFalse(state.get('error'), str(state))
        return state

    def fixture(self):
        try:
            return read_json(self.root / 'fixture.json')
        except (OSError, ValueError):
            return {}

    def test_starts_paused_with_actual_preview_and_time_semantics(self):
        state = self.start()
        self.assertTrue(state['connected'])
        self.assertTrue(state['paused'])
        self.assertEqual(state['held'], [])
        self.assertEqual(state['timing_mode'], 'duration_ms')
        self.assertEqual(state['pause_scope'], 'input')
        self.assertIs(state['simulation_pause'], False)
        self.assertNotIn('frame', state)
        with Image.open(self.directory / state['observation_screenshot']) as shot:
            self.assertGreater(len(shot.getcolors(100000) or []), 1)
        with self.assertRaisesRegex(RuntimeError, '暂停'):
            self.client.action('move', direction='right', duration_ms=100)

    def test_movement_is_read_by_scene_and_release_precedes_receipt(self):
        state = self.start()
        before = Image.open(self.directory / state['observation_screenshot']).convert('RGB')
        self.client.resume()
        result = self.client.action('move', direction='right', duration_ms=120, command_id='move-001')
        self.assertTrue(result['press_consumed'])
        self.assertTrue(result['release_consumed'])
        self.assertEqual(self.fixture()['x'], 140)
        self.assertFalse(self.fixture()['held'])
        self.assertEqual([event['type'] for event in self.fixture()['events']], ['down', 'up'])
        self.assertGreaterEqual(result['completed_monotonic_ms'] - result['started_monotonic_ms'], 120)
        with Image.open(result['screenshot']) as shot:
            self.assertIsNotNone(ImageChops.difference(before, shot.convert('RGB')).getbbox())
        self.assertEqual(hashlib.sha256(Path(result['screenshot']).read_bytes()).hexdigest(), result['screenshot_sha256'])
    def test_confirm_cancel_and_command_retry_do_not_duplicate_input(self):
        self.start()
        self.client.resume()
        first = self.client.action('confirm', duration_ms=50, command_id='confirm-001')
        again = self.client.action('confirm', duration_ms=50, command_id='confirm-001')
        self.assertEqual(first['screenshot_sha256'], again['screenshot_sha256'])
        self.assertEqual(self.fixture()['confirms'], 1)
        self.client.action('cancel', duration_ms=50)
        self.assertEqual(self.fixture()['cancels'], 1)
        self.assertEqual(len(self.fixture()['events']), 4)

    def test_wait_and_resume_never_claim_input_consumption(self):
        self.start()
        resumed = self.client.resume()
        result = self.client.action('wait', duration_ms=100)
        for receipt in (resumed, result):
            self.assertIs(receipt['consumed'], False)
            self.assertIs(receipt['press_consumed'], False)
            self.assertIs(receipt['release_consumed'], False)
        self.assertFalse(self.fixture().get('events', []))
        self.assertGreaterEqual(result['completed_monotonic_ms'] - result['started_monotonic_ms'], 100)

    def test_pause_waits_for_scene_to_read_release(self):
        self.start()
        self.client.resume()
        with ThreadPoolExecutor(max_workers=1) as executor:
            running = executor.submit(self.client.action, 'move', direction='right', duration_ms=1000,
                                      command_id='pause-held')
            self.until(lambda: self.fixture().get('held'))
            self.client.pause()
            self.until(lambda: self.client.status().get('paused') and not self.client.status().get('held'))
            self.assertFalse(self.fixture()['held'])
            with self.assertRaises(RuntimeError):
                running.result(timeout=4)
        result = self.client.result('pause-held')
        self.assertEqual(result['status'], 'failed')
        self.assertTrue(result['press_consumed'])
        self.assertTrue(result['release_consumed'])
        self.assertFalse(self.fixture()['held'])

    def test_expired_lease_releases_input_without_control_client(self):
        self.start()
        self.client.resume()
        write_json(self.directory / 'lease.json', {'session_id': 'fixture-session', 'expires_at': time.time() + .25})
        write_json(self.directory / 'request.json', dict(protocol=2, session_id='fixture-session', id='lease-held',
            payload=dict(action='move', direction='right', duration_ms=1000), pause_id='', after_observation_seq=2))
        self.until(lambda: self.fixture().get('held'))
        self.until(lambda: (self.directory / 'results/lease-held.json').exists())
        receipt = read_json(self.directory / 'results/lease-held.json')
        self.assertFalse(receipt['ok'])
        self.assertTrue(receipt['release_consumed'])
        self.assertFalse(self.fixture()['held'])
        self.assertTrue(self.client.status()['paused'])

    def test_initial_black_frame_stops_without_input(self):
        (self.root / 'blank').touch()
        state = self.start(allow_error=True)
        self.assertIn('blank', state['error'])
        self.assertEqual(state['observation_seq'], 0)
        self.assertTrue(state['paused'])
        self.assertFalse(self.fixture().get('events', []))

    def test_single_bright_pixel_does_not_make_black_frame_usable(self):
        (self.root / 'blank').touch()
        (self.root / 'speck').touch()
        state = self.start(allow_error=True)
        self.assertIn('blank', state['error'])
        self.assertEqual(state['observation_seq'], 0)

    def test_consumed_confirm_is_retained_when_same_action_turns_screen_black(self):
        (self.root / 'blank-after-confirm').touch()
        self.start()
        self.client.resume()
        with self.assertRaisesRegex(RuntimeError, 'blank'):
            self.client.action('confirm', duration_ms=50, command_id='confirm-blank')
        receipt = self.client.result('confirm-blank')
        self.assertEqual(receipt['status'], 'failed')
        self.assertTrue(receipt['press_consumed'])
        self.assertTrue(receipt['release_consumed'])
        self.assertEqual(self.fixture()['confirms'], 1)
        self.assertTrue(self.client.status()['paused'])

    def test_screenshot_publication_failure_retains_real_movement_and_release(self):
        self.start()
        self.client.resume()
        (self.directory / 'screenshots/write-failure.png').mkdir()
        with self.assertRaises(RuntimeError):
            self.client.action('move', direction='right', duration_ms=50, command_id='write-failure')
        receipt = self.client.result('write-failure')
        self.assertFalse(receipt['ok'])
        self.assertTrue(receipt['press_consumed'])
        self.assertTrue(receipt['release_consumed'])
        self.assertEqual(self.fixture()['x'], 140)
        self.assertFalse(self.fixture()['held'])
        self.assertTrue(self.client.status()['paused'])

    def test_wrong_session_never_publishes_usable_observation(self):
        state = self.start(wrong_session=True, allow_error=True)
        self.assertFalse(state['connected'])
        self.assertEqual(state.get('observation_seq', 0), 0)
        self.assertFalse(self.fixture().get('events', []))

    def test_event_loop_stall_is_terminated_without_success_receipt(self):
        (self.root / 'stall').touch()
        self.start()
        self.client.resume()
        write_json(self.directory / 'lease.json', {'session_id': 'fixture-session', 'expires_at': time.time() + 1.5})
        write_json(self.directory / 'request.json', dict(protocol=2, session_id='fixture-session', id='stall-event',
            payload=dict(action='confirm', duration_ms=50), pause_id='', after_observation_seq=2))
        self.until(lambda: not self.host.alive(), timeout=4)
        self.assertFalse((self.directory / 'results/stall-event.json').exists())
        self.until(lambda: not self.client.status()['connected'], timeout=3)

    def test_normal_input_saves_only_inside_test_project(self):
        (self.root / 'save').touch()
        self.start()
        self.client.resume()
        self.client.action('confirm', duration_ms=50)
        paths = read_json(self.root / 'paths.json')
        for path in paths['locations'] + [paths['savedir'], paths['multipersistent'], paths['tokens']]:
            self.assertTrue(Path(path).resolve().is_relative_to(self.root.resolve()), path)
        self.assertTrue(list((self.root / 'saves/main').glob('*.save')))
        self.assertTrue(list((self.root / 'multipersistent').iterdir()))
        self.assertTrue((self.root / 'saves/tokens/security_keys.txt').is_file())

    def test_paused_observation_updates_without_sending_input(self):
        state = self.start()
        with Image.open(self.directory / state['observation_screenshot']) as shot:
            before = shot.convert('RGB')
        (self.root / 'background').touch()
        def changed():
            current = self.client.status()
            if current.get('observation_seq', 0) <= state['observation_seq']:
                return False
            with Image.open(self.directory / current['observation_screenshot']) as shot:
                return ImageChops.difference(before, shot.convert('RGB')).getbbox()
        self.until(changed, timeout=2)
        self.assertTrue(self.client.status()['paused'])
        self.assertFalse(self.fixture().get('events', []))

    def test_normal_save_load_does_not_restore_bridge_session_or_held_input(self):
        (self.root / 'save').touch()
        self.start()
        self.client.resume()
        self.client.action('confirm', duration_ms=50)
        self.client.action('move', direction='right', duration_ms=50)
        self.assertEqual(self.fixture()['x'], 140)
        (self.root / 'load').touch()
        self.client.action('cancel', duration_ms=50, command_id='load-fixture')
        self.client.action('move', direction='right', duration_ms=50)
        self.assertEqual(self.fixture()['x'], 140)
        self.assertFalse(self.fixture()['held'])
        self.assertTrue(self.client.result('load-fixture')['release_consumed'])
        self.assertEqual(self.client.status()['held'], [])

    def test_wrong_engine_never_publishes_usable_observation(self):
        state = self.start(engine='native', allow_error=True)
        self.assertTrue(state.get('error'))
        self.assertEqual(state.get('observation_seq', 0), 0)
        self.assertFalse(self.fixture().get('events', []))

    def test_boolean_duration_is_rejected_before_any_input(self):
        self.start()
        self.client.resume()
        write_json(self.directory / 'request.json', dict(protocol=2, session_id='fixture-session', id='bad-bool',
            payload=dict(action='move', direction='right', duration_ms=True), pause_id='', after_observation_seq=1))
        self.until(lambda: self.client.status().get('error'))
        self.assertTrue(self.client.status()['paused'])
        self.assertFalse(self.fixture().get('events', []))

    def test_missing_draw_ack_ends_process_after_release_deadline(self):
        (self.root / 'drop-draw').touch()
        self.start()
        self.client.resume()
        write_json(self.directory / 'lease.json', {'session_id': 'fixture-session', 'expires_at': time.time() + 1.5})
        write_json(self.directory / 'request.json', dict(protocol=2, session_id='fixture-session', id='missing-draw',
            payload=dict(action='move', direction='right', duration_ms=50), pause_id='', after_observation_seq=1))
        self.until(lambda: not self.host.alive(), timeout=5)
        state = read_json(self.directory / 'status.json')
        self.assertLess(time.time() - state['updated_at'], .5, '事件循环仍活跃，由释放期限结束本批')
        self.assertFalse((self.directory / 'results/missing-draw.json').exists())

    def test_repeated_pause_and_read_errors_cannot_extend_release_deadline(self):
        (self.root / 'drop-draw').touch()
        self.start()
        self.client.resume()
        write_json(self.directory / 'lease.json', {'session_id': 'fixture-session', 'expires_at': time.time() + 1.5})
        write_json(self.directory / 'request.json', dict(protocol=2, session_id='fixture-session', id='repeat-release',
            payload=dict(action='move', direction='right', duration_ms=50), pause_id='', after_observation_seq=1))
        self.until(lambda: self.client.status().get('held'))
        for number in range(4):
            write_json(self.directory / 'pause.json', {'session_id': 'fixture-session', 'id': f'pause-{number}'})
            time.sleep(.1)
        (self.directory / 'pause.json').write_text('{', encoding='utf8')
        self.until(lambda: not self.host.alive(), timeout=5)
        state = read_json(self.directory / 'status.json')
        self.assertLess(time.time() - state['updated_at'], .5, '读取持续失败时释放期限仍须独立生效')
        self.assertFalse((self.directory / 'results/repeat-release.json').exists())

    def test_short_read_sharing_conflict_does_not_permanently_disable_bridge(self):
        self.start()
        path = self.directory / 'pause.json'
        write_json(path, {'session_id': 'fixture-session', 'id': 'share-test'})
        self.until(lambda: self.client.status().get('pause_id') == 'share-test')
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                                      ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
        kernel.CreateFileW.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        # 文件可被替换或写入，但在句柄释放前拒绝新的读取者。
        for _ in range(4):
            handle = kernel.CreateFileW(str(path), 0x80000000, 2 | 4, None, 3, 0, None)
            self.assertNotEqual(handle, ctypes.c_void_p(-1).value, ctypes.get_last_error())
            try:
                time.sleep(.075)
            finally:
                kernel.CloseHandle(handle)
            sequence = self.client.status().get('observation_seq', 0)
            self.until(lambda: self.client.status().get('observation_seq', 0) > sequence)
            self.assertFalse(self.client.status().get('error'))
        self.assertTrue(self.client.status()['paused'])
        self.assertFalse(self.fixture().get('events', []))

    def test_process_exit_disconnects_control(self):
        self.start()
        self.host.close()
        self.assertFalse(self.client.status()['connected'])
        with self.assertRaises(RuntimeError):
            self.client.resume()


if __name__ == '__main__':
    unittest.main()
