"""Emuera 取图入口；具体实现共用于毫秒协议的独立宿主。"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from gameqakit.capture_worker import run

if __name__ == '__main__':
    raise SystemExit(run(Path(sys.argv[1]), sys.argv[2], int(sys.argv[3]), Path(sys.argv[4]), sys.argv[5]))
