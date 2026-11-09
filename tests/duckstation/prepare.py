"""Create a portable emulator probe without copying user saves."""
from pathlib import Path
import argparse
import configparser
import hashlib
import json
import shutil

ROOT = SOURCE = GAME = RUNTIME = None


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def baseline():
    files = [SOURCE / 'settings.ini', SOURCE / 'portable.txt']
    for folder in ('memcards', 'savestates', 'bios', 'gamesettings'):
        files.extend(p for p in (SOURCE / folder).rglob('*') if p.is_file())
    files.extend(p for p in GAME.iterdir() if p.suffix.lower() in ('.bin', '.cue', '.bat'))
    return {str(p): {'size': p.stat().st_size, 'sha256': digest(p)} for p in files}


def main():
    global ROOT, SOURCE, GAME, RUNTIME
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--game-dir', type=Path, required=True)
    args = parser.parse_args()
    SOURCE, GAME, ROOT = args.source.resolve(strict=True), args.game_dir.resolve(strict=True), args.workspace.resolve()
    if any(ROOT.is_relative_to(path) or path.is_relative_to(ROOT) for path in (SOURCE, GAME)):
        parser.error('Workspace must be separate from source emulator and game data')
    ROOT.mkdir(parents=True, exist_ok=True)
    RUNTIME = ROOT / 'runtime'
    if RUNTIME.exists():
        raise RuntimeError('Probe runtime already exists; do not overwrite')
    previous = ROOT / 'source-baseline.json'
    if previous.exists():
        raise RuntimeError('Baseline already exists; retain the previous record')
    previous.write_text(json.dumps(baseline(), indent=2, ensure_ascii=False), encoding='utf-8')
    RUNTIME.mkdir()
    for path in SOURCE.iterdir():
        if path.is_file() and (path.suffix.lower() in ('.dll', '.conf') or path.name == 'duckstation-qt-x64-ReleaseLTCG.exe'):
            shutil.copy2(path, RUNTIME / path.name)
    for folder in ('bios', 'resources', 'QtPlugins', 'translations'):
        shutil.copytree(SOURCE / folder, RUNTIME / folder)
    (RUNTIME / 'portable.txt').touch()
    config = configparser.ConfigParser(interpolation=None)
    config.optionxform = str
    config.read(SOURCE / 'settings.ini', encoding='utf-8-sig')
    changes = {
        'Main': {'PauseOnFocusLoss': 'false', 'PauseOnControllerDisconnection': 'false',
                 'SaveStateOnExit': 'false', 'StartFullscreen': 'false', 'StartPaused': 'false',
                 'EnableDiscordPresence': 'false'},
        'AutoUpdater': {'CheckAtStartup': 'false'},
        'Cheevos': {'Enabled': 'false'},
        'UI': {'HideMainWindowWhenRunning': 'true'},
        'Audio': {'Backend': 'Null', 'OutputMuted': 'true'},
        'MemoryCards': {'Card1Type': 'Shared', 'Card2Type': 'None',
                        'Card1Path': str(RUNTIME / 'memcards/probe.mcd')},
        'Folders': {k: str(RUNTIME / v) for k, v in {
            'BIOS': 'bios', 'MemoryCards': 'memcards', 'SaveStates': 'savestates', 'Screenshots': 'screenshots',
            'Cache': 'cache', 'Cheats': 'cheats', 'Covers': 'covers', 'GameSettings': 'gamesettings',
            'InputProfiles': 'inputprofiles', 'Textures': 'textures', 'Videos': 'videos', 'Shaders': 'shaders'}.items()},
        'InputSources': {'SDL': 'true', 'XInput': 'false'},
        'Logging': {'LogToFile': 'true', 'LogLevel': 'Info', 'LogToConsole': 'false'},
    }
    for section, values in changes.items():
        if not config.has_section(section):
            config.add_section(section)
        for key, value in values.items():
            config.set(section, key, value)
    for section in ['Pad' + str(i) for i in range(1, 9)] + ['Hotkeys']:
        if config.has_section(section):
            config.remove_section(section)
        config.add_section(section)
    bindings = {'Cross': 'A', 'Circle': 'B', 'Square': 'X', 'Triangle': 'Y', 'Select': 'Back',
                'Start': 'Start', 'Up': 'DPadUp', 'Down': 'DPadDown', 'Left': 'DPadLeft', 'Right': 'DPadRight',
                'L1': 'LeftShoulder', 'R1': 'RightShoulder'}
    config.set('Pad1', 'Type', 'DigitalController')
    for key, value in bindings.items():
        config.set('Pad1', key, 'SDL-15/' + value)
    for key, value in {'Screenshot': 'Misc1', 'TogglePause': 'RightPaddle1', 'FrameStep': 'LeftPaddle1'}.items():
        config.set('Hotkeys', key, 'SDL-15/' + value)
    with (RUNTIME / 'settings.ini').open('w', encoding='utf-8') as out:
        config.write(out)
    print(json.dumps({'runtime': str(RUNTIME), 'baseline_files': len(json.loads(previous.read_text(encoding='utf-8'))),
                      'copied_bytes': sum(p.stat().st_size for p in RUNTIME.rglob('*') if p.is_file()),
                      'copied_user_saves': False}, indent=2))


if __name__ == '__main__':
    main()
