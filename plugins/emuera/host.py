"""固定 Emuera 自写工程的 v2 协议宿主与独立 Job 监督。"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from gameqakit.desktop import DesktopProcess
from gameqakit.duration_host import Supervisor as FileSupervisor, IDENT, open_shared, publish, read

RUNTIME_SHA256 = 'a9af3d1e8b7eb4b9323faf6da6d770addf507e4fd2ee0eb5cfc2e58603fa58e6'


class Supervisor(FileSupervisor):
    engine = 'emuera'
    runtime_sha256 = RUNTIME_SHA256


def run(runtime, launcher, directory, session):
    if not IDENT.fullmatch(session):
        raise ValueError('会话编号无效')
    if any(not path.is_absolute() for path in (runtime, launcher, directory)):
        raise ValueError('必须使用绝对路径')
    if (directory / 'connection.json').exists():
        raise ValueError('必须使用全新的会话目录，保留原会话证据')
    if hashlib.sha256(runtime.read_bytes()).hexdigest() != RUNTIME_SHA256:
        raise ValueError('运行时摘要未验证，不启动')
    if runtime.parent != launcher.parent or not launcher.is_file() or runtime == launcher:
        raise ValueError('启动器与原运行时必须是同目录下的独立文件')
    if not (runtime.parent / 'csv/GAMEBASE.csv').is_file() or not (runtime.parent / 'erb/bridge.erb').is_file():
        raise ValueError('缺少显式自写工程夹具')
    directory.mkdir(parents=True, exist_ok=True)
    env = {'GAMEQAKIT_BRIDGE_DIR': str(directory), 'GAMEQAKIT_SESSION_ID': session,
           'GAMEQAKIT_ENGINE': str(runtime)}
    for key, folder in (('APPDATA', 'appdata'), ('LOCALAPPDATA', 'localappdata'), ('TMP', 'tmp'), ('TEMP', 'tmp')):
        target = runtime.parent / folder
        target.mkdir(exist_ok=True)
        env[key] = str(target)
    with DesktopProcess([str(launcher)], cwd=runtime.parent, env=env) as target:
        return Supervisor(target, directory, session).run()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', required=True, type=Path)
    parser.add_argument('--launcher', required=True, type=Path)
    parser.add_argument('--directory', required=True, type=Path)
    parser.add_argument('--session', required=True)
    args = parser.parse_args()
    raise SystemExit(run(args.runtime.resolve(), args.launcher.resolve(), args.directory.resolve(), args.session))
