"""便携程序引导，不检查或自动安装 Python 依赖。"""
import multiprocessing
import os
from pathlib import Path
import sys
import traceback


def main():
    multiprocessing.freeze_support()
    for name in ('stdout', 'stderr'):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, 'w', encoding='utf-8'))
    args = sys.argv[1:]
    if Path(sys.executable).stem.lower() == 'gameqa-mcp' and not args:
        args = ['--mcp']
    from gameqakit.application import main as application
    try:
        return application(args)
    except Exception:
        if not args:
            import ctypes
            log = Path(sys.executable).resolve().parent / 'startup-error.log'
            try:
                log.write_text(traceback.format_exc(), encoding='utf-8')
                message = '启动失败，请查看：' + str(log)
            except OSError:
                message = traceback.format_exc()
            ctypes.windll.user32.MessageBoxW(None, message, 'Game QA Kit', 0x10)
        else:
            traceback.print_exc()
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
