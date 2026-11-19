"""Admit only the registered authored YYC project and matching local executable/data."""
import json
from pathlib import Path

from gameqakit.admission import file_digest, no_links

HERE = Path(__file__).resolve().parent
RUNTIME = 'gameqa-gamemaker.exe'
MANIFEST = json.loads((HERE / 'source-manifest.json').read_text(encoding='utf-8'))
SOURCE_DIGESTS = MANIFEST['authored_source_sha256']
ARTIFACT_DIGESTS = json.loads((HERE / 'runtime-manifest.json').read_text(encoding='utf-8'))['artifact_sha256']


def source_digests(source):
    root = Path(source)
    no_links(root)
    actual = {}
    allowed_directories = {parent.as_posix() for name in SOURCE_DIGESTS for parent in Path(name).parents}

    def scan(directory):
        for path in directory.iterdir():
            no_links(path)
            relative = path.relative_to(root).as_posix()
            if path.is_dir():
                if relative not in allowed_directories:
                    raise ValueError('Unregistered GameMaker authored directory')
                scan(path)
            else:
                actual[relative] = file_digest(path)
    scan(root)
    if actual != SOURCE_DIGESTS:
        raise ValueError('GameMaker authored scene or configuration was modified')
    return actual


def artifact_digests(artifacts):
    root = Path(artifacts)
    no_links(root)
    actual = {}
    for name, digest in ARTIFACT_DIGESTS.items():
        path = root / name
        no_links(path)
        if file_digest(path) != digest:
            raise ValueError('GameMaker runtime or data does not match the registered build')
        actual[name] = digest
    return actual


def inspect(source, artifacts):
    blockers = []
    for field, verify, path, recovery in (
            ('source', source_digests, source, '选择仓库 plugins/gamemaker/samples 自写工程。'),
            ('artifacts', artifact_digests, artifacts, '选择包含登记 gameqa-gamemaker.exe 与 data.win 的本机构建目录。')):
        try:
            if not path:
                raise ValueError('尚未选择所需目录')
            verify(path)
        except (OSError, ValueError) as exc:
            blockers.append(dict(code=field + '_not_ready', message=str(exc), field=field, recovery=recovery))
    return blockers
