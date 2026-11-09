"""为已核验的 DuckStation 构建建立独立配置与空白记忆卡目录。"""
import argparse
import configparser
import hashlib
import json
from pathlib import Path
import shutil

EXE = 'duckstation-qt-x64-ReleaseLTCG.exe'
SUPPORTED_SHA256 = '2ea09675202a6758992571a57971b1cd57fd6b0dfde4b808624698bf52efdd57'


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def validate_image(executable):
    path = Path(executable).resolve(strict=True)
    if digest(path) != SUPPORTED_SHA256:
        raise ValueError('DuckStation 版本未核验，不能使用当前原生偏移')
    return path


def prepare(source, workspace, game):
    source = Path(source).resolve(strict=True)
    game = Path(game).resolve(strict=True)
    workspace = Path(workspace).resolve()
    if any(workspace.is_relative_to(p) or p.is_relative_to(workspace) for p in (source, game.parent)):
        raise ValueError('工作区不得与原模拟器或游戏目录重叠')
    if workspace.exists():
        raise FileExistsError('不能覆盖已有测试工作区')
    if not source.is_dir() or not game.is_file() or game.suffix.casefold() != '.cue':
        raise ValueError('需要模拟器目录与 CUE 镜像')
    validate_image(source / EXE)
    folders = ('bios', 'resources', 'QtPlugins', 'translations')
    for name in folders:
        if not (source / name).is_dir():
            raise FileNotFoundError(source / name)
    runtime = workspace / 'runtime'
    runtime.mkdir(parents=True)
    for item in source.iterdir():
        if item.is_file() and (item.suffix.casefold() in ('.dll', '.conf') or item.name == EXE):
            shutil.copy2(item, runtime / item.name)
    for name in folders:
        shutil.copytree(source / name, runtime / name)
    (runtime / 'portable.txt').touch()
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.optionxform = str
    cfg.read_dict({
        'Main': {'PauseOnFocusLoss': 'false', 'PauseOnControllerDisconnection': 'false',
                 'SaveStateOnExit': 'false', 'StartFullscreen': 'false', 'EnableDiscordPresence': 'false',
                 'SetupWizardIncomplete': 'false'},
        'AutoUpdater': {'CheckAtStartup': 'false'}, 'Cheevos': {'Enabled': 'false'},
        'UI': {'HideMainWindowWhenRunning': 'true'}, 'Audio': {'Backend': 'Null', 'OutputMuted': 'true'},
        'InputSources': {'SDL': 'true', 'XInput': 'false'},
        'MemoryCards': {'Card1Type': 'Shared', 'Card2Type': 'None', 'Card1Path': str(runtime / 'memcards/test.mcd')},
        'Logging': {'LogToFile': 'true', 'LogLevel': 'Info', 'LogToConsole': 'false'},
        'Hotkeys': {'Screenshot': 'SDL-15/Misc1'},
        'Folders': {key: str(runtime / name) for key, name in {
            'BIOS': 'bios', 'MemoryCards': 'memcards', 'SaveStates': 'savestates', 'Screenshots': 'screenshots',
            'Cache': 'cache', 'Cheats': 'cheats', 'Covers': 'covers', 'GameSettings': 'gamesettings',
            'InputProfiles': 'inputprofiles', 'Textures': 'textures', 'Videos': 'videos', 'Shaders': 'shaders'}.items()},
        'Pad1': {'Type': 'DigitalController', **{key: 'SDL-15/' + value for key, value in {
            'Cross': 'A', 'Circle': 'B', 'Square': 'X', 'Triangle': 'Y', 'Select': 'Back', 'Start': 'Start',
            'Up': 'DPadUp', 'Down': 'DPadDown', 'Left': 'DPadLeft', 'Right': 'DPadRight',
            'L1': 'LeftShoulder', 'R1': 'RightShoulder'}.items()}},
        **{'Pad' + str(index): {'Type': 'None'} for index in range(2, 9)},
    })
    with (runtime / 'settings.ini').open('w', encoding='utf-8') as stream:
        cfg.write(stream)
    record = {'source': str(source), 'runtime': str(runtime), 'game': str(game),
              'exe_sha256': digest(runtime / EXE), 'copied_user_saves': False}
    (workspace / 'preparation.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('source', 'workspace', 'game'):
        parser.add_argument('--' + key, type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.workspace, args.game), ensure_ascii=False, indent=2))
