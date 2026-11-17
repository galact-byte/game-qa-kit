"""固定商业 KAG 的显式准备器；不启动游戏，不接通用识别入口。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil

INPUTS = {
    'runtime.exe': '8db00ce0cd63748296d4e13b6916a4f1b5b166078f806d731202d15b210efe11',
    'data.xp3': 'b3c3d86d6e6a5c34100e1e2d8a6cde59ca7576fe1c6d8f0f0ff7c100a7ab045e',
    'wuvorbis.dll': '302b1eb97ed0f6110904e84c11bf7436e4cdddd414f4d24d105aba687f32b47f',
}
CODE = Path(__file__).resolve().parent
BRIDGES = ('AfterInit2.tjs', 'KagBridge.tjs')
DATA_DIRS = ('savedata', 'appdata', 'localappdata', 'tmp', 'bridge')
RESERVE = 256 * 1024 * 1024


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def safe_path(path):
    path = Path(path)
    text = str(path).replace('/', '\\')
    if (not path.is_absolute() or text.startswith('\\\\') or ':' in text[2:]
            or any(part.endswith((' ', '.')) for part in path.parts)):
        raise ValueError('仅允许本地绝对路径，不允许设备路径、网络路径或备用数据流')
    for part in (path, *path.parents):
        if part.exists() and (part.is_symlink() or getattr(part.lstat(), 'st_file_attributes', 0) & 0x400):
            raise ValueError('路径不允许重解析点或符号链接')
    return path.resolve()


def _contained(path, parent):
    return path == parent or parent in path.parents


def prepare(source, destination, executable):
    source, destination = safe_path(source), safe_path(destination)
    if destination.exists() or _contained(destination, source) or _contained(source, destination):
        raise ValueError('必须使用与原目录分离的全新会话目录')
    if Path(executable).name != executable or not executable.lower().endswith('.exe'):
        raise ValueError('必须显式指定原运行时文件名')
    originals = {name: source / (executable if name == 'runtime.exe' else name) for name in INPUTS}
    for name, file in originals.items():
        safe_path(file)
        if not file.is_file() or digest(file) != INPUTS[name]:
            raise ValueError('商业输入摘要不匹配：' + name)
    if not destination.parent.is_dir():
        raise ValueError('会话父目录必须已存在')
    needed = sum(file.stat().st_size for file in originals.values()) + RESERVE
    if shutil.disk_usage(destination.parent)[2] < needed:
        raise ValueError('空间不足，保留构建与运行数据余量')
    for name in BRIDGES:
        if not (CODE / name).is_file():
            raise ValueError('桥部署文件缺失：' + name)
    destination.mkdir(exist_ok=False)
    game = destination / 'game'
    game.mkdir()
    for name in DATA_DIRS:
        (destination / name).mkdir()
    records = []
    # 失败时保留未完成目录与已复制输入，不覆盖、不自动删去失败证据。
    for name, original in (*originals.items(), *((name, CODE / name) for name in BRIDGES)):
        target = game / name
        shutil.copyfile(original, target)
        expected = INPUTS[name] if name in INPUTS else digest(original)
        if digest(target) != expected:
            raise ValueError('复制后摘要不匹配：' + name)
        records.append(dict(source=str(original), destination='game/' + name, sha256=expected,
                            size=target.stat().st_size))
    report = dict(version=1, source=str(source), session_root=str(destination), files=records)
    (destination / 'preparation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


def validate(root):
    root = safe_path(root)
    report = json.loads((root / 'preparation.json').read_text(encoding='utf-8'))
    if report.get('version') != 1 or report.get('session_root') != str(root):
        raise ValueError('准备身份不匹配')
    expected = dict(INPUTS, **{name: digest(CODE / name) for name in BRIDGES})
    game = root / 'game'
    if {p.name for p in game.iterdir()} != set(expected):
        raise ValueError('部署目录含缺失或额外文件')
    for name, sha256 in expected.items():
        file = safe_path(game / name)
        if not file.is_file() or digest(file) != sha256 or file.stat().st_nlink != 1:
            raise ValueError('部署输入被替换或存在硬链接：' + name)
    for folder in DATA_DIRS:
        if not safe_path(root / folder).is_dir():
            raise ValueError('运行数据目录缺失')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--destination', required=True, type=Path)
    parser.add_argument('--executable', required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.destination, args.executable), ensure_ascii=False, indent=2))
