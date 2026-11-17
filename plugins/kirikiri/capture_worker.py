"""固定 KiriKiri 的隔离桌面 GDI 取图；期限和回收仍由共用宿主监督。"""
from functools import partial
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from gameqakit.capture_worker import run
from gameqakit.win32 import capture


if __name__ == '__main__':
    # PrintWindow 会触发延后的重绘，可能只返回灰底；读取隔离桌面的实际画布。
    raise SystemExit(run(Path(sys.argv[1]), sys.argv[2], int(sys.argv[3]), Path(sys.argv[4]),
                         sys.argv[5], capture_fn=partial(capture, window_dc=True)))
