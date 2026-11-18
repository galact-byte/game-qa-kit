"""Validate the registered KiriKiri runtime and authored Window fixture."""
from pathlib import Path

from gameqakit.admission import file_digest, no_links
from .host import FIXTURE, RUNTIME_SHA256

SCRIPT = FIXTURE.name
SCRIPT_SHA256 = '527b89f0cdbe1c0093595a3dfe1c6401a72fe80ca8a41258939225e6eecd2dfc'
RUNTIME = 'runtime.exe'
BRIDGE = Path(__file__).with_name('GameQaBridge.tjs')


def source_digests(source):
    root = Path(source)
    no_links(root / SCRIPT)
    if {path.name for path in root.iterdir()} != {SCRIPT} or file_digest(root / SCRIPT) != SCRIPT_SHA256:
        raise ValueError('仅接受只含登记自写 Window 脚本的 KiriKiri 场景目录')
    return {SCRIPT: SCRIPT_SHA256}


def artifact_digests(artifacts):
    runtime = Path(artifacts) / RUNTIME
    no_links(runtime)
    no_links(BRIDGE)
    if file_digest(runtime) != RUNTIME_SHA256:
        raise ValueError('运行时不匹配已登记的 KiriKiri 2.32.2.426 x86 构建')
    return {RUNTIME: RUNTIME_SHA256, BRIDGE.name: file_digest(BRIDGE)}


def inspect(source, artifacts):
    blockers = []
    for field, verify, path, recovery in (
            ('source', source_digests, source, '选择只含仓库自写 Window 场景的目录。'),
            ('artifacts', artifact_digests, artifacts, '选择含已登记 runtime.exe 的本机组件目录。')):
        try:
            if not path:
                raise ValueError('尚未选择所需目录')
            verify(path)
        except (OSError, ValueError) as exc:
            blockers.append(dict(code=field + '_not_ready', message=str(exc), field=field, recovery=recovery))
    return blockers
