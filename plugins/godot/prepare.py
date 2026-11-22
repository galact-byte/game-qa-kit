"""受管 Godot 接入仅复制已登记的自写夹具，不读取或重写 PCK 内容。"""
import json
from pathlib import Path
import sys

from gameqakit.admission import file_digest, no_links

PACK = "fixture.pck"
RUNTIME = "godot-runtime.exe"
BRIDGE = Path(__file__).with_name("GameQaBridge.gd").resolve()
PACK_SHA256 = "3f919ad24862ef86a32c857d9e62b880bab510d84cccbe1fa6747a68443c3f6c"
RUNTIME_SHA256 = "2a2ffce588f342dc40d4d913da7682bfda24fb986c543b1bf88afe69fe1e7aab"


def component():
    if sys.platform == 'darwin':
        record = json.loads(Path(__file__).with_name('macos-arm64-component.json').read_text(encoding='utf-8'))
        if not record.get('runtime_sha256'):
            raise ValueError('macOS 运行时尚未登记实际构建摘要')
        return record
    return {'runtime': RUNTIME, 'runtime_sha256': RUNTIME_SHA256, 'pack_sha256': PACK_SHA256,
            'support_files': {}}


def checked_digest(path, expected):
    no_links(path)
    if not path.is_file() or (actual := file_digest(path)) != expected:
        raise ValueError(f"{path.name} 不匹配已登记的 Godot 4.5.1 样本或运行时")
    return actual


def source_digests(source):
    source = Path(source)
    no_links(source)
    if {path.name for path in source.iterdir()} != {PACK}:
        raise ValueError("自写样本目录必须只有 fixture.pck")
    return {PACK: checked_digest(source / PACK, component()['pack_sha256'])}


def artifact_digests(artifacts):
    spec = component()
    return {spec['runtime']: checked_digest(Path(artifacts) / spec['runtime'], spec['runtime_sha256']),
            **{name: checked_digest(Path(artifacts) / name, digest) for name, digest in spec['support_files'].items()},
            BRIDGE.name: file_digest(BRIDGE)}


def inspect(source, artifacts):
    blockers = []
    for field, verify, path, recovery in (
        ("source", source_digests, source, "选择仓库生成的自写样本目录；商业 PCK 不接入。"),
        ("artifacts", artifact_digests, artifacts, "选择含已登记 godot-runtime.exe 的本机组件目录。"),
    ):
        try:
            if not path:
                raise ValueError("未选择固定构建目录")
            verify(path)
        except (OSError, ValueError) as exc:
            blockers.append(dict(code=field + "_not_ready", message=str(exc), field=field, recovery=recovery))
    return blockers
