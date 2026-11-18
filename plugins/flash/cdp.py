"""CDP transport over two explicitly inherited Windows pipe handles."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import json
import msvcrt
import os

from gameqakit.desktop import DesktopProcess


class Browser:
    def __init__(self, executable, directory):
        self.executable = executable
        self.directory = directory
        self.host = None
        self._stack = ExitStack()
        self._id = 0

    def __enter__(self):
        with ExitStack() as resources, ExitStack() as child:
            child_read, parent_write = os.pipe()
            child.callback(os.close, child_read)
            self._output = resources.enter_context(os.fdopen(parent_write, 'wb'))
            parent_read, child_write = os.pipe()
            child.callback(os.close, child_write)
            self._input = resources.enter_context(os.fdopen(parent_read, 'rb'))
            handles = tuple(msvcrt.get_osfhandle(fd) for fd in (child_read, child_write))
            for handle in handles:
                os.set_handle_inheritable(handle, True)
            env = {}
            for name in ('APPDATA', 'LOCALAPPDATA', 'TEMP', 'TMP'):
                folder = self.directory / name.lower()
                folder.mkdir(exist_ok=True)
                env[name] = str(folder)
            self._io = resources.enter_context(ThreadPoolExecutor(max_workers=1))
            self.host = resources.enter_context(DesktopProcess([
                str(self.executable), '--headless', '--disable-background-networking',
                '--disable-component-update', '--disable-extensions', '--no-first-run',
                '--no-default-browser-check', '--disable-gpu', '--remote-debugging-pipe',
                '--remote-debugging-io-pipes=' + ','.join(map(str, handles)),
                '--user-data-dir=' + str(self.directory / 'profile'), 'about:blank',
            ], cwd=self.directory, env=env, inherited_handles=handles))
            self.host.run()
            self._stack = resources.pop_all()
        return self

    def __exit__(self, *args):
        return self._stack.__exit__(*args)

    def _exchange(self, method, params, session):
        self._id += 1
        ident = self._id
        request = {'id': ident, 'method': method, 'params': params or {}}
        if session:
            request['sessionId'] = session
        self._output.write(json.dumps(request).encode('utf-8') + b'\0')
        self._output.flush()
        packet = bytearray()
        while len(packet) <= 16 * 1024 * 1024:
            byte = self._input.read(1)
            if not byte:
                raise RuntimeError('浏览器私有管道已关闭')
            if byte != b'\0':
                packet.extend(byte)
                continue
            response = json.loads(packet)
            packet.clear()
            if response.get('id') != ident:
                continue
            if 'error' in response:
                raise RuntimeError(str(response['error']))
            return response['result']
        raise ValueError('浏览器消息超过大小上限')

    def submit(self, method, params=None, *, session=None):
        return self._io.submit(self._exchange, method, params, session)

    def call(self, method, params=None, *, session=None, timeout=3):
        try:
            return self.submit(method, params, session=session).result(timeout=timeout)
        except TimeoutError as exc:
            self.host.close()
            raise TimeoutError(f'Edge 私有管道调用超时：{method}') from exc
