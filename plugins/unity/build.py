"""使用现有 Windows C# 编译器及本地游戏引用构建插件，不安装或下载依赖。"""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def compiler() -> str:
    found = shutil.which('csc')
    if found:
        return found
    for base in ('Framework64', 'Framework'):
        path = Path('C:/Windows/Microsoft.NET') / base / 'v4.0.30319/csc.exe'
        if path.is_file():
            return str(path)
    raise RuntimeError('未找到本地 C# 编译器；本命令不会自动安装依赖')


def compile_sources(output: Path, sources: list[Path], references: list[Path] = ()) -> None:
    output = output.resolve()
    for path in [*sources, *references]:
        if not path.is_file():
            raise FileNotFoundError(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    arguments = [compiler(), '/nologo', '/unsafe+', '/warn:4', '/warnaserror+', '/optimize+',
                 '/target:' + ('exe' if output.suffix == '.exe' else 'library'), '/out:' + str(output)]
    if any(path.name == 'mscorlib.dll' for path in references):
        arguments.extend(['/noconfig', '/nostdlib+'])
    arguments.extend('/reference:' + str(path.resolve()) for path in references)
    arguments.extend(str(path.resolve()) for path in sources)
    result = subprocess.run(arguments, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(result.stdout.decode('utf-8', errors='replace'))
    if result.stdout:
        print(result.stdout.decode('utf-8', errors='replace').strip())


def references(game: Path, loader: Path | None = None) -> list[Path]:
    managed = list(game.glob('*_Data/Managed'))
    if len(managed) != 1:
        raise ValueError('必须提供含唯一 *_Data/Managed 的 Unity Mono 游戏目录')
    files = ['mscorlib.dll', 'System.dll', 'System.Core.dll',
             'UnityEngine.dll', 'UnityEngine.CoreModule.dll', 'UnityEngine.InputLegacyModule.dll',
             'UnityEngine.ImageConversionModule.dll', 'UnityEngine.UI.dll',
             'UnityEngine.TextRenderingModule.dll', 'UnityEngine.UIModule.dll', 'netstandard.dll']
    return [managed[0] / name for name in files if (managed[0] / name).is_file()] + [(loader or game) / 'BepInEx/core' / name
            for name in ['BepInEx.dll', '0Harmony.dll', 'Mono.Cecil.dll']]


def compile_il2cpp(game: Path, output: Path) -> None:
    core, interop, runtime = game / 'BepInEx/core', game / 'BepInEx/interop', game / 'dotnet'
    required = [core / name for name in ('BepInEx.Core.dll', 'BepInEx.Unity.IL2CPP.dll',
                                        'Il2CppInterop.Runtime.dll', 'Il2CppInterop.Common.dll', '0Harmony.dll')]
    required += [runtime / 'System.Private.CoreLib.dll', interop / 'Unity.InputSystem.dll', interop / 'UnityEngine.CoreModule.dll']
    for path in required:
        if not path.is_file():
            raise FileNotFoundError(path)
    dotnet = shutil.which('dotnet')
    if not dotnet:
        raise FileNotFoundError('找不到已安装的 dotnet SDK；不会自动安装')
    sdks = subprocess.check_output([dotnet, '--list-sdks'], text=True, encoding='utf-8')
    import re
    compilers = [Path(root) / version / 'Roslyn/bincore/csc.dll'
                 for version, root in re.findall(r'([^\s]+)\s+\[([^\]]+)\]', sdks)]
    if not compilers:
        raise FileNotFoundError('找不到现有 Roslyn 编译器')
    refs = [p for p in runtime.glob('*.dll') if (p.name.startswith('System.') and not p.name.endswith('.Native.dll')) or p.name in ('netstandard.dll', 'mscorlib.dll', 'Microsoft.CSharp.dll')]
    refs += required[:5] + [core / 'BepInEx.Unity.Common.dll']
    refs += list(interop.glob('UnityEngine*.dll')) + list(interop.glob('Il2Cpp*.dll'))
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    def compile_one(target, sources, extra=()):
        command = [dotnet, str(compilers[-1]), '/nologo', '/utf8output', '/unsafe+', '/target:library', '/nostdlib+',
                   '/define:IL2CPP', '/warnaserror+', '/out:' + str(target)]
        command += ['/reference:' + str(p) for p in refs + list(extra)]
        result = subprocess.run(command + [str(p) for p in sources], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if result.returncode:
            raise RuntimeError(result.stdout.decode('utf-8', errors='replace'))
    compile_one(output, [ROOT / name for name in ('ControlCore.cs', 'DesktopGuard.cs', 'InputBackend.cs', 'PhaseDiagnostics.cs', 'CapturePublisher.cs', 'GameQaBridge.cs', 'Il2CppEntry.cs')])
    compile_one(output.parent / 'GameQaInputSystem.dll', [ROOT / 'backends/InputSystemBackend.cs'], [output, interop / 'Unity.InputSystem.dll'])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-root', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--test-core', action='store_true')
    parser.add_argument('--input-system', action='store_true')
    parser.add_argument('--il2cpp', action='store_true')
    parser.add_argument('--rewired', action='store_true')
    parser.add_argument('--loader', type=Path, help='使用现有 BepInEx 5 目录中的编译引用')
    args = parser.parse_args()
    if args.test_core:
        compile_sources(args.output, [ROOT / 'ControlCore.cs', ROOT / 'DesktopGuard.cs', ROOT.parents[1] / 'tests/unity/ControlCoreTests.cs'])
        subprocess.run([str(args.output.resolve())], check=True)
        mapped = args.output.with_name(args.output.stem + '-mapped.exe')
        compile_sources(mapped, [ROOT / 'ControlCore.cs', ROOT / 'ActionState.cs',
                                ROOT.parents[1] / 'tests/unity/RewiredStateTests.cs'])
        subprocess.run([str(mapped.resolve())], check=True)
    else:
        if args.game_root is None:
            parser.error('构建插件需要 --game-root，仅读取其本地引用程序集')
        if args.il2cpp:
            compile_il2cpp(args.game_root, args.output)
        else:
            refs = references(args.game_root, args.loader)
            compile_sources(args.output, sorted(ROOT.glob('*.cs')), refs)
            managed = next(args.game_root.glob('*_Data/Managed'))
            if args.input_system:
                compile_sources(args.output.parent / 'GameQaInputSystem.dll', [ROOT / 'backends/InputSystemBackend.cs'],
                                refs + [args.output, managed / 'Unity.InputSystem.dll'])
            if args.rewired:
                compile_sources(args.output.parent / 'GameQaRewired.dll', [ROOT / 'backends/RewiredBackend.cs'],
                                refs + [args.output, managed / 'Rewired_Core.dll'])
        print(args.output.resolve())


if __name__ == '__main__':
    main()
