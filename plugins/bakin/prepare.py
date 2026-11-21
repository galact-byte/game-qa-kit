"""Copy only the registered build and validate the launcher's preparation receipt."""
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import subprocess
import time
import uuid

from gameqakit.admission import no_links, validate_destination

HERE = Path(__file__).resolve().parent
BUILD = json.loads((HERE / 'build-manifest.json').read_text(encoding='utf-8'))
PREPARATION_SECONDS = 300
STARTUP_SECONDS = 60


def inspect(source, artifacts):
    blockers = []
    for field, verify, path, recovery in (
            ('source', source_digests, source, '请选择已登记且未修改的 Bakin 固定构建根目录。'),
            ('artifacts', artifact_digests, artifacts, '请选择已登记的本机 Bakin 桥与补丁器目录。')):
        try:
            if not path:
                raise ValueError('尚未选择所需目录')
            verify(path)
        except (OSError, ValueError) as exc:
            blockers.append(dict(code=field + '_not_ready', message=str(exc), field=field, recovery=recovery))
    return blockers



def checkpoint(deadline, cancelled):
    if cancelled():
        raise InterruptedError('Bakin preparation cancelled')
    if time.monotonic() >= deadline:
        raise TimeoutError('Bakin preparation exceeded its whole-stage deadline')


def component(root, name):
    if (not isinstance(name, str) or not name or '\\' in name or ':' in name
            or PurePosixPath(name).is_absolute() or PureWindowsPath(name).is_absolute()
            or any(part in ('', '.', '..') for part in name.split('/'))):
        raise ValueError('Unregistered or escaping component path')
    path = Path(root) / name
    no_links(path)
    return path


def digest(path, *, deadline, cancelled=lambda: False):
    no_links(path)
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while True:
            checkpoint(deadline, cancelled)
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            result.update(chunk)
    no_links(path)
    return result.hexdigest()


def source_digests(source, *, deadline=None, cancelled=lambda: False):
    deadline = time.monotonic() + PREPARATION_SECONDS if deadline is None else deadline
    actual = {}
    for name, expected in BUILD['files'].items():
        path = component(source, name)
        if path.stat().st_size != expected['bytes']:
            raise ValueError('Unregistered Bakin component size: ' + name)
        actual[name] = digest(path, deadline=deadline, cancelled=cancelled)
        if actual[name] != expected['sha256']:
            raise ValueError('Unregistered Bakin component digest: ' + name)
    return actual


def copy_registered(source, target, *, deadline, cancelled=lambda: False):
    checkpoint(deadline, cancelled)
    source, target = Path(source).absolute(), Path(target).absolute()
    validate_destination(source, target)
    # Validate every path before creating any destination directories.
    for name in BUILD['files']:
        component(source, name)
        component(target, name)
    original = source_digests(source, deadline=deadline, cancelled=cancelled)
    checkpoint(deadline, cancelled)
    target.mkdir()
    for name, expected in BUILD['files'].items():
        path = component(source, name)
        output = component(target, name)
        output.parent.mkdir(parents=True, exist_ok=True)
        copied = hashlib.sha256()
        with path.open('rb') as reader, output.open('xb') as writer:
            while True:
                checkpoint(deadline, cancelled)
                chunk = reader.read(1024 * 1024)
                if not chunk:
                    break
                copied.update(chunk)
                writer.write(chunk)
        if (output.stat().st_size != expected['bytes'] or copied.hexdigest() != original[name]
                or digest(output, deadline=deadline, cancelled=cancelled) != original[name]):
            raise ValueError('Bakin component changed during copy: ' + name)
    return original


