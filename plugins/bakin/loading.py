"""Bounded loading observation for the registered Bakin title new-game path."""
import time
import uuid
import shutil

from gameqakit.admission import no_links
from gameqakit.control_time import LOADING_MODE
from gameqakit.duration_host import publish, read


class LoadingLifecycle:
    def loading_confirmed(self):
        return bool(self.loading and self.loading.get('confirmed') and self.loading['phase'] != 'completed'
                    and not self.loading.get('cancelled'))

    def begin_loading(self, request):
        self.loading = dict(command_id=request['id'], phase='armed', confirmed=False, consumed_at=None,
                            deadline=None, pause_id=uuid.uuid4().hex, shots=[], last_draw=0)

    def update_loading(self):
        loading, active, state = self.loading, self.active, self.target_state
        if not loading or not active or loading.get('cancelled') or loading['phase'] == 'completed':
            return
        if active['press'] and loading['consumed_at'] is None:
            loading['consumed_at'] = time.monotonic()
            loading['deadline'] = loading['consumed_at'] + 300
            loading['phase'] = 'withdrawing_input'
            self.paused = True
            self.pause_id = loading['pause_id']
            publish(self.directory / 'pause.json', dict(session_id=self.identity['session_id'], id=self.pause_id))
            # A loading pause withdraws the action once; it does not manufacture a release query.
            self.release()
            self.request_pause()
        if loading['consumed_at'] is None:
            return
        transition = state.get('title_state') in ('FADEOUT_FOR_NEWGAME', 'WAITING_NEWGAME')
        if loading['confirmed']:
            if state.get('paused') is not True or state.get('held') != 0 or not transition:
                raise RuntimeError('Bakin loading lost its new-game transition or neutral pause')
        elif (transition and state.get('paused') is True and state.get('held') == 0
              and not self.pause_pending and self.deferred is None and self.channel.acknowledged('pause')):
            loading['confirmed'] = True
            loading['phase'] = 'loading'

    @staticmethod
    def map_complete(value):
        return (value.get('loading_protocol') == 1 and value.get('scene') == 'MAP'
                and value.get('map_loading') is False and value.get('map_fade') == 'None')

    def loading_progress(self, **observation):
        loading = self.loading
        return dict(self.identity, id=loading['command_id'], result_kind=LOADING_MODE,
                    in_progress=True, loading_pause_id=loading['pause_id'], phase=loading['phase'],
                    paused=self.state()['paused'], held=self.state()['held'],
                    press_consumed=self.active['press'], release_consumed=self.active['release'],
                    loading_deadline_monotonic_ms=int(loading['deadline'] * 1000), **observation)

    def loading_capture(self, receipt):
        if not self.loading_confirmed():
            return
        loading = self.loading
        shot = dict(observation_seq=self.observation_seq, screenshot=self.observation,
                    screenshot_sha256=receipt['sha256'], draw_seq=receipt['draw_seq'],
                    captured_monotonic_ms=int(self.last_capture * 1000),
                    blank_frame=receipt.get('blank_frame', False), scene=receipt.get('scene'),
                    map_loading=receipt.get('map_loading'), map_fade=receipt.get('map_fade'),
                    loading_protocol=receipt.get('loading_protocol'))
        loading['shots'].append(shot)
        loading['shots'] = loading['shots'][-20:]
        loading['last_draw'] = receipt['draw_seq']
        loading['phase'] = ('awaiting_visual_review' if self.active['release'] and self.map_complete(shot)
                            and not shot['blank_frame'] else 'loading')
        progress = self.loading_progress(**shot)
        path = self.directory / 'progress' / (loading['command_id'] + '.json')
        archive = self.directory / 'progress' / loading['command_id'] / (str(self.observation_seq) + '.json')
        archive.parent.mkdir(parents=True, exist_ok=True)
        publish(archive, progress)
        self.publish_status()
        publish(path, progress)

    def progress_loading(self):
        if not self.loading_confirmed():
            return False
        loading = self.loading
        if not loading['shots'] and loading.get('published_release') is not self.active['release']:
            # A loading receipt must not depend on a frame the engine cannot draw yet.
            progress = self.loading_progress(observation_pending=True,
                        observation_seq=self.active['request']['after_observation_seq'])
            self.publish_status()
            publish(self.directory / 'progress' / (loading['command_id'] + '.json'), progress)
            loading['published_release'] = self.active['release']
        review_path = self.directory / 'loading-review.json'
        no_links(review_path)
        review = read(review_path)
        if review:
            if (any(type(review.get(k)) is not type(v) or review[k] != v for k, v in self.identity.items())
                    or review.get('id') != loading['command_id'] or review.get('loading_pause_id') != loading['pause_id']
                    or review.get('accepted') is not True or type(review.get('observation_seq')) is not int):
                raise ValueError('Bakin loading review identity or decision is invalid')
            shot = next((item for item in loading['shots'] if item['observation_seq'] == review['observation_seq']), None)
            if (not shot or not self.active['release'] or not self.map_complete(shot) or shot['blank_frame']
                    or not self.map_complete(self.target_state) or self.target_state.get('ready') is not True
                    or review.get('screenshot_sha256') != shot['screenshot_sha256']
                    or not 0 <= time.monotonic() * 1000 - shot['captured_monotonic_ms'] < 3000):
                raise ValueError('Bakin loading review has no fresh completed-map screenshot and release evidence')
            destination = self.directory / 'screenshots' / (loading['command_id'] + '.png')
            shutil.copyfile(self.directory / shot['screenshot'], destination)
            loading['phase'] = 'completed'
            loading['reviewed'] = shot
            self.finish(dict(result_kind=LOADING_MODE, loading_pause_id=loading['pause_id'], paused=True,
                             visual_reviewed=True, reviewed_observation_seq=shot['observation_seq'],
                             loading_elapsed_ms=int((time.monotonic() - loading['consumed_at']) * 1000),
                             map_loading=False, map_fade='None', screenshot=destination.relative_to(self.directory).as_posix(),
                             screenshot_sha256=shot['screenshot_sha256']))
            return True
        # Loading may stop drawing; an advanced counter does not promise another frame.
        # Start the bounded capture only when the engine reports a render-ready map.
        if (not self.capture and self.map_complete(self.target_state) and self.target_state.get('ready') is True
                and self.target_state.get('draw_seq', 0) > loading['last_draw']
                and time.monotonic() - self.last_capture >= .2):
            self.start_capture('loading-' + str(self.observation_seq + 1), 'loading')
        return True

    def loading_deadline(self, now):
        if self.loading and self.loading.get('cancelled'):
            return 'loading_cancelled'
        if self.loading and self.loading['deadline'] is not None and self.loading['phase'] != 'completed':
            if now >= self.loading['deadline']:
                return 'loading_timeout'
        if self.loading_confirmed():
            if self.stopping is not None and now - self.stopping > 1:
                return 'shutdown_timeout'
            if self.capture and now - self.capture['started'] >= 3:
                return 'capture_timeout'
            return ''
        return None
