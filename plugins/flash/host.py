"""Supervise input and capture independently of the browser IPC thread."""
from base64 import b64decode
from io import BytesIO
import shutil
import time

from PIL import Image

from gameqakit.admission import file_digest
from gameqakit.duration_host import Supervisor
from .prepare import EDGE_FILES, RUNTIME

RIGHT = dict(key='ArrowRight', code='ArrowRight', windowsVirtualKeyCode=39, nativeVirtualKeyCode=39)
GREEN = (80, 240, 100)
ORANGE = (242, 106, 42)


def landmarks(data):
    with Image.open(BytesIO(data)) as image:
        if image.size != (320, 240):
            raise ValueError('Flash 画布尺寸无效')
        image = image.convert('RGB')
        if image.getpixel((210, 200)) != (35, 62, 95):
            raise ValueError('Flash 画布背景无效')
        positions = ([x for x in (40, 128) if image.getpixel((x + 8, 110)) == GREEN],
                     [x for x in (32, 95) if image.getpixel((x + 8, 30)) == ORANGE])
        if any(len(values) != 1 for values in positions):
            raise ValueError('Flash 画布缺少探针色块')
        return positions[0][0], positions[1][0]


class FlashSupervisor(Supervisor):
    engine = 'flash'
    runtime_sha256 = EDGE_FILES[RUNTIME]
    actions = ('move', 'wait')
    button = 'right'

    def __init__(self, browser, directory, session, tab):
        super().__init__(browser.host, directory, session)
        self.browser, self.tab = browser, tab
        self.pending_frame = None
        self.last_requested = 0
        self.marker = None
        self.focus = None
        self.observation_error = ''

    def state(self):
        return {**super().state(), 'observation_error': self.observation_error}

    def capture_fault(self, error):
        self.observation_error = str(error)
        self.fault(error)

    def accept(self, request):
        # Do not queue a new key-down behind a screenshot that may stall.
        if self.pending_frame is None:
            super().accept(request)

    def send(self, _ident, kind):
        if kind != 'close':
            key = {**RIGHT, 'type': 'rawKeyDown' if kind == 'down' else 'keyUp'}
            self.active[kind + '_future'] = self.browser.submit('Input.dispatchKeyEvent', key, session=self.tab)

    def start_capture(self, _ident=None, _purpose=None):
        if self.pending_frame or time.monotonic() - self.last_requested < .06:
            return
        self.last_requested = time.monotonic()
        future = self.browser.submit('Page.captureScreenshot', {
            'format': 'png', 'clip': dict(x=0, y=0, width=320, height=240, scale=1)}, session=self.tab)
        active = self.active or {}
        self.pending_frame = (future, self.last_requested, active.get('down_future'), active.get('up_future'))

    def observe(self):
        if not self.pending_frame or not self.pending_frame[0].done():
            return
        future, _, down, up = self.pending_frame
        self.pending_frame = None
        try:
            data = b64decode(future.result()['data'], validate=True)
            x, marker = landmarks(data)
        except (OSError, ValueError, RuntimeError) as exc:
            if self.observation_seq:
                self.capture_fault(exc)
            return
        if marker != self.marker:
            self.marker = marker
            self.changed = time.monotonic()
            self.ui_seq += 1
        if self.observation_seq == 0:
            if self.ui_seq < 2 or x != 40:
                return
            if self.focus is None:
                # Focus after real playback starts. Navigation may reset earlier focus.
                self.focus = self.browser.submit('Runtime.evaluate', {
                    'expression': '''(() => {
                        const player = document.querySelector('ruffle-player');
                        const container = player.shadowRoot.getElementById('container');
                        container.focus({preventScroll: true});
                        return player.shadowRoot.activeElement === container;
                    })()''', 'returnByValue': True,
                }, session=self.tab)
                return
            if not self.focus.done() or not self.focus.result()['result'].get('value'):
                return
        if self.error and not (self.active and self.active['keyed']):
            return
        self.last_capture = time.monotonic()
        self.observation_seq += 1
        self.observation = ('screenshots/startup.png' if self.observation_seq == 1 else
                            f'screenshots/observation-{self.observation_seq}.png')
        destination = self.directory / self.observation
        destination.parent.mkdir(exist_ok=True)
        destination.write_bytes(data)
        active = self.active
        if not active or not active['keyed']:
            return
        if down is not None and down is active.get('down_future') and x == 128 and not active['press']:
            down.result()
            active['press'] = True
            active['until'] = self.last_capture + active['request']['payload']['duration_ms'] / 1000
            active['pressed_screenshot'] = self.observation
        if (up is not None and up is active.get('up_future') and x == 40
                and active['press'] and not active['release']):
            up.result()
            active['release'] = True

    def progress(self):
        active = self.active
        if active:
            complete = active['release'] if active['keyed'] else time.monotonic() >= active['until']
            if complete and self.observation_seq > active['request']['after_observation_seq']:
                capture = f"screenshots/{active['request']['id']}.png"
                shutil.copyfile(self.directory / self.observation, self.directory / capture)
                self.finish(dict(screenshot=capture,
                                 screenshot_sha256=file_digest(self.directory / capture),
                                 pressed_screenshot=active.get('pressed_screenshot')))
        if self.stopping is not None and not self.active:
            self.reason = 'stop_requested'
            self.target.close()

    def pump(self):
        super().pump()
        if self.target.alive() and (not self.error or self.active):
            self.start_capture()

    def deadlines(self):
        now = time.monotonic()
        if self.pending_frame and now - self.pending_frame[1] >= 3:
            return 'capture_timeout'
        active = self.active
        if active and active['keyed'] and not active['press'] and now - active['started'] >= 1:
            self.capture_fault('press_not_confirmed')
        if self.observation_seq and not self.error and now - self.changed >= 1:
            self.capture_fault('activity_timeout')
        if self.error and not self.active and now - self.last_capture >= 1:
            return 'observation_failed'
        if active and active['release_started'] is not None and not active['release'] and now - active['release_started'] >= 1:
            return 'release_timeout'
        if not self.observation_seq and now - self.started >= 8:
            return 'startup_timeout'
        if self.stopping is not None and now - self.stopping >= 1:
            return 'shutdown_timeout'
        return None
