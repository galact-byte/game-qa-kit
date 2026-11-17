"""原生守卫与自写 KAG 的同批端到端验证；不含商业资源。"""
import hashlib
import os
from pathlib import Path
import shutil
import unittest
from tests.test_kirikiri_kag import KagBridgeTests, BASE
from tests.test_kirikiri_bridge import KiriKiriBridgeTests
from tests.test_emuera_bridge import EmueraBridgeTests as Lifecycle
from plugins.kirikiri.build import build


@unittest.skipUnless(os.name == 'nt' and os.environ.get('GAMEQAKIT_TEST_KIRIKIRI'), '需固定本地运行时')
class GuardedKagTests(KagBridgeTests):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        import tempfile
        cls.build_root = Path(tempfile.mkdtemp(prefix='kirikiri-kag-build-', dir='E:/DevData'))
        cls.addClassCleanup(shutil.rmtree, cls.build_root)
        cls.guard = build(cls.build_root / 'KiriKiriGuard.dll', mode='guard', shell_counter_experiment=True)
        cls.strict_guard = build(cls.build_root / 'StrictGuard.dll', mode='guard')
        cls.native_host = build(cls.build_root / 'FixtureHost.exe', mode='host', fixture_target=cls.source)

    def setUp(self):
        super().setUp()
        self.runtime.rename(self.root / 'game')
        self.runtime = self.root / 'game'
        self.exe = self.runtime / 'runtime.exe'
        (self.runtime / 'GameQaBridge.tjs').unlink()
        for folder in ('savedata', 'appdata', 'localappdata', 'tmp'):
            (self.root / folder).mkdir()
        self.host_script.write_text(
            'import sys\nfrom pathlib import Path\n'
            f'sys.path.insert(0,{str(BASE)!r})\n'
            'from plugins.kirikiri.guarded import GuardedTarget\n'
            'from plugins.kirikiri.kag_host import Supervisor\n'
            'root,directory,session=Path(sys.argv[2]).parent.parent,Path(sys.argv[4]),sys.argv[6]\n'
            f'with GuardedTarget(root,Path({str(self.native_host)!r}),Path({str(self.guard)!r})) as target:\n'
            ' Supervisor(target,directory,session).run()\n', encoding='utf-8')

    def start(self, *, session='fixture-session', allow_error=False):
        if not hasattr(self, 'fixture_root'):
            self.fixture_root = self.root
            self.session_count = 0
        if (self.root / 'process.json').exists():
            if self.process is not None and self.process.poll() is None:
                raise RuntimeError('前一测试会话尚未停止')
            self.session_count += 1
            self.root = self.fixture_root / ('session-' + str(self.session_count))
            self.root.mkdir()
            previous_runtime = self.runtime
            self.runtime = self.root / 'game'
            shutil.copytree(previous_runtime, self.runtime)
            self.exe = self.runtime / 'runtime.exe'
            for folder in ('savedata', 'appdata', 'localappdata', 'tmp', 'bridge'):
                (self.root / folder).mkdir()
            self.directory = self.root / 'bridge'
            self.process = None
        return super().start(session=session, allow_error=allow_error)

    # The authored KAG screenshot is different from the Emuera colour fixture.
    test_enter_changes_screen_and_receipt_requires_release = None
    test_black_white_and_speck_initial_images_send_no_input = Lifecycle.test_black_white_and_speck_initial_images_send_no_input
    test_idle_observations_preserve_actual_canvas = KiriKiriBridgeTests.test_idle_observations_preserve_actual_canvas
    test_initial_pause_and_continuing_observations = Lifecycle.test_initial_pause_and_continuing_observations
    test_wait_and_unsupported_actions = Lifecycle.test_wait_and_unsupported_actions
    test_manual_pause_waits_for_target_release = Lifecycle.test_manual_pause_waits_for_target_release
    test_lease_expiry_releases_input = Lifecycle.test_lease_expiry_releases_input
    test_bad_identity_generation_and_expired_request_send_no_input = Lifecycle.test_bad_identity_generation_and_expired_request_send_no_input
    test_stale_pause_and_expired_request_send_no_input = Lifecycle.test_stale_pause_and_expired_request_send_no_input
    test_raw_unsupported_action_and_unsafe_id_never_send_input = Lifecycle.test_raw_unsupported_action_and_unsafe_id_never_send_input
    test_pid_and_path_binding_fail_before_input = Lifecycle.test_pid_and_path_binding_fail_before_input
    test_screenshot_write_failure_retains_consumption = Lifecycle.test_screenshot_write_failure_retains_consumption
    test_persistent_control_read_error_releases_input = Lifecycle.test_persistent_control_read_error_releases_input
    test_stalled_ui_is_killed_by_independent_host = Lifecycle.test_stalled_ui_is_killed_by_independent_host
    test_unconfirmed_release_with_repeated_pause_and_read_error_keeps_deadline = Lifecycle.test_unconfirmed_release_with_repeated_pause_and_read_error_keeps_deadline
    test_shutdown_releases_active_input_before_exit = Lifecycle.test_shutdown_releases_active_input_before_exit
    test_host_termination_kills_owned_job = Lifecycle.test_host_termination_kills_owned_job
    test_consumption_retained_when_action_turns_screen_black = Lifecycle.test_consumption_retained_when_action_turns_screen_black

    def test_full_input_changes_screen_and_deduplicates(self):
        from PIL import Image, ImageChops
        from gameqakit.sessions import read_json
        state = self.start()
        with Image.open(self.directory / state['observation_screenshot']) as image:
            before = image.convert('RGB')
        self.client.resume()
        result = self.client.action('confirm', duration_ms=100, command_id='one')
        self.assertTrue(result['press_consumed'])
        self.assertTrue(result['release_consumed'])
        self.assertEqual(result['held'], [])
        self.assertGreaterEqual(result['completed_monotonic_ms'] - result['started_monotonic_ms'], 100)
        self.assertEqual([event['kind'] for event in self.observer()['events']], ['down', 'up'])
        self.assertEqual(hashlib.sha256(Path(result['screenshot']).read_bytes()).hexdigest(), result['screenshot_sha256'])
        with Image.open(result['screenshot']) as image:
            after = image.convert('RGB')
        self.assertIsNotNone(ImageChops.difference(before, after).getbbox())
        self.assertGreater(sum(pixel == (22, 138, 82) for pixel in after.getdata()), 100)
        self.assertEqual(self.client.action('confirm', duration_ms=100, command_id='one')['screenshot_sha256'], result['screenshot_sha256'])
        self.assertEqual(len(self.observer()['events']), 2)
        self.assertEqual(Path(self.observer()['data_path']), self.root / 'savedata')
        native = read_json(self.directory / 'native-guard.json')
        self.assertEqual(native['pid'], self.meta['pid'])
        self.assertEqual(native['denied'], 0)
        for name, api, access in (('nonfatal-rejections.json', 'NtOpenSection(write)', 6),
                                  ('nonfatal-creation-rejections.json', 'NtCreateSection(named)', 0xf0007)):
            record = read_json(self.directory / name)
            self.assertGreater(record['count'], 0)
            self.assertEqual(record['api'], api)
            self.assertEqual(record['access'], access)
            self.assertEqual(record['status'], '0xc0000022')
            self.assertFalse(record['fatal'])
        cng = read_json(self.directory / 'nonfatal-cng-rejections.json')
        self.assertGreater(cng['count'], 0)
        self.assertEqual(cng['path'], '\\Device\\CNG')
        self.assertEqual(cng['control'], 0x390008)
        self.assertEqual(cng['status'], '0xc0000022')
        self.assertFalse(cng['fatal'])
        self.assertFalse((self.directory / 'guard-denial.json').exists())
        self.stop()
        self.assertTrue(read_json(self.directory / 'detached.json')['restored'])

    def test_default_guard_keeps_shell_request_fatal_before_input(self):
        from gameqakit.sessions import read_json
        from gameqakit.win32 import _pid_path
        script = self.host_script.read_text(encoding='utf-8')
        self.host_script.write_text(script.replace(repr(str(self.guard)), repr(str(self.strict_guard))), encoding='utf-8')
        state = self.start(allow_error=True)
        self.assertFalse(state['connected'])
        self.until(lambda: (self.root / 'host-result.json').exists(), 3)
        denial = read_json(self.directory / 'guard-denial.json')
        self.assertEqual(denial['api'], 'NtOpenSection(write)')
        self.assertTrue(denial['path'].endswith('\\BaseNamedObjects\\windows_shell_global_counters'))
        self.assertEqual(denial['control'], 6)
        self.assertFalse((self.directory / 'target-input.tjs').exists())
        self.assertFalse((self.directory / 'target-observer.tjs').exists())
        self.assertFalse(read_json(self.root / 'host-result.json')['target_alive'])
        self.until(lambda: not _pid_path(self.meta['pid']), 3)
        self.stop()

    def test_invalid_loading_targets_fail_without_input(self):
        from gameqakit.sessions import read_json
        self.set_testing_mode(load='multiple-windows')
        state = self.start(allow_error=True)
        self.until(lambda: self.process.poll() is not None, 3)
        self.assertFalse(self.observer().get('events'))
        state = read_json(self.directory / 'status.json')
        self.assertIn('window', state['error'].lower())
        self.assertEqual(read_json(self.directory / 'termination.json')['reason'], 'invalid_kag_window')
        self.assertFalse(read_json(self.directory / 'termination.json')['target_alive'])