def validate_receipt(receipt, runtime, session, pid, creation_id, version):
    fields = {'protocol', 'phase', 'session_id', 'pid', 'creation_id', 'executable', 'arguments'}
    if (not isinstance(receipt, dict) or set(receipt) != fields
            or type(receipt.get('protocol')) is not int or receipt['protocol'] != 2
            or receipt.get('phase') != 'prepared' or receipt.get('session_id') != session
            or type(receipt.get('pid')) is not int or receipt['pid'] != pid
            or receipt.get('creation_id') != creation_id
            or not isinstance(receipt.get('executable'), str)
            or not isinstance(receipt.get('arguments'), str) or len(receipt['arguments']) > 16384):
        raise ValueError('Invalid Bakin preparation receipt identity or schema')
    runtime = Path(runtime).resolve()
    launcher = runtime / BUILD['launcher']
    executable = runtime / 'data/bakinplayer.exe'
    if Path(receipt['executable']).resolve() != executable:
        raise ValueError('Preparation attempted to launch a different executable')
    match = re.fullmatch(r'/TMP="([^"]+)"\|"([^"]+)"\|"([^"]+)"\|([0-9]+) (.+)', receipt['arguments'])
    if match is None:
        raise ValueError('Unexpected native Bakin launch arguments')
    data, native_launcher, unpacked, native_version, tail = match.groups()
    data, native_launcher, unpacked = map(lambda value: Path(value).resolve(), (data, native_launcher, unpacked))
    if (data != runtime / 'data' or native_launcher != launcher or int(native_version) != version
            or unpacked == runtime / 'temp' or runtime / 'temp' not in unpacked.parents
            or Path(tail.strip('"')).resolve() != launcher):
        raise ValueError('Native launch arguments escape their bound runtime or version')
    for path in (data, native_launcher, unpacked):
        no_links(path)
    # Preserve the native argument values as argv, without invoking a shell.
    return dict(executable=str(executable),
                arguments=['/TMP=' + '|'.join((str(data), str(launcher), str(unpacked), str(version))), str(launcher)],
                unpacked=str(unpacked), native_arguments=receipt['arguments'])


def creation_identity(target):
    import ctypes
    from ctypes import wintypes as W
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetProcessTimes.argtypes = [W.HANDLE] + [ctypes.POINTER(W.FILETIME)] * 4
    creation, exit_time, kernel_time, user_time = (W.FILETIME() for _ in range(4))
    if not kernel.GetProcessTimes(target.process, *map(ctypes.byref, (creation, exit_time, kernel_time, user_time))):
        raise ctypes.WinError(ctypes.get_last_error())
    return str((creation.dwHighDateTime << 32) | creation.dwLowDateTime)


def artifact_digests(artifacts):
    from .build import OUTPUTS
    artifacts = Path(artifacts)
    expected = json.loads((HERE / 'artifact-manifest.json').read_text(encoding='utf-8'))
    if set(expected) != set(OUTPUTS):
        raise ValueError('Incomplete registered Bakin artifacts')
    for name, sha in expected.items():
        path = component(artifacts, name)
        if digest(path, deadline=time.monotonic() + 10) != sha:
            raise ValueError('Unregistered Bakin artifact: ' + name)
    return expected


def patch(mode, source, artifacts, profile, report, *, deadline, cancelled):
    checkpoint(deadline, cancelled)
    bridge = 'GameQaKit.Bakin.' + ('PreparedLaunch.dll' if mode == 'launcher' else 'RuntimeBridge.dll')
    result = subprocess.run([str(Path(artifacts) / 'GameQaKit.Bakin.PatchTool.exe'), mode,
        str(source), str(source), str(Path(artifacts) / bridge), str(profile), str(report)],
        capture_output=True, text=True, encoding='utf-8', errors='replace',
        timeout=max(.01, min(30, deadline - time.monotonic())))
    checkpoint(deadline, cancelled)
    if result.returncode:
        raise RuntimeError('Bakin patch failed: ' + result.stdout + result.stderr)


