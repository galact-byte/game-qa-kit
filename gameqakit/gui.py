#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""game-qa-kit 图形界面（PyQt6）——LiveAgent 风格：浅色 + 蓝色主色 + 左侧栏 + 卡片式动作。

主线：选 profile → 开始盯屏 → 你正常手玩（画面变化自动留图）→ 看到疑点点“标记”
→ 停止后“汇总复核日志”。截图/快照为辅助。
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
    QApplication, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QMainWindow, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget)

ACCENT = "#2f6bff"
QSS = f"""
* {{ font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif; font-size: 13px; color: #1f2328; }}
QMainWindow, QWidget {{ background: #ffffff; }}
#sidebar {{ background: #f5f6f8; border-right: 1px solid #e6e8eb; }}
#brand {{ font-size: 17px; font-weight: 700; color: {ACCENT}; }}
#navBtn {{ text-align: left; background: transparent; border: none; border-radius: 8px;
          padding: 8px 10px; color: #3c4149; }}
#navBtn:hover {{ background: #e9edf5; }}
#sectionLabel {{ color: #97a0ab; font-size: 11px; font-weight: 600; }}
#status {{ color: #97a0ab; font-size: 12px; }}
#h1 {{ font-size: 22px; font-weight: 700; }}
#sub {{ color: #97a0ab; }}
QComboBox, QLineEdit {{ background: #ffffff; border: 1px solid #dfe3e8; border-radius: 8px; padding: 6px 8px; }}
QComboBox:focus, QLineEdit:focus {{ border: 1px solid {ACCENT}; }}
#card {{ background: #ffffff; border: 1px solid #e6e8eb; border-radius: 12px; }}
#card:hover {{ border: 1px solid {ACCENT}; background: #f5f8ff; }}
#cardPrimary {{ background: {ACCENT}; border: 1px solid {ACCENT}; border-radius: 12px; }}
#cardPrimary:hover {{ background: #1e5bef; }}
#cardTitle {{ font-size: 14px; font-weight: 600; }}
#cardTitleOn {{ font-size: 14px; font-weight: 600; color: #ffffff; }}
#cardSub {{ color: #97a0ab; font-size: 12px; }}
#cardSubOn {{ color: #dce7ff; font-size: 12px; }}
#cardIcon {{ font-size: 20px; }}
#panel {{ background: #ffffff; border: 1px solid #e6e8eb; border-radius: 12px; }}
#thumb {{ background: #fafbfc; border: 1px dashed #dfe3e8; border-radius: 12px; color: #aeb4bd; }}
QPushButton#ghost {{ background: #ffffff; border: 1px solid #dfe3e8; border-radius: 8px; padding: 6px 12px; }}
QPushButton#ghost:hover {{ background: #f0f4ff; border-color: {ACCENT}; }}
QPlainTextEdit {{ background: #fbfbfc; border: 1px solid #e6e8eb; border-radius: 12px;
                  font-family: Consolas, monospace; font-size: 12px; color: #3c4149; }}
#pill {{ border-radius: 10px; padding: 2px 10px; font-size: 12px; }}
"""


class Card(QFrame):
    """LiveAgent 风格动作卡：图标 + 标题 + 副标题，可点击。"""
    clicked = pyqtSignal()

    def __init__(self, icon, title, subtitle, primary=False):
        super().__init__()
        self.primary = primary
        self.setObjectName("cardPrimary" if primary else "card")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(12)
        self.ic = QLabel(icon); self.ic.setObjectName("cardIcon")
        lay.addWidget(self.ic)
        col = QVBoxLayout(); col.setSpacing(2)
        self.t = QLabel(title); self.t.setObjectName("cardTitleOn" if primary else "cardTitle")
        self.s = QLabel(subtitle); self.s.setObjectName("cardSubOn" if primary else "cardSub")
        col.addWidget(self.t); col.addWidget(self.s)
        lay.addLayout(col); lay.addStretch(1)

    def set_text(self, icon, title, subtitle):
        self.ic.setText(icon); self.t.setText(title); self.s.setText(subtitle)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(e)


