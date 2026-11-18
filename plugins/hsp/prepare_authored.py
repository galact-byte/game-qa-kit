"""Admission for one repository-authored HSP 3.7 probe."""
from pathlib import Path

from gameqakit.admission import file_digest, no_links

SCRIPT = 'gameqa-hsp-fixture.ax'
SCRIPT_SHA256 = '6ca089127a166fedbe0253110636ad28f0c53103fb296c68cfd563cd2f78fb7c'
RUNTIME = 'hsp3.exe'
RUNTIME_SHA256 = '5eeb5ed1bb3f8d7aeac90d35e2f4c1d15f02586a8e8a79484072838b95b7fec8'
BRIDGE = 'GameQaHsp.dll'


def source_digests(source):
    root = Path(source)
    no_links(root / SCRIPT)
    if {item.name for item in root.iterdir()} != {SCRIPT} or file_digest(root / SCRIPT) != SCRIPT_SHA256:
        raise ValueError('仅接受只含登记自写 HSP 字节码的场景目录')
    return {SCRIPT: SCRIPT_SHA256}


def artifact_digests(artifacts):
    root = Path(artifacts)
    for name in (RUNTIME, BRIDGE):
        no_links(root / name)
    if file_digest(root / RUNTIME) != RUNTIME_SHA256:
        raise ValueError('运行时不匹配已登记的 HSP 3.7 x86 构建')
    return {RUNTIME: RUNTIME_SHA256, BRIDGE: file_digest(root / BRIDGE)}


def inspect(source, artifacts):
    blockers = []
    for field, verify, path, recovery in (
            ('source', source_digests, source, '请选择只含已登记自写字节码的目录。'),
            ('artifacts', artifact_digests, artifacts, '请选择含已登记 hsp3.exe 及本仓库构建 GameQaHsp.dll 的目录。')):
        try:
            if not path:
                raise ValueError('尚未选择所需目录')
            verify(path)
        except (OSError, ValueError) as exc:
            blockers.append(dict(code=field + '_not_ready', message=str(exc), field=field, recovery=recovery))
    return blockers
