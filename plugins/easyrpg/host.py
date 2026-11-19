"""Independent host for the registered source-built EasyRPG runtime."""
import time

from gameqakit.desktop import DesktopProcess
from gameqakit.duration_host import Supervisor
from gameqakit.duration_session import ManagedCapture
from .channel import Channel


class EasyRpgSupervisor(ManagedCapture, Supervisor):
    engine = 'easyrpg'
    actions = ('move', 'wait')
    button = 'right'

    def __init__(self, target, directory, session, channel, runtime_sha256):
        super().__init__(target, directory, session)
        self.channel = channel
        self.runtime_sha256 = runtime_sha256
        self.ids = {}
        self.target_state = {}
        self.pause_seq = 0
        self.pause_generation = ''

    def state(self):
        state = super().state()
        target_paused = bool(self.target_state.get('paused', 0))
        pause_acknowledged = not self.pause_seq or (
            self.target_state.get('ack_seq') == self.pause_seq and self.target_state.get('ack_kind') == 4)
        state.update(paused=self.paused and target_paused and pause_acknowledged,
                     target_frame=self.target_state.get('frame', 0),
                     target_ack_seq=self.target_state.get('ack_seq', 0),
                     target_paused=target_paused)
        if self.target_state.get('held'):
            state['held'] = ['right']
        return state

    def send(self, ident, kind):
        number = {'resume': 1, 'down': 2, 'up': 3, 'pause': 4, 'close': 5}[kind]
        snapshot = self.channel.exchange(kind=number, lease_ms=1500)
        self.ids[ident] = snapshot['seq']
        return snapshot['seq']

    def accept(self, request):
        super().accept(request)
        if self.active and request['payload']['action'] == 'resume':
            self.pause_seq = 0
            self.active['resume_seq'] = self.send(request['id'], 'resume')

    def observe(self):
        state = self.channel.exchange(lease_ms=1500 if self.paused or self.lease_valid() else 0)
        if state['session'] != self.channel.token or state['pid'] not in (0, self.target.pid):
            raise ValueError('EasyRPG target identity mismatch')
        self.target_state = state
        if state['ready'] and state['frame'] != self.ui_seq:
            self.ui_seq = state['frame']
            self.changed = time.monotonic()
        if state['ready'] and not self.observer:
            windows = [window for window in self.target.windows() if window['client'][0] >= 320 and window['client'][1] >= 240]
            if windows:
                self.observer = dict(hwnd=windows[0]['hwnd'], ready=True)
        active = self.active
        if not active or not active['keyed']:
            return
        if state['ack_seq'] == self.ids.get(active['down_id']) and state['ack_kind'] == 2 and state['held']:
            if not active['press']:
                active['press'] = True
                active['press_frame'] = state['frame']
                active['until'] = time.monotonic() + active['request']['payload']['duration_ms'] / 1000
        if (not active['release'] and state['ack_seq'] == self.ids.get(active['up_id'])
                and state['ack_kind'] == 3 and not state['held']):
            active['release'] = True
            active['release_frame'] = state['frame']
            active['after_ui'] = state['frame']

    def request_pause(self):
        if not self.pause_seq or self.pause_generation != self.pause_id:
            self.pause_seq = self.send('pause', 'pause')
            self.pause_generation = self.pause_id

    def interrupt(self, error):
        super().interrupt(error)
        if not self.active or not self.active['keyed']:
            self.request_pause()

    def progress(self):
        active = self.active
        if active and active['request']['payload']['action'] == 'resume':
            if self.target_state.get('ack_seq') != active.get('resume_seq') or self.target_state.get('paused'):
                return
        super().progress()

    def deadlines(self):
        active = self.active
        if active and active['keyed'] and not active['press'] and time.monotonic() - active['started'] > 1:
            self.fault('press_not_confirmed')
        return super().deadlines()

    def finish(self, screenshot=None):
        active = self.active
        evidence = dict(target_press_frame=active.get('press_frame'), target_release_frame=active.get('release_frame'),
                        target_ack_seq=self.target_state.get('ack_seq', 0))
        super().finish({**evidence, **(screenshot or {})})
        if self.paused:
            self.request_pause()


def run(executable, runtime, directory, session, runtime_sha256, *, supervisor=EasyRpgSupervisor):
    root = runtime.parent
    for name in ('config', 'save', 'tmp'):
        (root / name).mkdir(exist_ok=True)
    with Channel() as channel:
        channel.exchange(lease_ms=1500)
        env = {**channel.env, 'TMP': str(root / 'tmp'), 'TEMP': str(root / 'tmp'), 'SDL_RENDER_DRIVER': 'software'}
        argv = [str(executable), '--project-path', str(runtime), '--config-path', str(root / 'config'),
                '--save-path', str(root / 'save'), '--new-game', '--no-rtp', '--no-audio', '--window',
                '--no-vsync', '--fps-limit', '60', '--engine', 'rpg2k', '--record-input', str(root / 'consumed.log')]
        with DesktopProcess(argv, cwd=root, env=env, inherited_handles=channel.handles) as target:
            return supervisor(target, directory, session, channel, runtime_sha256).run()
