"""Build only the registered authored YYC scene from an existing official generated tree."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
NAME = 'gqk_gamemaker_probe_20261119'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(generated, data, output, vsdevcmd):
    manifest = json.loads((HERE / 'source-manifest.json').read_text(encoding='utf-8'))
    generated, data, output, vsdevcmd = (Path(p).absolute() for p in (generated, data, output, vsdevcmd))
    expected = manifest['generated_source_sha256']
    if {name: digest(generated / name) for name in expected} != expected:
        raise ValueError('Only the registered authored generated source is accepted')
    if digest(data) != manifest['data_sha256']:
        raise ValueError('Unregistered authored data.win')
    for path in (generated, data, output, vsdevcmd):
        if any(ch in str(path) for ch in '\r\n"%&|<>^'):
            raise ValueError('Unsupported shell metacharacter in build path')
    output.mkdir(parents=True, exist_ok=False)
    work = output / 'generated'
    work.mkdir()
    for name in expected:
        shutil.copy2(generated / name, work / name)
    solution = work / 'Sln'
    solution.mkdir()
    for path in (generated / 'Sln').iterdir():
        if path.is_file() and path.suffix in ('.ico', '.rc', '.h'):
            shutil.copy2(path, solution / path.name)
        elif path.is_file() and path.suffix in ('.sln', '.vcxproj', '.filters', '.user'):
            text = path.read_text(encoding='utf-8-sig')
            text = text.replace(str(generated), str(work)).replace(str(generated).replace('/', '\\'),
                                                                      str(work).replace('/', '\\'))
            (solution / path.name).write_text(text, encoding='utf-8')
    for name in ('Bridge.h', 'BridgeState.h'):
        shutil.copy2(HERE / name, work / name)
    step = work / 'gml_Object_obj_probe_Step_0.gml.cpp'
    text = step.read_text(encoding='utf-8')
    anchor = 'YY_STACKTRACE_FUNC_ENTRY( "gml_Object_obj_probe_Step_0", 0 );'
    ending = ';\n}\n#endif'
    if text.count(anchor) != 1 or text.count(ending) != 1 or text.count('#include "pch.hpp"') != 1:
        raise ValueError('Unregistered Step layout')
    text = text.replace('#include "pch.hpp"', '#include "pch.hpp"\n#include "Bridge.h"')
    text = text.replace(anchor, anchor + '\ngameqa_gm::bridge().before();')
    text = text.replace(ending, ';\ngameqa_gm::bridge().after(BOOL_RValue(*sWself_probe_right));\n}\n#endif')
    step.write_text(text, encoding='utf-8')
    command = output / 'build.cmd'
    command.write_text('@echo off\ncall "' + str(vsdevcmd) + '" -host_arch=amd64 -arch=amd64\n'
                       'if errorlevel 1 exit /b %errorlevel%\nmsbuild "' + str(solution / (NAME + '.sln')) + '" '
                       '/m:2 /p:Configuration=Release /p:Platform=x64 /p:PlatformToolset=v143 /t:build /verbosity:minimal\n'
                       'exit /b %errorlevel%\n', encoding='utf-8')
    with (output / 'build.log').open('wb') as log:
        result = subprocess.run(['cmd.exe', '/d', '/c', str(command)], stdout=log, stderr=subprocess.STDOUT,
                                cwd=output, timeout=180)
    if result.returncode:
        raise RuntimeError('YYC build failed; see ' + str(output / 'build.log'))
    artifacts = output / 'artifacts'
    artifacts.mkdir()
    shutil.copy2(solution / 'x64/Release' / (NAME + '.exe'), artifacts / 'gameqa-gamemaker.exe')
    shutil.copy2(data, artifacts / 'data.win')
    record = {'artifact_sha256': {p.name: digest(p) for p in artifacts.iterdir()},
              'bridge_sha256': {name: digest(HERE / name) for name in ('Bridge.h', 'BridgeState.h')},
              'original_source_unchanged': {name: digest(generated / name) for name in expected} == expected,
              'generated_copy': str(work), 'scope': 'registered authored YYC scene only'}
    (output / 'build-result.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(record, indent=2))
    return artifacts


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('generated', 'data', 'output', 'vsdevcmd'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    build(args.generated, args.data, args.output, args.vsdevcmd)
