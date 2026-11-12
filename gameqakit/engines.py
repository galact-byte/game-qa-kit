"""只读引擎证据与有界样本发现；识别不代表后台控制兼容。"""
from __future__ import annotations

from collections import deque
import hashlib
import json
from pathlib import Path
import re

_NATIVE_SAMPLE = json.loads((Path(__file__).resolve().parents[1] /
                            'plugins/native_input/samples/srpgstudio-x86-fixed.json').read_text(encoding='utf-8'))

# 资源树既不含入口，也不应随游戏体量放大盘点成本。
_RESOURCE_DIRS = {'audio', 'graphics', 'fonts', 'system', 'save', 'saves', 'savedata',
                  'img', 'images', 'movies', 'video', 'node_modules', 'managed',
                  'il2cpp_data', 'data', 'patch', 'resources', 'library', 'models',
                  'venv', '.venv', 'site-packages', 'custom_nodes', 'bepinex'}


def inspect_game(path):
    root = Path(path)
    if root.is_file():
        root = root.parent
    result = {'root': str(root.resolve()), 'engine': 'unknown', 'adapter': None,
              'evidence': [], 'control_verified': False}
    if not root.is_dir():
        raise FileNotFoundError(f'游戏目录不存在：{root}')

    def found(engine, evidence, adapter=None):
        return {**result, 'engine': engine, 'adapter': adapter, 'evidence': evidence}

    for prefix in ('', 'www/'):
        for core, engine in (('rmmz_core.js', 'rpgmaker_mz'), ('rpg_core.js', 'rpgmaker_mv')):
            marker = prefix + 'js/' + core
            if (root / marker).is_file():
                return found(engine, [marker], 'rpgmaker_js')
    ini = root / 'Game.ini'
    if ini.is_file() and ini.stat().st_size <= 16384:
        text = ini.read_bytes().decode('utf-8-sig', errors='replace')
        match = re.search(r'(?im)^\s*Library\s*=\s*[^\r\n]*\b(RGSS([123])\d{2}[A-Z]*\.dll)\s*$', text)
        if match:
            engine = {'1': 'rpgmaker_xp', '2': 'rpgmaker_vx', '3': 'rpgmaker_vxace'}[match[2]]
            return found(engine, ['Game.ini:Library=' + match[1]], 'rgss3' if match[2] == '3' else None)
    if (root / 'RPG_RT.ldb').is_file() and (root / 'RPG_RT.lmt').is_file():
        return found('rpgmaker_2000_2003', ['RPG_RT.ldb', 'RPG_RT.lmt'])
    data_dirs = [entry for entry in root.iterdir() if entry.is_dir() and entry.name.endswith('_Data')]
    for data in data_dirs:
        metadata = data / 'il2cpp_data/Metadata/global-metadata.dat'
        assembly = data / 'Managed/Assembly-CSharp.dll'
        if (root / 'GameAssembly.dll').is_file() and metadata.is_file():
            return found('unity_il2cpp', ['GameAssembly.dll', str(metadata.relative_to(root))])
        if assembly.is_file():
            return found('unity_mono', [str(assembly.relative_to(root))])
    if data_dirs and (root / 'UnityPlayer.dll').is_file():
        return found('unity_unknown_backend', ['UnityPlayer.dll', data_dirs[0].name])
    for marker in ('Data.wolf', 'Data/BasicData/Game.dat'):
        if (root / marker).is_file():
            return found('wolf_candidate', [marker], 'wolf')
    bytecode = root / 'start.ax'
    if bytecode.is_file():
        with bytecode.open('rb') as stream:
            if stream.read(4) == b'HSP3':
                return found('hsp3', ['start.ax:HSP3'], 'hsp')
    emulator = root / 'duckstation-qt-x64-ReleaseLTCG.exe'
    if emulator.is_file() and (root / 'SDL3.dll').is_file():
        with emulator.open('rb') as stream:
            if stream.read(2) == b'MZ':
                return found('duckstation_windows', [emulator.name + ':MZ', 'SDL3.dll'], 'duckstation')
    if (root / 'Runtime.rts').is_file():
        image = root / 'game.exe'
        if image.is_file() and (root / 'data.dts').is_file():
            with image.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            if digest == _NATIVE_SAMPLE['exe_sha256']:
                return {**found('srpgstudio_candidate', ['Runtime.rts', 'data.dts', 'game.exe:sha256=' + digest],
                                'native_input'), 'profile_id': _NATIVE_SAMPLE['id']}
        return found('srpgstudio_candidate', ['Runtime.rts'])
    return result


def scan_games(roots, *, max_depth=4, max_dirs=500):
    if type(max_depth) is not int or not 0 <= max_depth <= 10:
        raise ValueError('max_depth 必须在 0..10 之间')
    if type(max_dirs) is not int or not 1 <= max_dirs <= 10000:
        raise ValueError('max_dirs 必须在 1..10000 之间')
    queue = deque((Path(root), 0) for root in roots)
    result = {'games': [], 'errors': [], 'visited': 0, 'truncated': False}
    seen = set()
    while queue and result['visited'] < max_dirs:
        root, depth = queue.popleft()
        canonical = str(root.resolve()).casefold()
        if canonical in seen:
            continue
        seen.add(canonical)
        result['visited'] += 1
        try:
            game = inspect_game(root)
            if game['engine'] != 'unknown':
                result['games'].append(game)
                continue
            if depth < max_depth:
                for entry in root.iterdir():
                    if (entry.is_dir() and not entry.is_symlink()
                            and not entry.name.startswith('.')
                            and entry.name.casefold() not in _RESOURCE_DIRS):
                        queue.append((entry, depth + 1))
        except OSError as exc:
            result['errors'].append({'root': str(root), 'error': str(exc)})
    result['truncated'] = bool(queue)
    return result
