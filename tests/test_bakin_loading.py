"""Bakin loading is an explicit, bounded extension of the duration protocol."""
import unittest
from unittest import mock
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import threading
import time

from PIL import Image
from gameqakit.control import BridgeClient
from gameqakit.sessions import write_json

from gameqakit.control_time import validate_payload, validate_result, validate_state

MODE = 'bakin_title_new_game_v1'


def state(**updates):
    value = dict(engine='bakin', timing_mode='duration_ms', pause_scope='input', simulation_pause=False,
                 observation_seq=2, max_duration_ms=1000, buttons=['ok'], held=[], capabilities=['confirm'],
                 loading_modes=[MODE], target_scene='TITLE', target_title_state='TITLE')
    value.update(updates)
    return value


class LoadingProtocolTests(unittest.TestCase):
    def test_loading_intent_requires_registered_backend_and_explicit_capability(self):
        payload = dict(action='confirm', duration_ms=100, loading_mode=MODE)
        validate_payload(payload, state())
        for update in (dict(engine='wolf'), dict(loading_modes=[]), dict(target_scene='MAP'),
                       dict(target_title_state='FADEIN')):
            with self.subTest(update=update), self.assertRaises(ValueError):
                validate_payload(payload, state(**update))

    def test_loading_cannot_be_added_to_other_actions_or_unknown_modes(self):
        for action, mode in [('wait', MODE), ('resume', MODE), ('confirm', 'anything')]:
            with self.subTest(action=action, mode=mode), self.assertRaises(ValueError):
                validate_payload(dict(action=action, duration_ms=1, loading_mode=mode), state())

    def test_other_backends_cannot_advertise_loading(self):
        validate_state(state())
        with self.assertRaises(RuntimeError):
            validate_state(state(engine='wolf'))

    def result(self):
        return dict(consumed=True, press_consumed=True, release_consumed=True, observation_seq=4,
                    pause_id='', timing_mode='duration_ms', pause_scope='input', simulation_pause=False,
                    held=[], started_monotonic_ms=10, completed_monotonic_ms=80010,
                    screenshot_sha256='a'*64, result_kind=MODE, loading_pause_id='loading_pause',
                    paused=True, map_loading=False, map_fade='None', target_scene='MAP',
                    visual_reviewed=True, reviewed_observation_seq=4, loading_elapsed_ms=79900)

    def request(self):
        return dict(payload=dict(action='confirm', duration_ms=100, loading_mode=MODE),
                    after_observation_seq=2, pause_id='')

    def test_long_loading_result_has_distinct_contract(self):
        validate_result(self.result(), self.request())
        with self.assertRaises(RuntimeError):
            validate_result(self.result(), dict(self.request(), payload=dict(action='confirm', duration_ms=100)))

    def test_completion_requires_release_review_finished_fade_and_unchanged_limit(self):
        for update in (dict(release_consumed=False), dict(visual_reviewed=False), dict(map_loading=True),
                       dict(map_fade='FadeIn'), dict(paused=False), dict(loading_pause_id=''),
                       dict(loading_elapsed_ms=300001), dict(loading_elapsed_ms=True),
                       dict(reviewed_observation_seq=2)):
            with self.subTest(update=update), self.assertRaises(RuntimeError):
                validate_result(dict(self.result(), **update), self.request())
    def test_ordinary_result_never_receives_loading_duration_limit(self):
        result = self.result()
        result.pop('result_kind')
        with self.assertRaises(RuntimeError):
            validate_result(result, dict(self.request(), payload=dict(action='confirm', duration_ms=100)))


class LoadingClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='gqk-loading-client-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.meta = dict(protocol=2, session_id='fixture', pid=os.getpid(), exe_path=sys.executable, engine='bakin')
        self.state = dict(state(), **self.meta, paused=False, updated_at=time.time())
        write_json(self.root/'connection.json', self.meta)
        write_json(self.root/'status.json', self.state)
        self.client = BridgeClient(self.root, pid=os.getpid(), exe_path=sys.executable)
        self.stop = threading.Event()
        self.workers = []
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.stop.set()
        for worker in self.workers:
            worker.join(2)

    def progress(self, **updates):
        image = self.root/'screenshots/loading-3.png'
        image.parent.mkdir(exist_ok=True)
        Image.new('RGB', (32, 24), 'red').save(image)
        progress = dict(self.meta, id='load_one', result_kind=MODE, in_progress=True,
                        loading_pause_id='load_pause', phase='awaiting_visual_review',
                        paused=True, held=[], press_consumed=True, release_consumed=True,
                        observation_seq=3, screenshot='screenshots/loading-3.png',
                        screenshot_sha256=hashlib.sha256(image.read_bytes()).hexdigest(),
                        captured_monotonic_ms=int(time.monotonic()*1000),
                        loading_deadline_monotonic_ms=int((time.monotonic()+299)*1000),
                        blank_frame=False, scene='MAP', map_loading=False, map_fade='None', loading_protocol=1)
        progress.update(updates)
        write_json(self.root/'progress/load_one/3.json', progress)
        write_json(self.root/'pause.json', dict(session_id='fixture', id='load_pause'))
        write_json(self.root/'status.json', dict(self.state, paused=True, pause_id='load_pause',
                   observation_seq=3, loading=dict(command_id='load_one', pause_id='load_pause', phase='loading')))
        write_json(self.root/'progress/load_one.json', progress)
        return progress

    def stored_request(self):
        value = dict(protocol=2, session_id='fixture', id='load_one', pause_id='', after_observation_seq=2,
                     payload=dict(action='confirm', duration_ms=100, loading_mode=MODE))
        write_json(self.root/'requests/load_one.json', value)
        return value

    def test_progress_publication_already_has_matching_pause_and_status(self):
        self.stored_request()
        publish = write_json

        def verify(path, value):
            publish(path, value)
            if path == self.root / 'progress/load_one.json':
                self.assertTrue(self.client.result('load_one')['in_progress'])

        with mock.patch(__name__ + '.write_json', side_effect=verify):
            self.progress()
            self.loading_without_image()

    def test_loading_submit_returns_pending_without_timeout_pause_or_reconfirmation(self):
        def respond():
            while not self.stop.wait(.005):
                if (self.root/'request.json').exists():
                    self.progress()
                    return
        worker = threading.Thread(target=respond)
        self.workers.append(worker)
        worker.start()
        result = self.client.action('confirm', duration_ms=100, loading_mode=MODE,
                                    command_id='load_one', timeout=.5)
        self.assertEqual('pending', result['status'])
        self.assertIs(True, result['in_progress'])
        before = (self.root/'request.json').stat().st_mtime_ns
        self.client.result('load_one')
        self.assertEqual(before, (self.root/'request.json').stat().st_mtime_ns)
        self.assertEqual('load_pause', self.client._pause_id())

    def loading_without_image(self, **updates):
        value = dict(self.meta, id='load_one', result_kind=MODE, in_progress=True,
                     loading_pause_id='load_pause', phase='loading', paused=True, held=[],
                     press_consumed=True, release_consumed=False, observation_pending=True,
                     observation_seq=2, loading_deadline_monotonic_ms=int((time.monotonic()+299)*1000))
        value.update(updates)
        write_json(self.root/'pause.json', dict(session_id='fixture', id='load_pause'))
        write_json(self.root/'status.json', dict(self.state, paused=True, pause_id='load_pause',
                   loading=dict(command_id='load_one', pause_id='load_pause', phase='loading')))
        write_json(self.root/'progress/load_one.json', value)
        return value

    def test_loading_submit_returns_progress_while_rendering_is_unavailable(self):
        def respond():
            while not self.stop.wait(.005):
                if (self.root/'request.json').exists():
                    self.loading_without_image()
                    return
        worker = threading.Thread(target=respond)
        self.workers.append(worker)
        worker.start()
        result = self.client.action('confirm', duration_ms=100, loading_mode=MODE,
                                    command_id='load_one', timeout=.5)
        self.assertEqual('pending', result['status'])
        self.assertIs(True, result['observation_pending'])
        self.assertIs(False, result['release_consumed'])
        self.assertNotIn('screenshot', result)
        self.assertFalse((self.root/'screenshots').exists())
        before = (self.root/'request.json').read_bytes()
        self.assertEqual(result['loading_deadline_monotonic_ms'],
                         self.client.result('load_one')['loading_deadline_monotonic_ms'])
        self.assertEqual(before, (self.root/'request.json').read_bytes())
        self.assertEqual('load_pause', self.client._pause_id())

    def test_imageless_progress_cannot_substitute_for_visual_review_or_forge_evidence(self):
        self.stored_request()
        for update in (dict(observation_seq=3), dict(phase='awaiting_visual_review'),
                       dict(press_consumed=False), dict(held=['ok']), dict(paused=False),
                       dict(session_id='other'), dict(loading_pause_id='old'),
                       dict(screenshot='screenshots/old.png'), dict(screenshot_sha256='0'*64),
                       dict(captured_monotonic_ms=1), dict(loading_deadline_monotonic_ms=1)):
            with self.subTest(update=update), self.assertRaises((ValueError, RuntimeError)):
                self.loading_without_image(**update)
                self.client.result('load_one')
        self.loading_without_image()
        with self.assertRaises((ValueError, RuntimeError)):
            self.client.result('load_one', loading_review=dict(accepted=True, observation_seq=2,
                screenshot_sha256='0'*64, loading_pause_id='load_pause'))
        self.assertFalse((self.root/'loading-review.json').exists())

    def test_progress_is_validated_before_review_can_be_written(self):
        self.stored_request()
        progress = self.progress()
        review = dict(accepted=True, observation_seq=3, screenshot_sha256=progress['screenshot_sha256'],
                      loading_pause_id='load_pause')
        result = self.client.result('load_one', loading_review=review)
        self.assertEqual('pending', result['status'])
        self.assertTrue((self.root/'loading-review.json').is_file())
        self.assertFalse((self.root/'request.json').exists())

    def test_wrong_review_token_digest_and_sequence_never_write(self):
        self.stored_request()
        progress = self.progress()
        base = dict(accepted=True, observation_seq=3, screenshot_sha256=progress['screenshot_sha256'],
                    loading_pause_id='load_pause')
        for update in (dict(loading_pause_id='old'), dict(observation_seq=2),
                       dict(screenshot_sha256='0'*64), dict(accepted=False)):
            with self.subTest(update=update), self.assertRaises((ValueError, RuntimeError)):
                self.client.result('load_one', loading_review=dict(base, **update))
            self.assertFalse((self.root/'loading-review.json').exists())

    def test_progress_cannot_forge_identity_or_image_digest(self):
        self.stored_request()
        for update in (dict(session_id='other'), dict(screenshot_sha256='0'*64),
                       dict(held=['ok']), dict(paused=False), dict(screenshot='../outside.png')):
            with self.subTest(update=update), self.assertRaises((ValueError, RuntimeError)):
                self.progress(**update)
                self.client.result('load_one')

    def test_loading_completion_is_current_under_its_new_pause_token(self):
        self.stored_request()
        progress = self.progress()
        shot = self.root/'screenshots/load_one.png'
        shot.write_bytes((self.root/progress['screenshot']).read_bytes())
        result = dict(LoadingProtocolTests().result(), **self.meta, id='load_one', ok=True,
                      screenshot='screenshots/load_one.png', loading_pause_id='load_pause',
                      screenshot_sha256=hashlib.sha256(shot.read_bytes()).hexdigest())
        write_json(self.root/'results/load_one.json', result)
        self.assertFalse(self.client.result('load_one')['historical'])
