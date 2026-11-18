"""使用本机 .NET Framework 编译器构建固定 Emuera STA 启动器。"""
import argparse
import os
from pathlib import Path
import subprocess


def compiler_path():
    return Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Microsoft.NET/Framework/v4.0.30319/csc.exe'


def build(output, *, testing=False, source=None):
    compiler = compiler_path()
    if not compiler.is_file():
        raise RuntimeError('缺少本机 .NET Framework 4 编译器；不自动安装')
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    args = [str(compiler), '/nologo', '/noconfig', '/target:winexe', '/platform:x86', '/codepage:65001',
            '/reference:System.dll', '/reference:System.Core.dll',
            '/reference:System.Windows.Forms.dll', '/reference:System.Web.Extensions.dll',
            '/reference:System.Drawing.dll', '/out:' + str(output)]
    if testing:
        args.append('/define:GAMEQA_TESTING')
    args.append(str(source or Path(__file__).with_name('Launcher.cs')))
    result = subprocess.run(args, capture_output=True, timeout=15, cwd=output.parent)
    if result.returncode:
        diagnostic = (result.stdout + result.stderr).decode('mbcs', errors='replace')
        raise RuntimeError('C# 编译失败：\n' + diagnostic)
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--testing', action='store_true', help='仅测试夹具启用故障注入')
    options = parser.parse_args()
    build(options.output, testing=options.testing)
