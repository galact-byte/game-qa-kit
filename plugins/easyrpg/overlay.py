"""Reproducible overlay for the two approved upstream source commits."""
import hashlib
import json
from pathlib import Path
import shutil

PLAYER_COMMIT = 'f8e41f43b619413f95847536412b56f85307d378'
LCF_COMMIT = '9c09529e3de0a4106470082ef48a00d4c3593c33'


def apply(player):
    player = Path(player)
    overlay = Path(__file__).resolve().parent
    for name in ('bridge_state.h', 'gameqa_input.h', 'gameqa_input.cpp'):
        shutil.copyfile(overlay / name, player / 'src' / name)
    for name, before, after in (
        ('src/input_source.cpp', '#include "input_source.h"', '#include "input_source.h"\n#include "gameqa_input.h"\n#include <cstdlib>'),
        ('src/input_source.cpp', '\tif (!replay_from_path.empty()) {',
         '\tif (std::getenv("GAMEQAKIT_EASYRPG_MAPPING")) {\n\t\treturn GameQa::CreateManaged(cfg, std::move(directions));\n\t}\n\tif (!replay_from_path.empty()) {'),
        ('src/player.cpp', '#include "input.h"', '#include "input.h"\n#include "gameqa_input.h"'),
        ('src/player.cpp', '#ifdef __ANDROID__\n\tEpAndroid::invoke();',
         'GameQa::AfterScene();\n\n#ifdef __ANDROID__\n\tEpAndroid::invoke();'),
        ('src/options.h', '#define PAUSE_GAME_WHEN_FOCUS_LOST 1', '#define PAUSE_GAME_WHEN_FOCUS_LOST 0'),
        ('src/options.h', '#define PAUSE_AUDIO_WHEN_FOCUS_LOST 1', '#define PAUSE_AUDIO_WHEN_FOCUS_LOST 0'),
        ('CMakeLists.txt', '\tsrc/input_source.cpp', '\tsrc/gameqa_input.cpp\n\tsrc/input_source.cpp'),
    ):
        if before == after:
            continue
        path = player / name
        text = path.read_text(encoding='utf-8')
        if text.count(before) != 1:
            raise ValueError('Unexpected upstream source, refusing patch: ' + name)
        path.write_text(text.replace(before, after),encoding='utf-8',newline='\n')
    # Keep the product executable separate from installed/upstream Player binaries.
    path = player / 'CMakeLists.txt'
    with path.open('a',encoding='utf-8',newline='\n') as output:
        output.write('\nset_target_properties(${EXE_NAME} PROPERTIES OUTPUT_NAME GameQaEasyRpg)\n')
    manifest = {'player_commit': PLAYER_COMMIT, 'liblcf_commit': LCF_COMMIT,
                'overlay_sha256': {name:hashlib.sha256((overlay/name).read_bytes()).hexdigest()
                                   for name in ('bridge_state.h','gameqa_input.h','gameqa_input.cpp','overlay.py')}}
    return manifest


if __name__ == '__main__':
    import sys
    print(json.dumps(apply(sys.argv[1]),indent=2))
