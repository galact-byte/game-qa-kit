"""Register only the authored script and the recorded Ren'Py runtime files."""
from pathlib import Path

from gameqakit.admission import file_digest, fingerprint, no_links

SCRIPT = 'gameqa-renpy-fixture.rpy'
LAUNCHER = 'Eternum.py'
EXECUTABLE = 'lib/py3-windows-x86_64/pythonw.exe'
BRIDGE = Path(__file__).with_name('GameQaBridge.rpy')
SCRIPT_SHA256 = '579f36057f7d63ac2fa33b11e52107f4beb168863c0de9f9d76ec38780f39944'
RUNTIME_SHA256 = 'c603e17b619f48b6ced78bb4e9a61f3beb8883580477fe59e0b630d520cf7942'


def tree_digests(root):
    no_links(root)
    result = {}
    for path in root.rglob('*'):
        no_links(path)
        if path.is_file():
            result[path.relative_to(root).as_posix()] = file_digest(path)
    return result


def source_digests(source):
    root = Path(source)
    no_links(root)
    path = root / SCRIPT
    no_links(path)
    if {item.name for item in root.iterdir()} != {SCRIPT} or file_digest(path) != SCRIPT_SHA256:
        raise ValueError('仅接受未经修改的 RenPy 自写样本目录')
    return {SCRIPT: SCRIPT_SHA256}


def runtime_files(artifacts):
    root = Path(artifacts)
    no_links(root / LAUNCHER)
    values = {LAUNCHER: file_digest(root / LAUNCHER)}
    for name in ('lib', 'renpy'):
        values.update({f'{name}/{path}': digest for path, digest in tree_digests(root / name).items()})
    if EXECUTABLE not in values or fingerprint(values) != RUNTIME_SHA256:
        raise ValueError('运行时文件不匹配已登记的 RenPy 8.3.2 构建')
    return values


def artifact_digests(artifacts):
    runtime_files(artifacts)
    no_links(BRIDGE)
    return {'runtime': RUNTIME_SHA256, BRIDGE.name: file_digest(BRIDGE)}


def inspect(source, artifacts):
    blockers = []
    for field, verify, path, recovery in (
        ('source', source_digests, source, '选择只含仓库自写脚本的目录。'),
        ('artifacts', artifact_digests, artifacts, '选择已登记的本机 RenPy 8.3.2 运行时目录。'),
    ):
        try:
            if not path:
                raise ValueError('尚未选择固定构建目录')
            verify(path)
        except (OSError, ValueError) as exc:
            blockers.append(dict(code=field + '_not_ready', message=str(exc), field=field, recovery=recovery))
    return blockers
