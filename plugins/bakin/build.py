"""Build self-authored Bakin components using installed tools; never download dependencies."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

from gameqakit.admission import no_links
from .prepare import BUILD

HERE = Path(__file__).resolve().parent
CSC = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
OUTPUTS = ('GameQaKit.Bakin.PreparedLaunch.dll', 'GameQaKit.Bakin.RuntimeBridge.dll', 'GameQaKit.Bakin.PatchTool.exe', 'Mono.Cecil.dll')


def build(game, cecil, output):
    game, cecil, output = map(Path, (game, cecil, output))
    for path in (game, cecil, output): no_links(path)
    if not CSC.is_file() or not cecil.is_file():
        raise ValueError('Installed .NET compiler and existing Mono.Cecil assembly are required')
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError('Build output must be empty')
    shutil.copyfile(cecil, output / 'Mono.Cecil.dll')
    def compile_component(name, sources, references):
        command=[str(CSC), '/nologo', '/optimize+', '/target:' + ('exe' if name.endswith('.exe') else 'library'),
                 '/out:' + str(output/name), *['/r:' + str(ref) for ref in references],
                 *[str(HERE/source) for source in sources]]
        result=subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)
        if result.returncode: raise RuntimeError(result.stdout + result.stderr)
    compile_component(OUTPUTS[0], ['PreparedLaunch.cs'], ['System.Web.Extensions.dll'])
    compile_component(OUTPUTS[1], ['InputState.cs','ChannelState.cs','RuntimeBridge.cs'],
                      ['System.Drawing.dll','System.Web.Extensions.dll',game/'data/bakinengine.dll',game/'data/common.dll'])
    compile_component(OUTPUTS[2], ['PatchTool.cs'], [cecil])
    manifest={name:hashlib.sha256((output/name).read_bytes()).hexdigest() for name in OUTPUTS}
    (output/'artifact-digests.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    return manifest


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--game',required=True);parser.add_argument('--cecil',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args()
    print(json.dumps(build(args.game,args.cecil,args.output),indent=2))


if __name__ == '__main__': main()
