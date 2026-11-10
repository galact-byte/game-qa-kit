"""为已验证 Wolf 构建建立全新隔离副本；源目录始终只读。"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct

SUPPORTED_SHA256 = '90800e5dff9fa992a1fa78be5b2d5158baf2e9cc9603a45d964358e919f6868a'


def validate_image(path):
    path = Path(path)
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != SUPPORTED_SHA256:
        raise ValueError('不支持的 Wolf EXE：必须重新验证完整构建，不能关闭哈希校验')
    if len(data) < 64 or data[:2] != b'MZ':
        raise ValueError('无效 PE 文件')
    offset = struct.unpack_from('<I', data, 0x3c)[0]
    if offset + 24 > len(data) or data[offset:offset + 4] != b'PE\0\0':
        raise ValueError('无效 PE 文件')
    if struct.unpack_from('<H', data, offset + 4)[0] != 0x14c:
        raise ValueError('仅支持 Windows x86 Wolf 构建')
    return SUPPORTED_SHA256


def prepare(source, workspace):
    source, workspace = Path(source).resolve(), Path(workspace).resolve()
    if source == workspace or source in workspace.parents or workspace in source.parents:
        raise ValueError('源目录与工作区不得重叠')
    if workspace.exists():
        raise FileExistsError('工作区必须不存在，禁止覆盖已有测试或原始数据')
    if not source.is_dir():
        raise ValueError('源游戏目录不存在')
    for path in (source, *source.rglob('*')):
        if path.is_symlink() or path.is_junction():
            raise ValueError('源目录含链接，无法保证副本的数据边界')
    validate_image(source / 'Game.exe')
    workspace.mkdir(parents=True, exist_ok=False)
    runtime = workspace / 'runtime'
    shutil.copytree(source, runtime, ignore=shutil.ignore_patterns('Save', 'SaveData'))
    record = {'source': str(source), 'runtime': str(runtime),
              'exe_sha256': SUPPORTED_SHA256, 'copied_user_saves': False,
              'excluded': ['Save', 'SaveData'], 'source_modified': False}
    (workspace / 'preparation.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    (workspace / '测试数据记录.md').write_text(
        '# Wolf 测试副本记录\n\n'
        f'- 原目录（只读）：`{source}`\n- 本次副本：`{runtime}`\n'
        '- 改动：仅复制游戏资源；不带原 Save／SaveData，不改原档。\n'
        '- 存档／槽：测试副本自建，不使用原目录任何槽。\n'
        '- 改前备份：原目录即只读基线；无需覆盖原文件。\n'
        '- 还原：退出本次 Job 后回收 runtime；原目录不需还原。\n', encoding='utf-8')
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('workspace', type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.workspace), ensure_ascii=False, indent=2))
