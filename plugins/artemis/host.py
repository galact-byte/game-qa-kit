"""Supervise the registered authored Artemis scene without foreground input."""
import time

from gameqakit.desktop import DesktopProcess
from gameqakit.duration_host import Supervisor
from gameqakit.duration_session import ManagedCapture
from .channel import Channel, MOD
from .prepare import ARTIFACT_DIGESTS, RUNTIME


class ArtemisSupervisor(ManagedCapture, Supervisor):
    engine = 'artemis'
    actions = ('move', 'wait')
    button = 'right'
    runtime_sha256 = ARTIFACT_DIGESTS[RUNTIME]

    def __init__(self, target, directory, session, channel):
        super().__init__(target, directory, session)
        self.channel = channel
        self.target_state = {}
        self.ids = {}
        self.pause_seq = 0
        self.pause_generation = ''

    def state(self):
        state = super().state()
        target = self.target_state
        acknowledged = not self.pause_seq or (target.get('ack_seq') == self.pause_seq and target.get('ack_kind') == 4)
        state.update(paused=self.paused and target.get('paused', False) and acknowledged,
                     held=['right'] if target.get('held') else [],
                     target_paused=target.get('paused', False),
                     target_sample_seq=target.get('sample_seq', 0),
                     target_callbacks=target.get('callbacks', 0),
                     target_clock_ms=target.get('clock_ms', 0),
                     target_ack_seq=target.get('ack_seq', 0),
                     target_ack_kind=target.get('ack_kind', 0))
        return state

    def send(self, ident, kind):
        duration = self.active['request']['payload']['duration_ms'] if kind == 'down' else 0
        seq = self.channel.send(kind, duration)
        self.ids[ident] = seq
        return seq

    def accept(self, request):
        super().accept(request)
        if self.active and request['payload']['action'] == 'resume':
            self.pause_seq = 0
            self.active['resume_seq'] = self.send(request['id'], 'resume')

    def observe(self):
        state = self.channel.observe()
        if not state:
            return
        self.target_state = state
        if state['sample_seq'] != self.ui_seq:
            self.ui_seq = state['sample_seq']
            self.changed = time.monotonic()
        self.channel.heartbeat(1500 if self.paused or self.lease_valid() else 0)
        if not self.observer:
            windows = [w for w in self.target.windows()
                       if not w['minimized'] and w['client'][0] >= 320 and w['client'][1] >= 240]
            if windows:
                self.observer = dict(hwnd=windows[0]['hwnd'], ready=True)
        active = self.active
        if active and active['keyed']:
            if state['ack_seq'] == self.ids.get(active['down_id']) and state['ack_kind'] == 2 and state['held']:
                if not active['press']:
                    active['press'] = True
                    active['press_sample'] = state['sample_seq']
                    remaining = (self.channel.hold_until - state['clock_ms']) % MOD
                    active['until'] = time.monotonic() + (remaining / 1000 if remaining <= 1000 else 0)
            if (not active['release'] and state['ack_seq'] == self.ids.get(active['up_id'])
                    and state['ack_kind'] == 3 and not state['held']):
                active['release'] = True
                active['release_sample'] = state['sample_seq']
                active['after_ui'] = state['sample_seq']
        if state['reason'] not in ('none', 'action_expired'):
            self.fault('target_' + state['reason'])

    def request_pause(self):
        if not self.pause_seq or self.pause_generation != self.pause_id:
            self.pause_seq = self.send('pause', 'pause')
            self.pause_generation = self.pause_id

    def interrupt(self, error):
        super().interrupt(error)
        if not self.active or not self.active['keyed']:
            self.request_pause()

    def progress(self):
        if self.active and self.active['request']['payload']['action'] == 'resume':
            if self.target_state.get('ack_seq') != self.active.get('resume_seq') or self.target_state.get('paused'):
                return
        super().progress()

    def deadlines(self):
        active = self.active
        if active and active['keyed'] and not active['press'] and time.monotonic() - active['started'] > 1:
            self.fault('press_not_confirmed')
        return super().deadlines()

    def finish(self, screenshot=None):
        active = self.active
        evidence = dict(target_press_sample=active.get('press_sample'), target_release_sample=active.get('release_sample'),
                        target_ack_seq=self.target_state.get('ack_seq', 0))
        super().finish({**evidence, **(screenshot or {})})
        if self.paused:
            self.request_pause()


def run(executable, directory, session):
    runtime = executable.parent
    for name in ('savedata', 'tmp'):
        (runtime / name).mkdir(exist_ok=True)
    with DesktopProcess([str(executable)], cwd=runtime,
                        env={'TMP': str(runtime / 'tmp'), 'TEMP': str(runtime / 'tmp')}, detached_stdio=True) as target:
        (runtime / 'session.txt').write_text(f'{session} {target.pid}\n', encoding='ascii', newline='\n')
        channel = Channel(runtime, session, target.pid)
        return ArtemisSupervisor(target, directory, session, channel).run()
