#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""game-qa-kit 图形界面（PyQt6）：更直观的盯屏巡检控制台。

主线：选 profile → 开始盯屏 → 你正常手玩（画面变化自动留图）→ 看到疑点点“标记”
→ 停止后“汇总复核日志”。截图/快照/关游戏为辅助。
"""
from __future__ import annotations

import sys
import threading
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gameqakit import profile as _profile          # noqa: E402
from gameqakit import win32, watch as _watch, saves as _saves  # noqa: E402

from PyQt6.QtCore import Qt, QObject, QThread, pyqtSignal  # noqa: E402
from PyQt6.QtGui import QPixmap  # noqa: E402
from PyQt6.QtWidgets import (  # noqa: E402
    QApplication, QComboBox, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QMainWindow, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget)

QSS = """
* { font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif; font-size: 13px; }
QMainWindow, QWidget { background: #1e1f22; color: #e6e6e6; }
QGroupBox { border: 1px solid #34363b; border-radius: 8px; margin-top: 10px; padding: 10px; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 4px; color: #9aa0a6; }
QPushButton { background: #2d2f34; border: 1px solid #3a3d44; border-radius: 6px;
              padding: 6px 12px; }
QPushButton:hover { background: #3a3d44; }
QPushButton:pressed { background: #26282c; }
QPushButton#primary { background: #2f7d46; border: none; color: #fff; font-weight: 600; }
QPushButton#primary:hover { background: #369152; }
QPushButton#danger { background: #7d2f2f; border: none; color: #fff; }
QLineEdit, QComboBox { background: #26282c; border: 1px solid #3a3d44; border-radius: 6px;
                       padding: 5px 8px; }
QPlainTextEdit { background: #17181a; border: 1px solid #2a2c30; border-radius: 6px;
                 font-family: Consolas, monospace; font-size: 12px; }
QLabel#thumb { background: #17181a; border: 1px dashed #3a3d44; border-radius: 8px; color: #666; }
QLabel#status { color: #9aa0a6; }
"""


class Signals(QObject):
    log = pyqtSignal(str)
    thumb = pyqtSignal(str)
    watch_state = pyqtSignal(bool)


class WatchThread(QThread):
    def __init__(self, profile, out, interval, threshold, region, sig: Signals):
        super().__init__()
        self.p, self.out, self.interval = profile, out, interval
        self.threshold, self.region, self.sig = threshold, region, sig
        self.stop_event = threading.Event()

    def run(self):
        self.sig.watch_state.emit(True)
        try:
            _watch.watch(self.out, self.p.proc, self.p.state_dir, interval=self.interval,
                         threshold=self.threshold, region=self.region,
                         stop_event=self.stop_event,
                         on_event=lambda o: self.sig.log.emit(str(o)))
        except Exception as e:  # noqa: BLE001
            self.sig.log.emit(f"盯屏错误：{e}")
        finally:
            self.sig.watch_state.emit(False)


class App(QMainWindow):
    def __init__(self):
        super().__init__()
        self.profile: _profile.GameProfile | None = None
        self.watch_thread: WatchThread | None = None
        self.sig = Signals()
        self.sig.log.connect(self._log)
        self.sig.thumb.connect(self._show_thumb)
        self.sig.watch_state.connect(self._set_watch_state)

        self.setWindowTitle("game-qa-kit · 盯屏巡检控制台")
        self.resize(920, 640)
        self._build()
        self._refresh_profiles()

    # ---------------- UI ----------------
    def _build(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # 顶部：profile
        top = QHBoxLayout()
        top.addWidget(QLabel("游戏 Profile:"))
        self.cb_profile = QComboBox()
        self.cb_profile.setMinimumWidth(160)
        top.addWidget(self.cb_profile)
        b_load = QPushButton("加载"); b_load.clicked.connect(self._load_profile); top.addWidget(b_load)
        b_chk = QPushButton("检测窗口"); b_chk.clicked.connect(lambda: self._run(self._check_window)); top.addWidget(b_chk)
        self.lb_status = QLabel("未加载 profile"); self.lb_status.setObjectName("status")
        top.addWidget(self.lb_status); top.addStretch(1)
        root.addLayout(top)

        # 盯屏
        gb_w = QGroupBox("盯屏（你手玩，画面变化才自动留图）")
        gw = QGridLayout(gb_w)
        gw.addWidget(QLabel("间隔秒"), 0, 0)
        self.e_interval = QLineEdit("2.0"); self.e_interval.setFixedWidth(56); gw.addWidget(self.e_interval, 0, 1)
        gw.addWidget(QLabel("变化阈值"), 0, 2)
        self.e_threshold = QLineEdit("4"); self.e_threshold.setFixedWidth(56); gw.addWidget(self.e_threshold, 0, 3)
        gw.addWidget(QLabel("区域 x,y,w,h(可空)"), 0, 4)
        self.e_region = QLineEdit(); self.e_region.setFixedWidth(150); gw.addWidget(self.e_region, 0, 5)
        self.btn_watch = QPushButton("▶ 开始盯屏"); self.btn_watch.setObjectName("primary")
        self.btn_watch.clicked.connect(self._toggle_watch); gw.addWidget(self.btn_watch, 0, 6)
        self.lb_watch = QLabel("● 未运行"); self.lb_watch.setStyleSheet("color:#c0504d;")
        gw.addWidget(self.lb_watch, 0, 7)
        gw.setColumnStretch(8, 1)
        root.addWidget(gb_w)

        # 标记 + 动作
        gb_m = QGroupBox("标记疑点 / 动作")
        gm = QHBoxLayout(gb_m)
        gm.addWidget(QLabel("备注:"))
        self.e_note = QLineEdit(); gm.addWidget(self.e_note, 1)
        b_mark = QPushButton("★ 标记当前画面"); b_mark.setObjectName("primary")
        b_mark.clicked.connect(lambda: self._run(self._mark)); gm.addWidget(b_mark)
        for text, fn in [("截图", self._capture), ("汇总复核日志", self._report),
                         ("列出快照", self._list_saves)]:
            b = QPushButton(text); b.clicked.connect(lambda _, f=fn: self._run(f)); gm.addWidget(b)
        b_open = QPushButton("打开数据目录"); b_open.clicked.connect(self._open_data); gm.addWidget(b_open)
        root.addWidget(gb_m)

        # 预览 + 日志
        body = QHBoxLayout()
        self.lb_thumb = QLabel("(截图/标记后显示)")
        self.lb_thumb.setObjectName("thumb")
        self.lb_thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lb_thumb.setFixedWidth(380)
        self.lb_thumb.setMinimumHeight(300)
        body.addWidget(self.lb_thumb)
        self.txt = QPlainTextEdit(); self.txt.setReadOnly(True)
        body.addWidget(self.txt, 1)
        root.addLayout(body, 1)

        self.setStyleSheet(QSS)

    # ---------------- 基础设施 ----------------
    def _log(self, msg: str):
        self.txt.appendPlainText(f"[{datetime.now():%H:%M:%S}] {msg}")

    def _run(self, fn):
        def worker():
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                self.sig.log.emit(f"错误：{e}")
        threading.Thread(target=worker, daemon=True).start()

    def _need_profile(self) -> _profile.GameProfile:
        if not self.profile:
            raise RuntimeError("请先加载一个 profile")
        return self.profile

    def _parse_region(self):
        s = self.e_region.text().strip()
        return [int(x) for x in s.split(",")] if s else None

    # ---------------- 动作 ----------------
    def _refresh_profiles(self):
        self.cb_profile.clear()
        self.cb_profile.addItems(_profile.list_profiles())

    def _load_profile(self):
        name = self.cb_profile.currentText()
        if not name:
            return
        self.profile = _profile.load_profile(name)
        self.lb_status.setText(f"已加载 {name}（proc={self.profile.proc}）")
        self._log(f"加载 profile：{name}")

    def _check_window(self):
        p = self._need_profile()
        try:
            hwnd = win32.find_window(p.proc, p.title_substr)
            info = win32.window_info(hwnd)
            self.sig.log.emit(f"窗口就绪 hwnd={hwnd} 客户区={info['client']} "
                              f"{'(被前台遮挡)' if info['occluded_by_foreground'] else '(前台)'}")
        except Exception as e:  # noqa: BLE001
            self.sig.log.emit(f"未找到游戏窗口：{e}")

    def _set_watch_state(self, running: bool):
        self.lb_watch.setText("● 运行中" if running else "● 未运行")
        self.lb_watch.setStyleSheet("color:#4caf50;" if running else "color:#c0504d;")
        self.btn_watch.setText("■ 停止盯屏" if running else "▶ 开始盯屏")
        self.btn_watch.setObjectName("danger" if running else "primary")
        self.btn_watch.setStyleSheet("")  # 触发 objectName 样式刷新
        self.setStyleSheet(QSS)

    def _toggle_watch(self):
        if self.watch_thread and self.watch_thread.isRunning():
            self.watch_thread.stop_event.set()
            self._log("正在停止盯屏…")
            return
        p = self._need_profile()
        out = p.watch_dir / datetime.now().strftime("%Y%m%d_%H%M%S")
        self.watch_thread = WatchThread(
            p, out, float(self.e_interval.text() or 2.0),
            int(self.e_threshold.text() or 4), self._parse_region(), self.sig)
        self.watch_thread.start()

    def _mark(self):
        p = self._need_profile()
        r = _watch.mark(self.e_note.text().strip(), p.proc, p.state_dir, p.watch_dir)
        self.sig.log.emit(f"已标记：{r['marked']}")
        self.sig.thumb.emit(r["marked"])

    def _capture(self):
        p = self._need_profile()
        hwnd = win32.find_window(p.proc, p.title_substr)
        out = p.shots_dir / f"shot_{datetime.now():%Y%m%d_%H%M%S}.png"
        win32.capture(hwnd, out, region=self._parse_region())
        self.sig.log.emit(f"截图：{out}")
        self.sig.thumb.emit(str(out))

    def _report(self):
        p = self._need_profile()
        r = _watch.watch_report(p.state_dir, p.watch_dir)
        self.sig.log.emit(f"复核日志：{r['report']}（画面 {r['frames']} 标记 {r['marks']}）")

    def _list_saves(self):
        p = self._need_profile()
        rows = _saves.list_saves(p.saves_dir)
        self.sig.log.emit(f"快照 {len(rows)} 个：" + ", ".join(x["name"] for x in rows[:20]))

    def _open_data(self):
        if not self.profile:
            return
        import os
        d = self.profile.data_path
        d.mkdir(parents=True, exist_ok=True)
        os.startfile(str(d))  # noqa: S606

    def _show_thumb(self, path: str):
        pix = QPixmap(path)
        if pix.isNull():
            self.lb_thumb.setText("预览失败")
            return
        self.lb_thumb.setPixmap(pix.scaled(
            self.lb_thumb.width() - 8, self.lb_thumb.height() - 8,
            Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def closeEvent(self, e):
        if self.watch_thread and self.watch_thread.isRunning():
            self.watch_thread.stop_event.set()
            self.watch_thread.wait(3000)
        super().closeEvent(e)


def main():
    app = QApplication(sys.argv)
    w = App()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
