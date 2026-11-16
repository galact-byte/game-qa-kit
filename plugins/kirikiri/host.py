"""固定 KiriKiri 自写工程的 v2 独立宿主；复用既有期限与取图监督。"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from gameqakit.desktop import DesktopProcess
from gameqakit.win32 import window_pid
from gameqakit.duration_host import Supervisor as FileSupervisor, IDENT, open_shared, publish
from plugins.kirikiri.struct_data import decode

RUNTIME_SHA256 = '8db00ce0cd63748296d4e13b6916a4f1b5b166078f806d731202d15b210efe11'
BASE = Path(__file__).resolve().parents[2]


def write_text(path, text):
    temporary = path.with_name(path.name + '.part')
    try:
        temporary.write_text(text, encoding='ascii')
        deadline = time.monotonic() + .1
        while True:
            try:
                temporary.replace(path)
                return
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.005)
    finally:
        temporary.unlink(missing_ok=True)


def read_struct(path):
    deadline = time.monotonic() + .1
    while True:
        try:
            with open_shared(path) as stream:
                raw = stream.read(65537)
            if len(raw) > 65536:
                raise ValueError('观察文件超过 64 KiB')
            return decode(raw.decode('utf-16' if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'))
        except FileNotFoundError:
            return {}
        except (PermissionError, UnicodeError, ValueError):
            # saveStruct 不提供原子替换；仅在固定 I/O 上限内等待完整快照。
            if time.monotonic() >= deadline:
                raise
            time.sleep(.005)


class SnapshotPending(Exception):
    """目标正在发布快照；本轮不接受命令，仍由宿主检查原期限。"""


class Supervisor(FileSupervisor):
    engine = 'kirikiri'
    runtime_sha256 = RUNTIME_SHA256

    def __init__(self, target, directory, session):
        super().__init__(target, directory, session)
        self.command_seq = 0
        self.write_command('target-config.tjs', 'config', 'config')

    def write_command(self, filename, ident, kind):
        self.command_seq += 1
        text = (f'GQK1\nseq={self.command_seq}\nsession={self.identity["session_id"]}\n'
                f'pid={self.target.pid}\nid={ident}\nkind={kind}\nend={self.command_seq}\n')
        write_text(self.directory / filename, text)

    def send(self, ident, kind):
        if not IDENT.fullmatch(ident) or kind not in ('down', 'up', 'close'):
            raise ValueError('目标命令编号或动作无效')
        self.write_command('target-input.tjs', ident, kind)

    def pump(self):
        try:
            super().pump()
        except SnapshotPending:
            pass

    def observe(self):
        try:
            state = read_struct(self.directory / 'target-observer.tjs')
        except ValueError as error:
            if str(error) == '观察数据不完整':
                raise SnapshotPending from error
            raise
        if not state:
            raise SnapshotPending
        if state.get('begin_seq') != state.get('seq') or state.get('end_seq') != state.get('seq'):
            raise SnapshotPending
        windows = self.target.windows()
        if state.get('hwnd') not in {item['hwnd'] for item in windows}:
            raise ValueError('目标窗口句柄不匹配：' + repr((state.get('hwnd'), windows)))
        if (state.get('session_id') != self.identity['session_id'] or state.get('pid') != self.target.pid
                or type(state.get('hwnd')) is not int
                or state.get('begin_seq') != state.get('seq') or state.get('end_seq') != state.get('seq')
                or state.get('held') not in (0, 1) or state.get('ready') != 1
                or state.get('wrapped') != 1 or not isinstance(state.get('events'), list)):
            raise ValueError('目标观察身份、窗口或覆层状态无效')
        state['held'], state['ready'], state['wrapped'] = bool(state['held']), True, True
        for event in state['events']:
            expected = 1 if event.get('kind') == 'down' else 0
            if (event.get('kind') not in ('down', 'up') or event.get('after_original_handler') != 1
                    or event.get('query') != expected or event.get('handler_query') != expected
                    or event.get('other_key') != event.get('other_native')):
                raise ValueError('原处理器消费与键查询状态不一致')
            event['after_original_handler'] = True
        publish(self.directory / 'observer.json', state)
        super().observe()

    def run(self):
        try:
            return super().run()
        finally:
            detached = read_struct(self.directory / 'detached.tjs')
            if detached:
                publish(self.directory / 'detached.json', {'restored': detached.get('restored') == 1})


def validate(runtime, directory, session):
    if not isinstance(session, str) or not IDENT.fullmatch(session):
        raise ValueError('会话编号无效')
    if not runtime.is_absolute() or not directory.is_absolute():
        raise ValueError('必须使用绝对路径')
    if not runtime.is_file() or hashlib.sha256(runtime.read_bytes()).hexdigest() != RUNTIME_SHA256:
        raise ValueError('运行时摘要未验证，不启动')
    if directory.exists() and any(directory.iterdir()):
        raise ValueError('必须使用全新的空会话目录，保留原会话证据')
    project = runtime.parent
    for name, source in (('startup.tjs', BASE / 'tests/kirikiri_fixture/startup.tjs'),
                         ('GameQaBridge.tjs', Path(__file__).with_name('GameQaBridge.tjs'))):
        if not (project / name).is_file() or (project / name).read_bytes() != source.read_bytes():
            raise ValueError('缺少显式自写工程夹具或桥版本不一致')
    allowed = {runtime.name, 'startup.tjs', 'GameQaBridge.tjs', 'test-mode.tjs',
               'savedata', 'appdata', 'localappdata', 'tmp', 'fixture-error.tjs'}
    if any(item.name not in allowed for item in project.iterdir()):
        raise ValueError('工程目录含未授权资源、插件或配置')
    for path in (runtime, directory, *project.rglob('*')):
        if path.is_symlink() or (path.exists() and getattr(path.lstat(), 'st_file_attributes', 0) & 0x400):
            raise ValueError('测试路径不允许符号链接或重解析点')


def run(runtime, directory, session):
    validate(runtime, directory, session)
    directory.mkdir(parents=True, exist_ok=True)
    environment = {}
    for key, folder in (('APPDATA', 'appdata'), ('LOCALAPPDATA', 'localappdata'), ('TMP', 'tmp'), ('TEMP', 'tmp')):
        path = runtime.parent / folder
        path.mkdir(exist_ok=True)
        environment[key] = str(path)
    (runtime.parent / 'savedata').mkdir(exist_ok=True)
    with DesktopProcess([str(runtime), str(runtime.parent), '-datapath=' + (runtime.parent / 'savedata').as_posix(),
                         '-gameqadir=' + directory.as_posix()], cwd=runtime.parent, env=environment) as target:
        return Supervisor(target, directory, session).run()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', required=True, type=Path)
    parser.add_argument('--directory', required=True, type=Path)
    parser.add_argument('--session', required=True)
    args = parser.parse_args()
    raise SystemExit(run(args.runtime.absolute(), args.directory.absolute(), args.session))
