"""Admission for a fixed original SWF, Ruffle release and system Edge build."""
import os
from pathlib import Path

from gameqakit.admission import file_digest, no_links

SCRIPT = 'gameqa-flash-fixture.swf'
SCRIPT_SHA = '801283386c61f1f9955823d534d862a631d72c318571bd0400929673608b0edc'
ARCHIVE = Path(__file__).with_name('ruffle-0.7.1-web-selfhosted.zip')
ARCHIVE_SHA = 'ef12edbee135d581a9cd523837d746da30a07a64dbf1cf472d2e472c656f4b5e'
RUNTIME = 'msedge.exe'
VERSION = '157.0.4341.60'
EDGE_FILES = {
    RUNTIME: '2497874639116d8edc3de34b3fd83f72fa099112a5d2a426e1f08fcf39a99612',
    VERSION + '/msedge.dll': 'e4c51cda4e2bc9f3896f23b14bc35166941d6c4e799428c76d64a7d55163d09d',
}


def source_digests(source):
    root = Path(source)
    no_links(root / SCRIPT)
    if {item.name for item in root.iterdir()} != {SCRIPT} or file_digest(root / SCRIPT) != SCRIPT_SHA:
        raise ValueError('仅接受只含登记自写 SWF 的 Flash 场景目录')
    return {SCRIPT: SCRIPT_SHA}


def artifact_digests(artifacts):
    if os.name != 'nt' or not artifacts:
        raise ValueError('未找到已登记的 Edge Windows x64 安装')
    root = Path(artifacts)
    for name, digest in EDGE_FILES.items():
        path = root / name
        no_links(path)
        if not path.is_file() or file_digest(path) != digest:
            raise ValueError('Edge 安装不匹配已登记的 ' + VERSION + ' 构建：' + name)
    no_links(ARCHIVE)
    if file_digest(ARCHIVE) != ARCHIVE_SHA:
        raise ValueError('Ruffle Web 自托管包摘要不符')
    return {**EDGE_FILES, ARCHIVE.name: ARCHIVE_SHA}


def inspect(source, artifacts):
    blockers = []
    root = None
    try:
        source_digests(source)
    except (OSError, ValueError) as exc:
        blockers.append(dict(code='source_not_ready', message=str(exc), field='source',
                             recovery='请选择仓库自写 Flash 场景目录。'))
    roots = ([Path(artifacts).absolute()] if artifacts else
             [Path(os.environ[name]) / 'Microsoft/Edge/Application'
              for name in ('ProgramFiles(x86)', 'ProgramFiles', 'LOCALAPPDATA') if os.environ.get(name)])
    failure = '未找到已登记的 Edge Windows x64 安装'
    for candidate in roots:
        if not candidate.is_dir():
            continue
        try:
            artifact_digests(candidate)
            root = candidate
            break
        except (OSError, ValueError) as exc:
            failure = str(exc)
    if root is None:
        blockers.append(dict(code='artifacts_not_ready', message=failure, field='artifacts',
                             recovery='Edge 更新后的构建需要重新验收并登记。'))
    return blockers, root
