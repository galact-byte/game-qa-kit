"""Exercise Bakin supervision without commercial game assets."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from gameqakit.duration_host import Supervisor, publish
from PIL import Image
import hashlib


class BakinHostTests(unittest.TestCase):
    def setUp(self):
        from plugins.bakin.host import BakinSupervisor
        self.temp=tempfile.TemporaryDirectory(prefix='gqk-bakin-host-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.target=SimpleNamespace(pid=42,argv=['player.exe'])
        window_patch=patch('plugins.bakin.host.game_window',return_value={'hwnd':42,'client':[1280,720],'minimized':False})
        self.window_patch_original=window_patch.get_original()[0]
        self.window=window_patch.start()
        self.addCleanup(window_patch.stop)
        self.channel=Mock()
        self.channel.status={}
        self.channel.clock.return_value=1000
        self.channel.sequence=0
        self.channel.action_timing={'start_deadline_ms':2000}
        self.channel.action_pressed.return_value=True
        self.channel.acknowledged.return_value=True
        self.host=BakinSupervisor(self.target,self.root,'fixture',self.channel)

    def arm_move(self):
        self.host.observation_seq = 1
        self.host.paused = False
        self.host.lease_valid = Mock(return_value=True)
        self.host.accept(dict(protocol=2, session_id='fixture', id='move_evidence', pause_id='',
            after_observation_seq=1, created_at=time.time(),
            payload=dict(action='move', direction='right', duration_ms=1000)))
        self.host.active['press'] = True
        self.host.target_state = dict(held=8, scene='MAP')

    def test_move_collects_two_held_frames_without_finishing_or_extending_hold(self):
        self.arm_move()
        expiry = self.host.active['until']
        for index in (1, 2):
            self.host.progress()
            self.assertEqual('held', self.host.capture['purpose'])
            relative = 'screenshots/held_'+str(index)+'.png'
            source = self.root/relative
            source.parent.mkdir(exist_ok=True)
            Image.new('RGB', (16,16), 'blue').save(source)
            self.channel.root = self.root
            snapshot = dict(action_id=self.host.active['down_id'], held=8, sample_seq=index+5, at_ms=1000+index)
            receipt = dict(screenshot=relative, sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                input_snapshot=snapshot, draw_seq=index+8, captured_at_ms=1000+index)
            self.channel.capture_result.return_value = (None, receipt)
            self.host.poll_capture()
            self.assertIsNotNone(self.host.active)
            self.assertEqual(expiry, self.host.active['until'])
        evidence = self.host.active['held_screenshots']
        self.assertEqual(2, len(evidence))
        self.assertNotEqual(evidence[0]['screenshot'], evidence[1]['screenshot'])
        self.host.progress()
        self.assertIsNone(self.host.capture)
        self.host.active['release'] = True
        self.host.active['after_ui'] = 0
        self.host.ui_seq = 2
        self.host.progress()
        self.assertEqual('action', self.host.capture['purpose'])

    def test_frame_read_after_release_cannot_be_counted_as_held(self):
        self.arm_move()
        self.host.progress()
        self.assertIsNotNone(self.host.capture)
        source = self.root/'late.png'
        Image.new('RGB', (16,16), 'blue').save(source)
        self.channel.root = self.root
        self.channel.capture_result.return_value = (None, dict(screenshot='late.png',
            sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            input_snapshot=dict(action_id=self.host.active['down_id'], held=0, sample_seq=8, at_ms=1100),
            draw_seq=10, captured_at_ms=1100))
        self.host.poll_capture()
        self.assertEqual([], self.host.active.get('held_screenshots', []))
        self.assertIsNotNone(self.host.active)

    def test_short_press_waits_for_consumption_before_starting_hold_timer(self):
        self.host.observation_seq = 1
        self.host.paused = False
        self.host.lease_valid = Mock(return_value=True)
        with patch('plugins.bakin.host.time.monotonic', return_value=10):
            self.host.accept(dict(protocol=2, session_id='fixture', id='consume_timer', pause_id='',
                after_observation_seq=1, created_at=time.time(),
                payload=dict(action='confirm', duration_ms=100)))
        self.channel.clock.return_value = 1125
        self.channel.observe.return_value = dict(sample_seq=2, scene='TITLE', ready=True,
            paused=False, held=16, error=None,
            press=dict(action_id=self.host.active['down_id'], sample_seq=2, at_ms=1125,
                       hold_until_ms=1225, value=16), release=None)
        self.channel.action_released.return_value = False
        with patch('plugins.bakin.host.time.monotonic', return_value=10.125):
            self.host.observe()
        self.assertAlmostEqual(10.225, self.host.active['until'])
        self.channel.command.reset_mock()
        with patch('gameqakit.duration_host.time.monotonic', return_value=10.15):
            self.host.pump()
        self.channel.command.assert_not_called()

    def test_only_bakin_has_approved_sixty_second_startup(self):
        self.assertEqual(60,self.host.startup_seconds)
        normal=Supervisor(self.target,self.root,'fixture')
        normal.started=time.monotonic()-16
        self.assertEqual('startup_timeout',normal.deadlines())
        self.host.started=time.monotonic()-16
        self.assertIsNone(self.host.deadlines())

    def test_scene_none_cannot_trigger_initial_capture(self):
        self.channel.observe.return_value=dict(sample_seq=1,scene='NONE',paused=True,held=0,error=None)
        self.host.observe()
        self.assertFalse(self.host.observer.get('ready'))
        self.channel.observe.return_value=dict(sample_seq=2,scene='TITLE',ready=True,paused=True,held=0,error=None)
        self.host.observe()
        self.assertTrue(self.host.observer['ready'])

    def test_logo_and_title_fade_are_not_a_controllable_startup(self):
        for scene,ready in [('LOGO',False),('TITLE',False),('TITLE',True)]:
            self.channel.observe.return_value=dict(sample_seq=2,scene=scene,ready=ready,paused=True,held=0,error=None)
            self.host.observe()
            self.assertEqual(ready and scene=='TITLE',self.host.observer['ready'])

    def test_missing_or_minimized_main_window_cannot_be_ready(self):
        self.window.return_value=None
        self.channel.observe.return_value=dict(sample_seq=2,scene='TITLE',paused=True,held=0,error=None)
        self.host.observe()
        self.assertFalse(self.host.observer['ready'])

    def test_main_window_identity_ignores_occlusion_but_rejects_minimized_hidden_and_other_windows(self):
        original=self.window_patch_original
        rows=[dict(hwnd=42,client=[1280,720],minimized=False,occluded_by_foreground=True)]
        self.target.windows=Mock(return_value=rows)
        def name(hwnd,buffer,size):
            buffer.value='kmyAppWindow'
            return len(buffer.value)
        with patch('gameqakit.win32.user32.GetClassNameW',side_effect=name), patch('gameqakit.win32.user32.IsWindowVisible',return_value=True):
            self.assertEqual(rows[0],original(self.target))
            rows[0]['minimized']=True
            self.assertIsNone(original(self.target))
            rows[0]['minimized']=False
            with patch('gameqakit.win32.user32.IsWindowVisible',return_value=False):
                self.assertIsNone(original(self.target))
            with patch('gameqakit.win32.user32.GetClassNameW',side_effect=lambda hwnd,buffer,size:setattr(buffer,'value','#32770')):
                self.assertIsNone(original(self.target))

    def test_active_input_requires_consumed_release_evidence(self):
        self.host.paused=False
        self.host.active=dict(keyed=True,down_id='down',key=16,press=False,release=False,release_started=None)
        self.channel.observe.return_value=dict(sample_seq=2,scene='TITLE',paused=False,held=0,error=None,
            press=dict(action_id='down',sample_seq=1,at_ms=1000,hold_until_ms=1100),release=None)
        self.channel.action_released.return_value=False
        self.host.observe()
        self.assertTrue(self.host.active['press'])
        self.assertFalse(self.host.active['release'])
        self.channel.action_released.return_value=True
        self.channel.observe.return_value['release']=dict(action_id='down',sample_seq=2)
        self.host.observe()
        self.assertTrue(self.host.active['release'])

    def test_start_timeout_keeps_the_existing_action_and_activity_limits(self):
        self.arm_loading()
        self.channel.clock.return_value=2000
        self.assertEqual('press_timeout',self.host.deadlines())
        self.assertFalse(self.host.active['press'])
        self.assertFalse(self.host.active['release'])

    def test_consumed_deadline_is_not_reanchored_by_repeated_status(self):
        state=self.arm_loading()
        self.channel.action_released.return_value=False
        state['press']=dict(action_id=self.host.active['down_id'],sample_seq=2,at_ms=1050,hold_until_ms=1150)
        self.channel.clock.return_value=1050
        with patch('plugins.bakin.host.time.monotonic',return_value=10):self.host.observe()
        expiry=self.host.active['until']
        self.channel.clock.return_value=1060
        with patch('plugins.bakin.host.time.monotonic',return_value=11):self.host.observe()
        self.assertEqual(expiry,self.host.active['until'])

    def test_pause_discards_deferred_down_before_acknowledgement(self):
        self.channel.sequence=1
        self.channel.status={'command_seq':0}
        self.host.active=dict(keyed=True,down_id='pending',up_id='up',key=16,press=False,
            release=False,release_started=None,error='')
        self.host.send('pending','down')
        self.host.interrupt('manual')
        self.channel.status={'command_seq':1}
        self.host.flush_commands()
        self.channel.command.assert_called_once_with('pause')
        self.assertIsNone(self.host.deferred)

    def test_pause_waits_for_previous_command_acknowledgement(self):
        self.channel.sequence=2
        self.channel.status={'command_seq':1}
        self.host.interrupt('manual')
        self.channel.command.assert_not_called()
        self.channel.status={'command_seq':2}
        self.channel.observe.return_value=dict(sample_seq=2,scene='TITLE',paused=False,held=0,error=None)
        self.host.observe()
        self.channel.command.assert_called_once_with('pause')

    def test_host_does_not_renew_active_permission_after_client_lease_expiry(self):
        self.host.paused=False
        self.channel.observe.return_value=dict(sample_seq=2,scene='TITLE',paused=False,held=0,error=None)
        self.host.observe()
        self.channel.lease.assert_not_called()

    def test_initial_none_sample_does_not_end_the_approved_startup_phase(self):
        self.host.started=time.monotonic()-42
        self.host.ui_seq=1
        self.host.changed=time.monotonic()-1.1
        self.assertIsNone(self.host.deadlines())
        self.assertEqual([],self.host.state()['capabilities'])
        self.assertTrue(self.host.paused)
        self.host.started=time.monotonic()-60.1
        self.assertEqual('startup_timeout',self.host.deadlines())

    def test_ready_sampling_and_initial_capture_keep_their_original_deadlines(self):
        self.host.ui_seq=1
        self.host.changed=time.monotonic()-1.1
        self.host.observation_seq=1
        self.assertEqual('activity_timeout',self.host.deadlines())
        self.host.observation_seq=0
        self.host.ui_seq=0
        self.host.capture={'started':time.monotonic()-3.1}
        self.assertEqual('capture_timeout',self.host.deadlines())

    def arm_loading(self):
        self.host.observation_seq = 1
        self.host.paused = False
        self.host.pause_id = ''
        self.host.lease_valid = Mock(return_value=True)
        state = dict(sample_seq=2, scene='TITLE', ready=True, paused=False, held=0, error=None,
                     command_seq=0, loading_protocol=1, title_state='TITLE', map_loading=False, map_fade='None')
        self.channel.status = state
        self.channel.observe.return_value = state
        self.host.observe()
        request = dict(protocol=2, session_id='fixture', id='load_one', pause_id='', after_observation_seq=1,
                       created_at=time.time(), payload=dict(action='confirm', duration_ms=100,
                       loading_mode='bakin_title_new_game_v1'))
        self.host.accept(request)
        return state

    def consume_loading(self):
        state = self.arm_loading()
        self.channel.action_released.return_value = False
        state.update(press=dict(action_id=self.host.active['down_id'], sample_seq=2, at_ms=1000, hold_until_ms=1100),
                     title_state='FADEOUT_FOR_NEWGAME')
        self.host.observe()
        state.update(paused=True)
        self.host.observe()
        return state

    def test_loading_does_not_change_deadlines_before_press_and_pause_ack(self):
        state = self.arm_loading()
        self.host.changed = time.monotonic() - 1.1
        self.assertEqual('activity_timeout', self.host.deadlines())
        self.channel.action_released.return_value = False
        state.update(press=dict(action_id=self.host.active['down_id'], sample_seq=2, at_ms=1000, hold_until_ms=1100), title_state='FADEOUT_FOR_NEWGAME')
        self.host.observe()
        self.host.active['release_started'] = time.monotonic() - 1.1
        self.assertEqual('release_timeout', self.host.deadlines())

    def test_only_confirmed_pause_and_newgame_transition_enable_loading_observation(self):
        self.consume_loading()
        self.host.changed = time.monotonic() - 5
        self.host.active['release_started'] = time.monotonic() - 5
        self.assertIsNone(self.host.deadlines())
        self.assertFalse(self.host.active['release'])
        self.assertTrue(self.host.state()['paused'])
        self.assertEqual([], self.host.state()['capabilities'])
        self.assertEqual('loading', self.host.state()['loading']['phase'])

    def test_confirmed_loading_publishes_progress_before_any_capture_or_release(self):
        from gameqakit.duration_host import read
        self.consume_loading()
        self.host.progress()
        value = read(self.root/'progress/load_one.json')
        self.assertIsNotNone(value)
        self.assertIs(True, value['observation_pending'])
        self.assertEqual('loading', value['phase'])
        self.assertEqual(1, value['observation_seq'])
        self.assertIs(True, value['press_consumed'])
        self.assertIs(False, value['release_consumed'])
        self.assertEqual(self.host.pause_id, value['loading_pause_id'])
        self.assertNotIn('screenshot', value)
        self.assertIsNone(self.host.capture)
        self.assertFalse((self.root/'progress/load_one').exists())
        deadline = value['loading_deadline_monotonic_ms']
        self.host.active['release'] = True
        self.host.progress()
        newer = read(self.root/'progress/load_one.json')
        self.assertIs(True, newer['release_consumed'])
        self.assertEqual(deadline, newer['loading_deadline_monotonic_ms'])

    def test_unconfirmed_loading_never_publishes_imageless_progress(self):
        self.arm_loading()
        self.host.progress()
        self.assertFalse((self.root/'progress/load_one.json').exists())

    def test_loading_waits_for_render_ready_map_before_requesting_a_new_frame(self):
        from plugins.bakin.target import TargetChannel
        from gameqakit.duration_host import read
        state = self.consume_loading()
        target_root = self.root / 'target'
        target_root.mkdir()
        self.host.channel = TargetChannel(target_root, 'fixture', 42, 'created', clock=lambda: 16579875)
        self.addCleanup(self.host.channel.close)
        self.host.loading['last_draw'] = 512
        state.update(draw_seq=518, ready=False)
        deadline = self.host.loading['deadline']
        with patch('plugins.bakin.loading.time.monotonic', return_value=100):
            self.host.last_capture = 99
            for updates in ({}, dict(scene='MAP', map_loading=True),
                            dict(map_loading=False, map_fade='FadeIn'),
                            dict(map_fade='None', ready=False)):
                state.update(updates)
                self.host.progress()
                self.assertIsNone(self.host.capture)
                self.assertFalse((target_root / 'target-capture.json').exists())
                self.assertEqual(deadline, self.host.loading['deadline'])
            state.update(ready=True, draw_seq=519)
            self.host.progress()
        self.assertEqual('loading', self.host.capture['purpose'])
        self.assertEqual(16582875, read(target_root / 'target-capture.json')['expires_at_ms'])
        self.assertFalse(self.host.active['release'])
        self.assertTrue(self.host.paused)
        self.assertEqual([], self.host.state()['capabilities'])

    def test_loading_deadline_is_fixed_and_capture_timeout_is_unchanged(self):
        self.consume_loading()
        self.host.capture = dict(started=time.monotonic() - 3.1)
        self.assertEqual('capture_timeout', self.host.deadlines())
        self.host.capture = None
        self.host.loading['deadline'] = time.monotonic() - .01
        self.assertEqual('loading_timeout', self.host.deadlines())

    def test_loading_capture_failure_retains_original_error_in_termination(self):
        from gameqakit.duration_host import read
        from plugins.bakin.target import TargetChannel
        self.consume_loading()
        target_root = self.root / 'target'
        target_root.mkdir()
        target_channel = TargetChannel(target_root, 'fixture', 42, 'created', clock=lambda: 55470234)
        self.addCleanup(target_channel.close)
        target_channel.pending_capture = dict(expires_at_ms=55470234)
        self.channel.capture_result = target_channel.capture_result
        # The channel's integer deadline can expire between the supervisor check and polling.
        self.host.capture = dict(started=time.monotonic() - 2.9, purpose='loading', ident='loading-239')
        self.target.desktop_name = 'fixture'
        self.target.alive = lambda: not getattr(self.target, 'closed', False)
        self.target.close = lambda: setattr(self.target, 'closed', True)
        self.assertEqual(4, self.host.run(launch_target=False))
        termination = read(self.root / 'termination.json')
        self.assertEqual('loading_cancelled', termination['reason'])
        self.assertIn('capture exceeded 3 seconds', termination.get('error', ''))
        self.assertIs(False, termination['release_consumed'])
        self.assertIs(False, termination['target_alive'])
        self.assertIs(True, self.host.paused)

    def test_loading_input_loss_or_wrong_transition_fails_closed(self):
        state = self.consume_loading()
        state.update(held=16)
        with self.assertRaises(RuntimeError):
            self.host.observe()

    def test_loading_cannot_resume_or_accept_second_action_before_review(self):
        self.consume_loading()
        request = dict(protocol=2, session_id='fixture', id='resume_early', pause_id=self.host.pause_id,
                       after_observation_seq=1, created_at=time.time(), payload=dict(action='resume'))
        with self.assertRaises(ValueError):
            self.host.accept(request)

    def test_duplicate_loading_request_never_sends_a_second_confirmation(self):
        self.consume_loading()
        request = self.host.active['request']
        before = self.channel.command.call_count
        self.host.accept(request)
        self.assertEqual(before, self.channel.command.call_count)

    def reviewed_map(self, **updates):
        state = self.consume_loading()
        state.update(scene='MAP', ready=True, map_loading=False, map_fade='None', title_state='WAITING_NEWGAME')
        self.host.active['release'] = True
        self.host.observation_seq = 2
        self.host.observation = 'screenshots/loading-2.png'
        image = self.root/self.host.observation
        image.parent.mkdir(exist_ok=True)
        Image.new('RGB', (32, 24), 'red').save(image)
        self.host.last_capture = time.monotonic()
        receipt = dict(sha256=hashlib.sha256(image.read_bytes()).hexdigest(), draw_seq=3,
                       blank_frame=False, loading_protocol=1, scene='MAP', map_loading=False, map_fade='None')
        receipt.update(updates)
        self.host.loading_capture(receipt)
        review = dict(self.host.identity, id='load_one', loading_pause_id=self.host.pause_id,
                      accepted=True, observation_seq=2, screenshot_sha256=receipt['sha256'])
        return review

    def test_reviewed_completion_keeps_pause_and_uses_command_screenshot(self):
        review = self.reviewed_map()
        request = self.host.active['request']
        publish(self.root/'loading-review.json', review)
        self.host.progress()
        from gameqakit.duration_host import read
        result = read(self.root/'results/load_one.json')
        self.assertEqual('screenshots/load_one.png', result['screenshot'])
        self.assertTrue((self.root/result['screenshot']).is_file())
        self.assertTrue(self.host.paused)
        self.assertIsNone(self.host.active)
        self.host.accept(request)
        self.assertIsNone(self.host.active)
        self.assertEqual('completed', self.host.loading['phase'])

    def test_loading_review_rejects_blank_or_still_loading_and_wrong_identity(self):
        for updates in (dict(blank_frame=True), dict(map_loading=True), dict(map_fade='FadeIn')):
            with self.subTest(updates=updates):
                review = self.reviewed_map(**updates)
                publish(self.root/'loading-review.json', review)
                with self.assertRaises(ValueError):
                    self.host.progress_loading()
                self.assertFalse((self.root/'results/load_one.json').exists())
                self.host.active = None
                self.host.loading = None
        review = self.reviewed_map()
        review['loading_pause_id'] = 'old_pause'
        publish(self.root/'loading-review.json', review)
        with self.assertRaises(ValueError):
            self.host.progress_loading()

    def test_manual_pause_and_stop_cancel_loading_without_relaxing_guards(self):
        self.consume_loading()
        self.host.interrupt('manual pause')
        self.assertEqual('loading_cancelled', self.host.deadlines())
        self.assertTrue(self.host.paused)
        self.assertFalse(self.host.loading_confirmed())

    def test_review_from_an_older_observation_cannot_cross_three_seconds(self):
        review = self.reviewed_map()
        self.host.loading['shots'][0]['captured_monotonic_ms'] -= 3001
        publish(self.root/'loading-review.json', review)
        with self.assertRaises(ValueError):
            self.host.progress_loading()

    def test_action_result_has_five_second_deadline(self):
        self.host.observation_seq=1
        self.host.active=dict(started=time.monotonic()-5.1,keyed=False,release_started=None)
        self.assertEqual('action_timeout',self.host.deadlines())
