"""Wolf 的显式日文区域启动器，区域设置只作用于本次自有进程树。"""
import configparser
import hashlib
from pathlib import Path
import shutil
import time

from gameqakit.sessions import read_text

LOCALE_HASHES = {
    'LoaderDll.dll': '82fae0f44f4ca0c9c37907df74cef2415eeb5fae1cf8d4f36f34ffcaf7e3cc0c',
    'LocaleEmulator.dll': 'c79c175fdad174aa46a72197d148316299a56f950aaab1b84930d09ee1084a88',
}


class LocaleProcess:
    def __init__(self, executable, *, bridge, session, dll, injector, locale_dir, host_exe=None):
        executable, self.bridge = Path(executable).resolve(), Path(bridge).resolve()
        workspace = self.bridge.parent
        if (executable != workspace / 'runtime/Game.exe'
                or not (workspace / 'preparation.json').is_file()):
            raise ValueError('prepared_runtime_required')
        libraries = [Path(locale_dir) / name for name in LOCALE_HASHES]
        for library in libraries:
            if (not library.is_file()
                    or hashlib.sha256(library.read_bytes()).hexdigest() != LOCALE_HASHES[library.name]):
                raise ValueError('unsupported_locale_binary')
        from plugins.wolf.prepare import validate_image
        if validate_image(executable) != '2b811e3db991f45113087f979e3f375da7e46769a01f6e554584ed7c100b2f27':
            raise ValueError('locale_profile_required')
        if not session or any(c not in '0123456789abcdef' for c in session):
            raise ValueError('invalid_locale_session')
        dll, injector = Path(dll).resolve(strict=True), Path(injector).resolve(strict=True)
        host_exe = Path(host_exe or injector.with_name('WolfLocaleHost.exe')).resolve(strict=True)
        stage = workspace / 'launcher'
        stage.mkdir(exist_ok=False)
        for library in [host_exe, *libraries]:
            shutil.copy2(library, stage / library.name)
        from gameqakit.desktop import DesktopProcess
        self.host = DesktopProcess([str(stage / host_exe.name), str(self.bridge), str(dll), str(injector)],
                                   cwd=executable.parent,
                                   env={'GAMEQAKIT_BRIDGE_DIR': str(self.bridge), 'GAMEQAKIT_SESSION_ID': session})
        self.argv = [str(executable)]
        self.session = session
        self.pid = self.process = None
        self.started = False

    @property
    def desktop_name(self):
        return self.host.desktop_name

    @property
    def desktop(self):
        return self.host.desktop

    def _await_stage(self, stage):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            error = self.bridge / 'launch-error.txt'
            if error.exists():
                raise RuntimeError(error.read_text(encoding='utf-8'))
            if not self.host.alive():
                raise RuntimeError('locale_host_exited')
            status = self.bridge / 'launch.ini'
            if status.exists():
                parser = configparser.ConfigParser(interpolation=None)
                try:
                    parser.read_string(read_text(status))
                except (PermissionError, FileNotFoundError):
                    # A Windows replacement can outlast the shared reader's short retry.
                    time.sleep(.02)
                    continue
                row = parser['bridge']
                if (row.get('session_id') != self.session or row.getint('owner_pid') != self.host.pid
                        or row.getint('protocol') != 1 or row.getint('pid') <= 0):
                    raise RuntimeError('locale_launch_binding_mismatch')
                if row.get('stage') == stage:
                    return row.getint('pid')
            time.sleep(.02)
        raise TimeoutError('locale_launch_timeout:' + stage)

    def __enter__(self):
        try:
            self.host.__enter__()
            self.host.run()
            self.pid = self._await_stage('created')
            import ctypes
            from ctypes import wintypes as W
            from gameqakit.desktop import k
            from gameqakit.win32 import _pid_path
            if Path(_pid_path(self.pid)).resolve() != Path(self.argv[0]):
                raise RuntimeError('locale_target_path_mismatch')
            k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
            k.OpenProcess.restype = W.HANDLE
            self.process = k.OpenProcess(0x00100000 | 0x1000, False, self.pid)
            if not self.process:
                raise ctypes.WinError(ctypes.get_last_error())
            from gameqakit.control import write_connection
            write_connection(self.bridge, {'protocol': 1, 'session_id': self.session,
                             'pid': self.pid, 'owner_pid': self.host.pid, 'desktop': self.desktop_name,
                             'exe_path': self.argv[0], 'command_format': 'ini', 'control_transport': 'wolf-shm-v1'})
            if self._await_stage('ready') != self.pid:
                raise RuntimeError('locale_target_changed')
            return self
        except Exception:
            self.close()
            raise

    def run(self):
        if self.started or not self.process:
            raise RuntimeError('locale_target_not_suspended')
        from gameqakit.sessions import write_text
        write_text(self.bridge / 'start.ini', '[bridge]\nsession_id=' + self.session + '\npid=' + str(self.pid) + '\n')
        if self._await_stage('running') != self.pid:
            raise RuntimeError('locale_target_changed')
        self.started = True

    def alive(self):
        if not self.process:
            return False
        import ctypes
        from ctypes import wintypes as W
        from gameqakit.desktop import k
        code = W.DWORD()
        return bool(k.GetExitCodeProcess(self.process, ctypes.byref(code))) and code.value == 259

    def windows(self):
        from gameqakit.desktop import DesktopProcess
        return DesktopProcess.windows(self)

    def close(self):
        self.host.close()
        if self.process:
            from gameqakit.desktop import k
            k.WaitForSingleObject(self.process, 5000)
            k.CloseHandle(self.process)
            self.process = None

    def __exit__(self, *_args):
        self.close()
