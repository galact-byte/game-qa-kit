"""Admit only the registered local runtime and unmodified repository-authored project."""
import json
from pathlib import Path

from gameqakit.admission import file_digest, no_links

RUNTIME='HaitokunoShoudou.exe'
SCRIPT='gameqa-artemis-fixture.iet'
ARTIFACT_DIGESTS={
    RUNTIME:'4480a20da5857ea329036c3031e671241f5efb73e9f75bf07412834d3c0a60b2',
    'd3dcompiler_47.dll':'efbdbbcd0d954f8fdc53467de5d89ad525e4e4a9cfff8a15d07c6fdb350c407f',
}
SOURCE_DIGESTS=json.loads((Path(__file__).parent/'samples/sha256.json').read_text(encoding='utf-8'))


def source_digests(source):
    root=Path(source); no_links(root)
    actual={}
    for item in root.iterdir():
        no_links(item)
        if not item.is_file(): raise ValueError('Artemis authored project cannot contain directories or saved data')
        actual[item.name]=file_digest(item)
    if actual!=SOURCE_DIGESTS: raise ValueError('Artemis authored scene or configuration was modified')
    return actual


def artifact_digests(artifacts):
    root=Path(artifacts); no_links(root)
    actual={}
    for name,digest in ARTIFACT_DIGESTS.items():
        path=root/name; no_links(path)
        if file_digest(path)!=digest: raise ValueError('Artemis runtime or DLL does not match the registered build')
        actual[name]=digest
    return actual


def inspect(source,artifacts):
    blockers=[]
    for field,verify,path,recovery in (
        ('source',source_digests,source,'选择仓库 plugins/artemis/samples/authored 自写场景。'),
        ('artifacts',artifact_digests,artifacts,'选择包含登记 Artemis EXE 和 d3dcompiler_47.dll 的本机组件目录。'),
    ):
        try:
            if not path: raise ValueError('尚未选择所需目录')
            verify(path)
        except (OSError,ValueError) as exc:
            blockers.append(dict(code=field+'_not_ready',message=str(exc),field=field,recovery=recovery))
    return blockers
