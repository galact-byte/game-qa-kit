"""只读引擎证据与有界样本发现；识别不代表后台控制兼容。"""
from __future__ import annotations

from collections import deque
import hashlib
import json
from pathlib import Path
import re
import struct

_NATIVE_SAMPLE = json.loads((Path(__file__).resolve().parents[1] /
                            'plugins/native_input/samples/srpgstudio-x86-fixed.json').read_text(encoding='utf-8'))

# 资源树既不含入口，也不应随游戏体量放大盘点成本。
_RESOURCE_DIRS = {'audio', 'graphics', 'fonts', 'system', 'save', 'saves', 'savedata',
                  'img', 'images', 'movies', 'video', 'node_modules', 'managed',
                  'il2cpp_data', 'data', 'patch', 'resources', 'library', 'models',
                  'venv', '.venv', 'site-packages', 'custom_nodes', 'bepinex'}


_TYRANO_SCRIPTS = ('tyrano/tyrano.js', 'tyrano/plugins/kag/kag.js')


def _tyrano_asar_markers(archive):
    """只读有界 ASAR 索引；不解包、执行脚本或跟随归档链接。"""
    with archive.open('rb') as stream:
        prefix = stream.read(16)
        if len(prefix) != 16:
            return []
        size_pickle, header_size, payload_size, json_size = struct.unpack('<4I', prefix)
        file_size = archive.stat().st_size
        if (size_pickle != 4 or not 0 < json_size <= 16 * 1024 * 1024
                or payload_size != 4 + json_size + (-json_size % 4)
                or header_size != payload_size + 4 or 8 + header_size > file_size):
            return []
        try:
            tree = json.loads(stream.read(json_size))
            for marker in _TYRANO_SCRIPTS:
                node = tree
                for name in marker.split('/'):
                    node = node['files'][name]
                offset, size = node.get('offset'), node.get('size')
                if (not isinstance(offset, str) or not re.fullmatch('[0-9]{1,20}', offset)
                        or type(size) is not int or size <= 0
                        or any(key in node for key in ('link', 'files', 'unpacked'))
                        or int(offset) + size > file_size - 8 - header_size):
                    return []
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
            return []
    return [f'resources/app.asar:{marker}' for marker in _TYRANO_SCRIPTS]


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
    if (root / 'CSV/FLAG.csv').is_file() and (root / 'ERB').is_dir():
        for image in root.iterdir():
            name = image.name.casefold()
            if name.startswith('emuera') and name.endswith('.exe') and image.is_file():
                with image.open('rb') as stream:
                    if stream.read(2) == b'MZ':
                        return found('emuera_candidate', ['CSV/FLAG.csv', 'ERB/', image.name + ':MZ'])
    if (root / 'renpy/__init__.py').is_file() and (root / 'renpy/bootstrap.py').is_file():
        return found('renpy', ['renpy/__init__.py', 'renpy/bootstrap.py'])
    if (root / 'gameqa-renpy-fixture.rpy').is_file():
        return found('renpy_authored_candidate', ['gameqa-renpy-fixture.rpy'])
    if (root / 'gameqa-tyrano-fixture.ks').is_file():
        return found('tyrano_authored_candidate', ['gameqa-tyrano-fixture.ks'])
    if (root / 'gameqa-emuera-fixture.erb').is_file():
        return found('emuera_authored_candidate', ['gameqa-emuera-fixture.erb'])
    for prefix in ('', 'resources/app/', 'resources/app/src/'):
        markers = [prefix + marker for marker in _TYRANO_SCRIPTS]
        if all((root / marker).is_file() for marker in markers):
            return found('tyranoscript_candidate', markers)
    archive = root / 'resources/app.asar'
    if archive.is_file():
        markers = _tyrano_asar_markers(archive)
        if markers:
            return found('tyranoscript_candidate', markers)
    archives = []
    for pack in root.iterdir():
        if pack.is_file() and pack.suffix.casefold() == '.xp3':
            with pack.open('rb') as stream:
                header = stream.read(19)
            if len(header) == 19 and header[:11] == b'XP3\r\n \n\x1a\x8bg\x01':
                archives.append(pack.name + ':XP3')
    if archives:
        for image in root.iterdir():
            if image.is_file() and image.suffix.casefold() == '.exe':
                with image.open('rb') as stream:
                    header = stream.read(65536)
                if header[:2] == b'MZ' and b'@TVP(kirikiri) for win32' in header:
                    return found('kirikiri_candidate', [sorted(archives)[0], image.name + ':MZ+TVP(kirikiri)'])
    for pack in root.glob('*.pck'):
        if not pack.is_file():
            continue
        with pack.open('rb') as stream:
            header = stream.read(96)
        if len(header) == 96:
            magic, pack_version, major, _minor, _patch = struct.unpack('<4s4I', header[:20])
            if magic == b'GDPC' and pack_version in (2, 3, 4) and major == 4:
                return found('godot4', [pack.name + ':GDPC'])
    project = root / 'project.godot'
    if project.is_file() and project.stat().st_size <= 1048576:
        text = project.read_text(encoding='utf-8-sig', errors='replace')
        if re.search(r'(?m)^\s*config_version\s*=\s*5\s*$', text):
            return found('godot4_candidate', ['project.godot:config_version=5'])
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
