"""Validate the registered Emuera runtime and authored scene."""
from pathlib import Path

from gameqakit.admission import file_digest, no_links
from .build import compiler_path
from .host import RUNTIME_SHA256

SCRIPT = 'gameqa-emuera-fixture.erb'
SCRIPT_SHA256 = 'd1beff0ab344e407f9f66a4d66ce1e97c5ec452b082624c1fb19a3eacc5bfef8'
RUNTIME = 'Emuera.exe'
LAUNCHER = 'GameQaEmuera.exe'
LAUNCHER_SOURCE = Path(__file__).with_name('Launcher.cs')
GAMEBASE = ('タイトル,GameQA Emuera Bridge\nウィンドウタイトル,GameQA Emuera Bridge\n'
            'コード,731516\nバージョン,1000\n').encode('utf-8-sig')


def source_digests(source):
    root = Path(source)
    no_links(root / SCRIPT)
    if {p.name for p in root.iterdir()} != {SCRIPT} or file_digest(root / SCRIPT) != SCRIPT_SHA256:
        raise ValueError('仅接受未经修改的 Emuera 自写场景目录')
    return {SCRIPT: SCRIPT_SHA256}


def artifact_digests(artifacts):
    runtime = Path(artifacts) / RUNTIME
    no_links(runtime)
    if file_digest(runtime) != RUNTIME_SHA256:
        raise ValueError('运行时不匹配已登记的 Emuera 1.824 私家构建')
    no_links(LAUNCHER_SOURCE)
    return {RUNTIME: RUNTIME_SHA256, LAUNCHER_SOURCE.name: file_digest(LAUNCHER_SOURCE)}


def inspect(source, artifacts):
    blockers = []
    for field, verify, path, recovery in (
        ('source', source_digests, source, '选择只含仓库自写场景的目录。'),
        ('artifacts', artifact_digests, artifacts, '选择含已登记 Emuera.exe 的本机组件目录。'),
    ):
        try:
            if not path:
                raise ValueError('尚未选择所需目录')
            verify(path)
        except (OSError, ValueError) as exc:
            blockers.append(dict(code=field + '_not_ready', message=str(exc), field=field, recovery=recovery))
    if not compiler_path().is_file():
        blockers.append(dict(code='compiler_missing', message='缺少 Windows .NET Framework 4 编译器',
                             field='artifacts', recovery='使用已提供 .NET Framework 4 编译器的 Windows 环境。'))
    return blockers
