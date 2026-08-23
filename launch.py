"""game-qa-kit 启动器：环境检查 + 依赖安装 + 启动 GUI。"""

import subprocess
import sys
from pathlib import Path


def run(cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, shell=True, capture_output=True, text=True)


def check_python():
    ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    print(f"[OK] Python 版本: {ver}")
    if sys.version_info < (3, 8):
        print("[X] 需要 Python 3.8+，请升级后重试")
        sys.exit(1)


def check_dependencies():
    print("\n检查依赖包...")
    # tkinter 属标准库但个别发行版缺失；单独检查给清晰提示
    packages = {"PIL": "Pillow", "PyQt6": "PyQt6"}
    for import_name, install_name in packages.items():
        try:
            __import__(import_name)
            print(f"  [OK] {import_name}")
        except ImportError:
            print(f"  [..] {import_name} 未安装，正在安装...")
            run(f"{sys.executable} -m pip install {install_name} -q")
            try:
                __import__(import_name)
                print(f"  [OK] {import_name} 安装成功")
            except ImportError:
                print(f"  [X] {import_name} 安装失败，请手动执行: pip install {install_name}")
                sys.exit(1)


def start_app():
    print("\n正在启动 GUI...")
    print("=" * 50)
    gui = Path(__file__).resolve().parent / "gameqakit" / "gui.py"
    try:
        subprocess.run([sys.executable, str(gui)])
    except KeyboardInterrupt:
        print("\n已停止")


def main():
    print("=" * 50)
    print("  game-qa-kit 盯屏巡检控制台")
    print("=" * 50)
    print()
    check_python()
    check_dependencies()
    start_app()


if __name__ == "__main__":
    main()
