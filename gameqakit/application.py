"""源码与便携版共用入口；显式分派被冻结程序的后台子进程。"""
import json
from pathlib import Path
import runpy
import sys

WORKER_MODULES = {'gameqakit.sessions', 'gameqakit.game_worker', 'gameqakit.mcp_server',
                  'gameqakit.cli', 'gameqakit.x11_helper', 'gameqakit.capture_worker',
                  'gameqakit.macos_process', 'plugins.kirikiri.capture_worker'}


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        from .gui import main as gui_main
        return gui_main()
    if args[0] == '--worker-self-test' and len(args) == 2:
        Path(args[1]).write_text(json.dumps({'frozen': bool(getattr(sys, 'frozen', False))}), encoding='utf-8')
        return 0
    if args[0] == '--smoke-test' and len(args) == 2:
        from .startup_check import check
        check(Path(args[1]))
        return 0
    if args[0] == '--self-test' and len(args) == 2:
        from .packaging_check import check
        check(Path(args[1]))
        return 0
    if args == ['--mcp']:
        args = ['-m', 'gameqakit.mcp_server']
    if len(args) < 2 or args[0] != '-m' or args[1] not in WORKER_MODULES:
        raise ValueError('仅支持 GUI、--mcp、--self-test 或已登记的 -m 后台模块')
    original = sys.argv
    try:
        sys.argv = [args[1], *args[2:]]
        runpy.run_module(args[1], run_name='__main__', alter_sys=True)
    finally:
        sys.argv = original
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
