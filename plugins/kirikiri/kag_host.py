"""KAG 消费回执校验；受保护商业启动入口另由原生宿主提供。"""
import ctypes
from ctypes import wintypes as W
from gameqakit.duration_host import Supervisor as FileSupervisor, publish
from gameqakit.win32 import user32
from plugins.kirikiri.host import Supervisor as FixtureSupervisor, read_struct, SnapshotPending

user32.GetClassNameW.argtypes = [W.HWND, W.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int


def kag_windows(target):
    windows = []
    for item in target.windows():
        name = ctypes.create_unicode_buffer(256)
        if not user32.GetClassNameW(item['hwnd'], name, len(name)):
            raise ValueError('KAG window class unavailable')
        if name.value == 'TTVPWindowForm':
            windows.append(item)
    return windows


class Supervisor(FixtureSupervisor):
    def deadlines(self):
        if getattr(self, 'invalid_window', False):
            publish(self.directory / 'status.json', self.state())
            return 'invalid_kag_window'
        return super().deadlines()

    def observe(self):
        try:
            state = read_struct(self.directory / 'target-observer.tjs')
        except ValueError as error:
            if str(error) == '观察数据不完整':
                raise SnapshotPending from error
            raise
        if not state or state.get('begin_seq') != state.get('seq') or state.get('end_seq') != state.get('seq'):
            raise SnapshotPending
        # Fixed runtime Window objects use this class; Delphi/IME helpers do not.
        windows = kag_windows(self.target)
        if not windows and self.closing:
            raise SnapshotPending
        if len(windows) != 1:
            self.invalid_window = True
            raise ValueError('KAG window count invalid: ' + repr(windows))
        if (state.get('hwnd') not in {item['hwnd'] for item in windows}
                or state.get('session_id') != self.identity['session_id'] or state.get('pid') != self.target.pid
                or type(state.get('hwnd')) is not int or state.get('held') not in (0, 1)
                or state.get('ready') != 1 or state.get('wrapped') != 1 or not isinstance(state.get('events'), list)):
            raise ValueError('KAG 观察身份或输入覆层无效')
        state['held'], state['ready'], state['wrapped'] = bool(state['held']), True, True
        for event in state['events']:
            expected = 1 if event.get('kind') == 'down' else 0
            count = event.get('query_count')
            if (event.get('kind') not in ('down', 'up') or event.get('after_original_handler') != 1
                    or type(count) is not int or not 0 <= count <= 100000
                    or event.get('query') != expected
                    or (count == 0 and event.get('handler_query') != -1)
                    or (count > 0 and event.get('handler_query') != expected)
                    or event.get('other_key') != event.get('other_native')):
                raise ValueError('KAG 原处理器回执或真实键查询无效')
            event['after_original_handler'] = True
        publish(self.directory / 'observer.json', state)
        FileSupervisor.observe(self)