def launcher_prepare(runtime, artifacts, directory, session, *, deadline, cancelled=lambda: False):
    """Keep native extraction; replace only its final unused Process.Start result."""
    from gameqakit.desktop import DesktopProcess
    from gameqakit.duration_host import read, publish
    import psutil
    runtime, artifacts, directory = map(lambda p: Path(p).absolute(), (runtime, artifacts, directory))
    artifact_digests(artifacts)
    directory.mkdir(parents=True, exist_ok=True)
    for name in ('temp', 'profile'):
        (runtime / name).mkdir(exist_ok=True)
    launcher = component(runtime, BUILD['launcher'])
    shutil.copyfile(artifacts / 'GameQaKit.Bakin.PreparedLaunch.dll', runtime / 'GameQaKit.Bakin.PreparedLaunch.dll')
    backups = directory / 'original-components'
    backups.mkdir()
    for name in (BUILD['launcher'], 'data/bakinengine.dll'):
        shutil.copyfile(component(runtime, name), backups / Path(name).name)
    patch('launcher', launcher, artifacts, runtime / 'profile', directory / 'launcher-patch.txt',
          deadline=deadline, cancelled=cancelled)
    env = dict(TEMP=str(runtime/'temp'), TMP=str(runtime/'temp'),
               GAMEQAKIT_BAKIN_PREPARE=str(directory), GAMEQAKIT_BAKIN_SESSION=session)
    start = time.monotonic()
    with DesktopProcess([str(launcher)], cwd=runtime, env=env, detached_stdio=True) as target:
        identity = creation_identity(target)
        publish(directory/'preparation-launch.json',dict(session_id=session,pid=target.pid,creation_id=identity,desktop=target.desktop_name))
        target.run()
        while target.alive():
            checkpoint(deadline, cancelled)
            # This launcher has no native child before the replaced final call.
            if psutil.Process(target.pid).children(recursive=True):
                raise RuntimeError('Player or other unexpected child started during preparation')
            time.sleep(.02)
        receipt = read(directory / 'prepared.json')
        with (runtime/'data/data.rbpack').open('rb') as stream:
            header=stream.read(8)
        if header[:6] != b'BKNPAK': raise ValueError('Invalid registered package header')
        native = validate_receipt(receipt, runtime, session, target.pid, identity, int.from_bytes(header[6:8], 'big'))
    if digest(runtime/'data/bakinplayer.exe',deadline=deadline,cancelled=cancelled) != BUILD['player']['sha256']:
        raise ValueError('Launcher did not restore the registered player')
    publish(directory/'prepared-native.json',dict(native,elapsed_ms=round((time.monotonic()-start)*1000),player_started=False))
    return native


def extracted_digests(runtime, native, *, deadline, cancelled=lambda: False):
    """Explicitly inventory first preparation; compare against its frozen registry thereafter."""
    runtime=Path(runtime)
    outputs={}
    for base in (runtime/'data',Path(native['unpacked'])):
        for path in sorted(base.rglob('*')):
            checkpoint(deadline,cancelled)
            no_links(path)
            if path.is_file():
                name=('unpacked/' + path.relative_to(Path(native['unpacked'])).as_posix()
                      if path.is_relative_to(Path(native['unpacked'])) else path.relative_to(runtime).as_posix())
                outputs[name]=dict(bytes=path.stat().st_size,sha256=digest(path,deadline=deadline,cancelled=cancelled))
    return outputs


def install_bridge(runtime, artifacts, directory, native, *, deadline, cancelled=lambda:False):
    runtime,artifacts,directory=map(Path,(runtime,artifacts,directory))
    actual=extracted_digests(runtime,native,deadline=deadline,cancelled=cancelled)
    expected=json.loads((HERE/'extracted-manifest.json').read_text(encoding='utf-8'))
    if actual != expected:
        raise ValueError('Prepared components do not match the frozen extraction registry')
    for name in BUILD['files']:
        if name.startswith('data/') and actual[name] != BUILD['files'][name]:
            raise ValueError('Preparation changed a registered data component: '+name)
    shutil.copyfile(artifacts/'GameQaKit.Bakin.RuntimeBridge.dll',runtime/'data/GameQaKit.Bakin.RuntimeBridge.dll')
    patch('engine',runtime/'data/bakinengine.dll',artifacts,runtime/'profile/config.xml',directory/'engine-patch.txt',
          deadline=deadline,cancelled=cancelled)
    checkpoint(deadline,cancelled)


def prepare_runtime(source, target, artifacts, directory, session, *, cancelled=lambda:False):
    deadline=time.monotonic()+PREPARATION_SECONDS
    artifact_digests(artifacts)
    copied=copy_registered(source,target,deadline=deadline,cancelled=cancelled)
    native=launcher_prepare(target,artifacts,directory,session,deadline=deadline,cancelled=cancelled)
    install_bridge(target,artifacts,directory,native,deadline=deadline,cancelled=cancelled)
    return copied,native
