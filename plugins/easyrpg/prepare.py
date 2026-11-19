"""Admit only the authored RM2K fixture and the registered source-built runtime."""
import json
from pathlib import Path

from gameqakit.admission import file_digest, no_links

RUNTIME = 'GameQaEasyRpg.exe'
RUNTIME_SHA256 = '4b38bcd36a8806547a607b796e30b19d941c20cebe8c30165f53d7c8076b8220'
SOURCE_DIGESTS = json.loads((Path(__file__).parent / 'samples/rm2k-sha256.json').read_text(encoding='utf-8'))


def source_digests(source):
    root = Path(source)
    no_links(root)
    actual = {}
    def scan(directory):
        for item in directory.iterdir():
            no_links(item)
            if item.is_dir():
                scan(item)
            elif item.is_file():
                actual[item.relative_to(root).as_posix()] = file_digest(item)
            else:
                raise ValueError('不支持的样本文件类型')
    scan(root)
    if actual != SOURCE_DIGESTS:
        raise ValueError('仅接受未经修改且不带存档的仓库自写 RM2K 场景')
    return actual


def artifact_digests(artifacts):
    runtime = Path(artifacts) / RUNTIME
    no_links(runtime)
    if not RUNTIME_SHA256 or file_digest(runtime) != RUNTIME_SHA256:
        raise ValueError('运行时不匹配已登记的 GameQA EasyRPG x64 源码构建')
    return {RUNTIME: RUNTIME_SHA256}


def inspect(source, artifacts):
    blockers = []
    for field, verify, path, recovery in (
        ('source', source_digests, source, '选择仓库 plugins/easyrpg/samples/rm2k 自写场景。'),
        ('artifacts', artifact_digests, artifacts, '选择含已登记 GameQaEasyRpg.exe 的本机组件目录。'),
    ):
        try:
            if not path:
                raise ValueError('尚未选择所需目录')
            verify(path)
        except (OSError, ValueError) as exc:
            blockers.append(dict(code=field+'_not_ready',message=str(exc),field=field,recovery=recovery))
    return blockers
