"""显式运行的 Windows 原生窗口集成检查，不使用真实游戏或系统输入。

python tests/native_smoke.py --out <项目外证据目录>
短暂显示两个不激活窗口，验证被遮挡采集、事件归档和共享会话控制。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["QT_QPA_PLATFORM"] = "windows"

from PIL import Image
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget
from gameqakit import profile, sessions, watch, win32


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    root = Path(args.out).resolve()
    root.mkdir(parents=True, exist_ok=False)
    qt = QApplication([])
    foreground_before = int(win32.user32.GetForegroundWindow() or 0)
    title = f"GameQA Native Smoke {uuid.uuid4().hex} FileNotFoundError"
    target = QWidget(flags=Qt.WindowType.Window | Qt.WindowType.WindowDoesNotAcceptFocus)
    target.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    target.setWindowTitle(title)
    target.resize(480, 280)
    target.move(40, 80)
    target.setStyleSheet("background:#218c55;color:white;")
    layout = QVBoxLayout(target)
    layout.addWidget(QLabel("原生窗口采集验证\nFileNotFoundError: fixture.dat\n这里应为绿色，而不是遮挡层的红色"))
    cover = QWidget(flags=Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint |
                    Qt.WindowType.WindowDoesNotAcceptFocus)
    cover.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    cover.setWindowTitle("GameQA smoke occluder")
    cover.resize(540, 340)
    cover.move(10, 50)
    cover.setStyleSheet("background:#d63031;")
    p = profile.GameProfile(name="native-smoke", proc=Path(sys.executable).stem,
                            title_substr=title, data_dir=str(root / "data"),
                            exe_path=sys.executable, target_pid=os.getpid())
    evidence = None
    try:
        target.show(); cover.show()
        qt.processEvents()
        started = sessions.start_watch(p, interval=0.1)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            qt.processEvents()
            result = watch.watch_events(p.state_dir, p.watch_dir)
            if result["events"]:
                evidence = result["events"][0]
                break
            time.sleep(0.02)
        if evidence is None:
            raise AssertionError(sessions.watch_status(p))
        assert evidence["findings"][0]["category"] == "FILE_NOT_FOUND", evidence
        with Image.open(evidence["image"]) as image:
            r, g, b = image.convert("RGB").getpixel((image.width // 2, image.height * 3 // 4))
            assert g > r and g > b, (r, g, b)
        duplicate = sessions.start_watch(p)
        assert duplicate["out"] == started["out"] and duplicate["already_running"]
        sessions.stop_watch(p)
        deadline = time.monotonic() + 5
        while sessions.watch_status(p)["running"] and time.monotonic() < deadline:
            qt.processEvents(); time.sleep(0.02)
        status = sessions.watch_status(p)
        assert not status["running"], status
        report = watch.watch_report(p.state_dir, p.watch_dir)
        foreground_after = int(win32.user32.GetForegroundWindow() or 0)
        summary = {"passed": True, "captured_while_occluded": True,
                   "captured": status["captured"], "suspects": status["suspects"],
                   "capture_errors": status["capture_errors"], "analysis_errors": status["analysis_errors"],
                   "foreground_before": foreground_before, "foreground_after": foreground_after,
                   "foreground_unchanged": foreground_before == foreground_after,
                   "evidence": evidence["image"], "report": report["report"]}
        sessions.write_json(root / "result.json", summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    finally:
        sessions.stop_watch(p)
        for child in list(sessions._CHILDREN.values()):
            child.wait(timeout=5)
        cover.close(); target.close(); qt.processEvents()


if __name__ == "__main__":
    main()