class Signals(QObject):
    log = pyqtSignal(str)
    thumb = pyqtSignal(str)
    watch_state = pyqtSignal(bool)


class WatchThread(QThread):
    def __init__(self, profile, out, interval, threshold, region, sig):
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
        self.profile = None
        self.watch_thread = None
        self.sig = Signals()
        self.sig.log.connect(self._log)
        self.sig.thumb.connect(self._show_thumb)
        self.sig.watch_state.connect(self._set_watch_state)
        self.setWindowTitle("game-qa-kit · 盯屏巡检控制台")
        self.resize(1000, 660)
        self._build()
        self._refresh_profiles()

    # ---------------- UI ----------------
    def _nav(self, text, slot):
        b = QPushButton(text); b.setObjectName("navBtn"); b.clicked.connect(slot)
        return b

    def _build(self):
        central = QWidget(); self.setCentralWidget(central)
        root = QHBoxLayout(central); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)

        # ---- 侧栏 ----
        side = QFrame(); side.setObjectName("sidebar"); side.setFixedWidth(220)
        sl = QVBoxLayout(side); sl.setContentsMargins(16, 16, 16, 16); sl.setSpacing(10)
        sl.addWidget(QLabel("🕊  game-qa-kit", objectName="brand"))
        sl.addSpacing(6)
        sl.addWidget(QLabel("游戏 PROFILE", objectName="sectionLabel"))
        self.cb_profile = QComboBox(); sl.addWidget(self.cb_profile)
        b_load = QPushButton("加载"); b_load.setObjectName("ghost"); b_load.clicked.connect(self._load_profile)
        sl.addWidget(b_load)
        sl.addSpacing(10)
        sl.addWidget(QLabel("操作", objectName="sectionLabel"))
        sl.addWidget(self._nav("🔎  检测窗口", lambda: self._run(self._check_window)))
        sl.addWidget(self._nav("🖼  截图", lambda: self._run(self._capture)))
        sl.addWidget(self._nav("📄  汇总复核日志", lambda: self._run(self._report)))
        sl.addWidget(self._nav("💾  列出快照", lambda: self._run(self._list_saves)))
        sl.addWidget(self._nav("📂  打开数据目录", self._open_data))
        sl.addStretch(1)
        self.lb_status = QLabel("未加载 profile", objectName="status")
        self.lb_status.setWordWrap(True); sl.addWidget(self.lb_status)
        root.addWidget(side)

        # ---- 主区 ----
        main = QWidget(); ml = QVBoxLayout(main); ml.setContentsMargins(24, 22, 24, 20); ml.setSpacing(16)
        head = QHBoxLayout()
        hbox = QVBoxLayout(); hbox.setSpacing(2)
        hbox.addWidget(QLabel("盯屏巡检", objectName="h1"))
        hbox.addWidget(QLabel("你手玩，工具只在画面变化时自动留证，事后异步复核", objectName="sub"))
        head.addLayout(hbox); head.addStretch(1)
        self.lb_watch = QLabel("● 未运行", objectName="pill")
        self.lb_watch.setStyleSheet("background:#fdecea;color:#c0504d;")
        head.addWidget(self.lb_watch, alignment=Qt.AlignmentFlag.AlignTop)
        ml.addLayout(head)

        # 动作卡
        grid = QGridLayout(); grid.setSpacing(12)
        self.card_watch = Card("▶", "开始盯屏", "自动留证，画面变化才截图", primary=True)
        self.card_watch.clicked.connect(self._toggle_watch)
        self.card_mark = Card("★", "标记疑点", "即刻截当前画面并写备注")
        self.card_mark.clicked.connect(lambda: self._run(self._mark))
        self.card_shot = Card("🖼", "截图", "抓一张当前画面")
        self.card_shot.clicked.connect(lambda: self._run(self._capture))
        self.card_report = Card("📄", "汇总复核日志", "标记帧在前，全部帧时间线在后")
        self.card_report.clicked.connect(lambda: self._run(self._report))
        for i, c in enumerate((self.card_watch, self.card_mark, self.card_shot, self.card_report)):
            grid.addWidget(c, i // 2, i % 2)
        ml.addLayout(grid)

        # 设置行（盯屏参数 + 备注）
        settings = QFrame(); settings.setObjectName("panel")
        sg = QGridLayout(settings); sg.setContentsMargins(14, 12, 14, 12); sg.setHorizontalSpacing(8)
        sg.addWidget(QLabel("间隔秒"), 0, 0)
        self.e_interval = QLineEdit("2.0"); self.e_interval.setFixedWidth(60); sg.addWidget(self.e_interval, 0, 1)
        sg.addWidget(QLabel("变化阈值"), 0, 2)
        self.e_threshold = QLineEdit("4"); self.e_threshold.setFixedWidth(60); sg.addWidget(self.e_threshold, 0, 3)
        sg.addWidget(QLabel("区域 x,y,w,h"), 0, 4)
        self.e_region = QLineEdit(); self.e_region.setPlaceholderText("可空，如 0,560,1200,160"); sg.addWidget(self.e_region, 0, 5)
        sg.addWidget(QLabel("标记备注"), 1, 0)
        self.e_note = QLineEdit(); self.e_note.setPlaceholderText("看到疑点写一句，再点“标记疑点”"); sg.addWidget(self.e_note, 1, 1, 1, 5)
        sg.setColumnStretch(5, 1)
        ml.addWidget(settings)

        # 预览 + 日志
        body = QHBoxLayout(); body.setSpacing(12)
        self.lb_thumb = QLabel("（截图/标记后显示预览）", objectName="thumb")
        self.lb_thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lb_thumb.setFixedWidth(360); self.lb_thumb.setMinimumHeight(240)
        body.addWidget(self.lb_thumb)
        self.txt = QPlainTextEdit(); self.txt.setReadOnly(True); body.addWidget(self.txt, 1)
        ml.addLayout(body, 1)

        root.addWidget(main, 1)
        self.setStyleSheet(QSS)

    # ---------------- 基础设施 ----------------
    def _log(self, msg):
        self.txt.appendPlainText(f"[{datetime.now():%H:%M:%S}] {msg}")

    def _run(self, fn):
        def worker():
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                self.sig.log.emit(f"错误：{e}")
        threading.Thread(target=worker, daemon=True).start()

    def _need_profile(self):
        if not self.profile:
            raise RuntimeError("请先在左上角选择并加载一个 profile")
        return self.profile

    def _parse_region(self):
        s = self.e_region.text().strip()
        return [int(x) for x in s.split(",")] if s else None

    # ---------------- 动作 ----------------
    def _refresh_profiles(self):
        self.cb_profile.clear(); self.cb_profile.addItems(_profile.list_profiles())

    def _load_profile(self):
        name = self.cb_profile.currentText()
        if not name:
            return
        self.profile = _profile.load_profile(name)
        self.lb_status.setText(f"已加载 {name}\nproc={self.profile.proc}")
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

    def _set_watch_state(self, running):
        if running:
            self.lb_watch.setText("● 运行中"); self.lb_watch.setStyleSheet("background:#e7f6ec;color:#2f9e52;")
            self.card_watch.set_text("■", "停止盯屏", "点此结束本次盯屏会话")
        else:
            self.lb_watch.setText("● 未运行"); self.lb_watch.setStyleSheet("background:#fdecea;color:#c0504d;")
            self.card_watch.set_text("▶", "开始盯屏", "自动留证，画面变化才截图")

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
        d = self.profile.data_path; d.mkdir(parents=True, exist_ok=True)
        os.startfile(str(d))  # noqa: S606

    def _show_thumb(self, path):
        pix = QPixmap(path)
        if pix.isNull():
            self.lb_thumb.setText("预览失败"); return
        self.lb_thumb.setPixmap(pix.scaled(
            self.lb_thumb.width() - 10, self.lb_thumb.height() - 10,
            Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def closeEvent(self, e):
        if self.watch_thread and self.watch_thread.isRunning():
            self.watch_thread.stop_event.set(); self.watch_thread.wait(3000)
        super().closeEvent(e)


def main():
    app = QApplication(sys.argv)
    App().show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
