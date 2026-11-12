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
from gameqakit import win32, watch as _watch, saves as _saves, icons, sessions  # noqa: E402

from PyQt6.QtCore import Qt, QObject, QSize, QTimer, pyqtSignal  # noqa: E402
from PyQt6.QtGui import QPixmap  # noqa: E402
from PyQt6.QtWidgets import (  # noqa: E402
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QListWidget, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget)

ACCENT = "#2f6bff"
QSS = f"""
* {{ font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif; font-size: 13px; color: #1f2328; }}
QMainWindow {{ background: #ffffff; }}
#main {{ background: #ffffff; }}
QLabel {{ background: transparent; }}
#sidebar {{ background: #f5f6f8; border-right: 1px solid #e6e8eb; }}
#brand {{ font-size: 17px; font-weight: 700; color: {ACCENT}; }}
#navBtn {{ text-align: left; background: transparent; border: none; border-radius: 8px;
          padding: 8px 10px; color: #3c4149; }}
#navBtn:hover {{ background: #e9edf5; }}
#sectionLabel {{ color: #97a0ab; font-size: 11px; font-weight: 600; }}
#status {{ color: #97a0ab; font-size: 12px; }}
#hint {{ color: #97a0ab; font-size: 11px; }}
#h1 {{ font-size: 22px; font-weight: 700; }}
#sub {{ color: #97a0ab; }}
QComboBox, QLineEdit {{ background: #ffffff; border: 1px solid #dfe3e8; border-radius: 8px; padding: 6px 8px; }}
QComboBox:focus, QLineEdit:focus {{ border: 1px solid {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 26px; subcontrol-origin: padding; subcontrol-position: center right; }}
QComboBox::down-arrow {{ width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{ background: #ffffff; border: 1px solid #dfe3e8; border-radius: 8px;
    padding: 4px; outline: none; selection-background-color: #f0f4ff; selection-color: #1f2328; }}
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
    """LiveAgent 风格动作卡：线性图标 + 标题 + 副标题，可点击。"""
    clicked = pyqtSignal()

    def __init__(self, icon_name, title, subtitle, primary=False):
        super().__init__()
        self.primary = primary
        self.icon_color = "#ffffff" if primary else ACCENT
        self.setObjectName("cardPrimary" if primary else "card")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(title)
        self.setMinimumHeight(72)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(12)
        self.ic = QLabel(); self.ic.setFixedWidth(26)
        self.ic.setPixmap(icons.pixmap(icon_name, self.icon_color, 22))
        lay.addWidget(self.ic, alignment=Qt.AlignmentFlag.AlignVCenter)
        col = QVBoxLayout(); col.setSpacing(2)
        self.t = QLabel(title); self.t.setObjectName("cardTitleOn" if primary else "cardTitle")
        self.s = QLabel(subtitle); self.s.setObjectName("cardSubOn" if primary else "cardSub")
        col.addWidget(self.t); col.addWidget(self.s)
        lay.addLayout(col); lay.addStretch(1)

    def set_icon(self, name):
        self.ic.setPixmap(icons.pixmap(name, self.icon_color, 22))

    def set_text(self, title, subtitle):
        self.t.setText(title); self.s.setText(subtitle)
        self.setAccessibleName(title)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
            event.accept()
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(e)


class Signals(QObject):
    log = pyqtSignal(str)
    thumb = pyqtSignal(str)
    watch_result = pyqtSignal(dict)


class SuspectDialog(QDialog):
    """每页只读取二十条元数据，选中时才加载一张原图。"""
    def __init__(self, profile, parent=None):
        super().__init__(parent)
        self.profile, self.offset, self.rows = profile, 0, []
        self.setWindowTitle("自动疑点 · 待复核")
        self.resize(900, 600)
        layout = QVBoxLayout(self)
        self.summary = QLabel(); self.summary.setWordWrap(True); layout.addWidget(self.summary)
        body = QHBoxLayout()
        self.items = QListWidget(); self.items.setAccessibleName("疑点列表")
        body.addWidget(self.items, 1)
        right = QVBoxLayout()
        self.preview = QLabel("暂无原图"); self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(320, 180)
        right.addWidget(self.preview, 1)
        self.detail = QPlainTextEdit(); self.detail.setReadOnly(True)
        self.detail.setAccessibleName("疑点依据与原图路径")
        right.addWidget(self.detail, 1)
        body.addLayout(right, 2); layout.addLayout(body, 1)
        buttons = QHBoxLayout()
        self.previous = QPushButton("上一页"); self.next = QPushButton("下一页")
        refresh = QPushButton("刷新"); close = QPushButton("关闭")
        for button in (self.previous, self.next, refresh, close):
            button.setMinimumHeight(44); buttons.addWidget(button)
        self.previous.clicked.connect(lambda: self._page(-20))
        self.next.clicked.connect(lambda: self._page(20))
        refresh.clicked.connect(self._load); close.clicked.connect(self.accept)
        self.items.currentRowChanged.connect(self._select)
        layout.addLayout(buttons)
        self._load()

    def _page(self, delta):
        self.offset = max(0, self.offset + delta)
        self._load()

    def _load(self):
        result = _watch.watch_events(self.profile.state_dir, self.profile.watch_dir,
                                     limit=20, offset=self.offset)
        self.rows = result["events"]
        self.items.clear(); self.detail.clear(); self.preview.clear()
        for row in self.rows:
            categories = ", ".join(item["category"] for item in row["findings"])
            self.items.addItem(f"{row['sequence']} · {categories}\n{row['captured_at']}")
        total = result["total"]
        self.summary.setText(f"共 {total} 处自动疑点，均待复核；没有疑点不等于没有缺陷。" if total else
                             "当前无自动疑点；请继续查看人工标记和普通留图，不能据此判定无缺陷。")
        if result.get("errors"):
            self.summary.setText(self.summary.text() + "\n" + "\n".join(result["errors"]))
        self.previous.setEnabled(self.offset > 0)
        self.next.setEnabled(self.offset + 20 < total)
        if self.rows:
            self.items.setCurrentRow(0)
        else:
            self.preview.setText("暂无原图")

    def _select(self, index):
        if not 0 <= index < len(self.rows):
            return
        import json
        row = self.rows[index]
        self.detail.setPlainText(json.dumps(row, ensure_ascii=False, indent=2))
        pixmap = QPixmap(row["image"])
        if pixmap.isNull():
            self.preview.setText("原图无法读取，请检查证据路径")
        else:
            self.preview.setPixmap(pixmap.scaled(520, 280, Qt.AspectRatioMode.KeepAspectRatio,
                                                 Qt.TransformationMode.SmoothTransformation))


class ProfileDialog(QDialog):
    """新建/编辑一个游戏 profile。"""

    def __init__(self, parent=None, existing=None):
        super().__init__(parent)
        self.setWindowTitle("编辑 Profile" if existing else "新建 Profile")
        self.setMinimumWidth(600)
        self.setStyleSheet(QSS)
        form = QFormLayout(self); form.setSpacing(8)
        intro = QLabel("先打开游戏，再从进程列表选择，自动读取配置。\n"
                       "存档目录等无法确定的信息可稍后补充，也支持手动填写。")
        intro.setObjectName("hint"); intro.setWordWrap(True); form.addRow(intro)
        self.e_name = QLineEdit(); self.e_proc = QLineEdit(); self.e_title = QLineEdit()
        self.e_save = QLineEdit(); self.e_globs = QLineEdit(); self.e_data = QLineEdit()
        self.e_path = QLineEdit(); self.e_path.setReadOnly(True)
        self.e_path.setPlaceholderText("选择进程后自动读取；用于区分同名程序")
        self._selected = None
        self._client_size = existing.client_size if existing else None
        self._notes = existing.notes if existing else ""
        self.e_proc.textChanged.connect(self._manual_process_changed)
        self.e_title.setPlaceholderText("可空，留空更安全（只按进程名匹配）")
        self.e_proc.setPlaceholderText("如 Game")
        self.e_globs.setPlaceholderText("如 data*.dat, *.var（逗号分隔，改档才需）")
        self.e_data.setPlaceholderText("新配置请选择 QA 数据目录，原有配置保持原位置")
        if existing:
            self.e_name.setText(existing.name); self.e_name.setEnabled(False)
            self.e_proc.setText(existing.proc); self.e_title.setText(existing.title_substr or "")
            self.e_path.setText(existing.exe_path or "")
            self.e_save.setText(existing.savedata_dir or "")
            self.e_globs.setText(", ".join(existing.save_globs))
            self.e_data.setText(existing.data_dir or "")
            self._regions = existing.regions
        else:
            self._regions = {}
        picker = QWidget(); row = QHBoxLayout(picker); row.setContentsMargins(0, 0, 0, 0)
        self.cb_process = QComboBox()
        self.cb_process.setAccessibleName("运行中的游戏进程")
        self.cb_process.setMinimumHeight(44)
        self.cb_process.setMinimumContentsLength(28)
        self.cb_process.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.cb_process.currentIndexChanged.connect(self._select_process)
        row.addWidget(self.cb_process, 1)
        refresh = QPushButton("刷新"); refresh.setObjectName("ghost"); refresh.setMinimumHeight(44)
        refresh.clicked.connect(self._refresh_processes); row.addWidget(refresh)
        form.addRow("运行中的程序", picker)
        self.lb_process_hint = QLabel(); self.lb_process_hint.setWordWrap(True)
        form.addRow(self.lb_process_hint)
        form.addRow("名称 *", self.e_name)
        form.addRow("进程名 *", self.e_proc)
        form.addRow("程序路径", self.e_path)
        form.addRow("窗口标题子串", self.e_title)
        form.addRow("存档目录", self._with_browse(self.e_save))
        form.addRow("存档文件模式", self.e_globs)
        form.addRow("QA 数据目录", self._with_browse(self.e_data))
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._on_ok); bb.rejected.connect(self.reject)
        form.addRow(bb)
        self._refresh_processes()

    def _manual_process_changed(self):
        if self._selected:
            self._client_size = None
            self.cb_process.blockSignals(True)
            self.cb_process.setCurrentIndex(0)
            self.cb_process.blockSignals(False)
            self.lb_process_hint.setText("已改为手动配置；请选择进程以恢复精确定位。")
        self._selected = None
        self.e_path.clear()

    def _refresh_processes(self):
        previous = self._selected["pid"] if self._selected else None
        self.cb_process.blockSignals(True)
        self.cb_process.clear()
        self.cb_process.addItem("请选择运行中的游戏（也可手动填写）", None)
        try:
            rows = win32.list_process_targets()
            for item in rows:
                self.cb_process.addItem(f"{item['name']} · PID {item['pid']} · {item['title']}", item)
            self.lb_process_hint.setText(f"找到 {len(rows)} 个有窗口的程序；请选择游戏。" if rows else
                                         "未找到可读取的程序，请打开游戏后刷新，或手动填写。")
        except Exception as exc:
            self.lb_process_hint.setText(f"读取进程失败：{exc}；仍可手动填写。")
        index = next((i for i in range(1, self.cb_process.count())
                      if self.cb_process.itemData(i)["pid"] == previous), 0)
        self.cb_process.setCurrentIndex(index)
        self.cb_process.blockSignals(False)
        self._select_process(index)

    def _select_process(self, index):
        item = self.cb_process.itemData(index)
        if not item:
            self._selected = None
            return
        self.e_proc.blockSignals(True)
        self.e_proc.setText(Path(item["exe_path"]).stem)
        self.e_proc.blockSignals(False)
        self.e_path.setText(item["exe_path"])
        self.e_path.setToolTip(item["exe_path"])
        self.e_title.clear()
        self.e_title.setPlaceholderText("通常留空；已用程序路径定位")
        self._selected = item
        self._client_size = item["client_size"]
        self.cb_process.setToolTip(f"{item['title']}\n{item['exe_path']}")
        if not self.e_name.text().strip():
            import re
            executable = Path(item["exe_path"])
            label = executable.parent.name if executable.stem.casefold() == "game" else executable.stem
            name = re.sub(r'[<>:"/\\\\|?*\x00-\x1f]', '_', label).strip(" .")[:60]
            name = name or executable.stem
            candidate, sequence = name, 2
            while (_profile.profiles_dir() / f"{candidate}.json").exists():
                candidate = f"{name}-{sequence}"; sequence += 1
            self.e_name.setText(candidate)
        size = f"窗口 {self._client_size[0]}×{self._client_size[1]}" if self._client_size else "窗口已最小化，尺寸待恢复后读取"
        self.lb_process_hint.setText(f"已读取 PID {item['pid']}，{size}；未猜测存档目录。")

    def _with_browse(self, edit):
        w = QWidget(); h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(6)
        h.addWidget(edit, 1)
        b = QPushButton("浏览"); b.setObjectName("ghost")
        b.clicked.connect(lambda: self._browse(edit)); h.addWidget(b)
        return w

    def _browse(self, edit):
        d = QFileDialog.getExistingDirectory(self, "选择目录", edit.text() or "")
        if d:
            edit.setText(d)

    def _on_ok(self):
        if not self.e_name.text().strip() or not self.e_proc.text().strip():
            QMessageBox.warning(self, "缺少必填", "名称和进程名不能为空")
            return
        if self.e_name.isEnabled() and not self.e_data.text().strip():
            QMessageBox.warning(self, "缺少 QA 数据目录", "请明确选择 QA 数据目录，用于保存截图、观察记录和证据。")
            return
        self.accept()

    def result_profile(self):
        globs = [g.strip() for g in self.e_globs.text().split(",") if g.strip()]
        return _profile.GameProfile(
            name=self.e_name.text().strip(), proc=self.e_proc.text().strip(),
            title_substr=self.e_title.text().strip() or None,
            savedata_dir=self.e_save.text().strip() or None,
            save_globs=globs, data_dir=self.e_data.text().strip() or None,
            regions=self._regions, client_size=self._client_size, notes=self._notes,
            exe_path=self.e_path.text().strip() or None,
            target_pid=self._selected["pid"] if self._selected else None)


class App(QMainWindow):
    def __init__(self):
        super().__init__()
        self.profile = None
        self._watch_running = self._watch_busy = self._owns_watch = self._closing = False
        self._last_warning = self._last_error = ""
        self.sig = Signals()
        self.sig.log.connect(self._log)
        self.sig.thumb.connect(self._show_thumb)
        self.sig.watch_result.connect(self._watch_result)
        self.setWindowTitle("game-qa-kit · 盯屏巡检控制台")
        self.resize(1000, 660)
        self._build()
        self._refresh_profiles()
        self.watch_timer = QTimer(self)
        self.watch_timer.timeout.connect(self._poll_watch)
        self.watch_timer.start(500)

    # ---------------- UI ----------------
    def _nav(self, icon_name, text, slot):
        b = QPushButton("  " + text); b.setObjectName("navBtn")
        b.setIcon(icons.icon(icon_name, "#5b6470", 18)); b.setIconSize(QSize(18, 18))
        b.clicked.connect(slot)
        return b

    def _build(self):
        central = QWidget(); self.setCentralWidget(central)
        root = QHBoxLayout(central); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)

        # ---- 侧栏 ----
        side = QFrame(); side.setObjectName("sidebar"); side.setFixedWidth(220)
        sl = QVBoxLayout(side); sl.setContentsMargins(16, 16, 16, 16); sl.setSpacing(10)
        brand = QHBoxLayout(); brand.setSpacing(8)
        bi = QLabel(); bi.setPixmap(icons.pixmap("eye", ACCENT, 20))
        brand.addWidget(bi); brand.addWidget(QLabel("game-qa-kit", objectName="brand")); brand.addStretch(1)
        sl.addLayout(brand)
        sl.addSpacing(6)
        sl.addWidget(QLabel("游戏 PROFILE", objectName="sectionLabel"))
        hint = QLabel("一个游戏的配置：进程名 / 存档 / 截图区。换游戏换一份。")
        hint.setObjectName("hint"); hint.setWordWrap(True); sl.addWidget(hint)
        self.cb_profile = QComboBox(); sl.addWidget(self.cb_profile)
        prow = QHBoxLayout(); prow.setSpacing(6)
        for text, slot in [("加载", self._load_profile), ("新建", self._new_profile), ("编辑", self._edit_profile)]:
            b = QPushButton(text); b.setObjectName("ghost"); b.clicked.connect(slot); prow.addWidget(b)
        sl.addLayout(prow)
        sl.addSpacing(10)
        sl.addWidget(QLabel("工具", objectName="sectionLabel"))
        sl.addWidget(self._nav("search", "检测窗口", lambda: self._run(self._check_window)))
        sl.addWidget(self._nav("play", "AI 后台接入", self._game_admission))
        sl.addWidget(self._nav("database", "列出快照", lambda: self._run(self._list_saves)))
        sl.addWidget(self._nav("folder", "打开数据目录", self._open_data))
        sl.addStretch(1)
        self.lb_status = QLabel("未加载 profile", objectName="status")
        self.lb_status.setWordWrap(True); sl.addWidget(self.lb_status)
        root.addWidget(side)

        # ---- 主区 ----
        main = QWidget(); main.setObjectName("main")
        ml = QVBoxLayout(main); ml.setContentsMargins(24, 22, 24, 20); ml.setSpacing(16)
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
        self.card_watch = Card("play", "开始盯屏", "自动留证，画面变化才截图", primary=True)
        self.card_watch.clicked.connect(self._toggle_watch)
        self.card_mark = Card("star", "标记疑点", "即刻截当前画面并写备注")
        self.card_mark.clicked.connect(lambda: self._run(self._mark))
        self.card_shot = Card("image", "截图", "抓一张当前画面")
        self.card_shot.clicked.connect(lambda: self._run(self._capture))
        self.card_report = Card("file", "汇总复核日志", "人工标记、自动疑点、画面时间线")
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
        sg.addWidget(QLabel("分析方式"), 2, 0)
        self.cb_analysis = QComboBox()
        self.cb_analysis.setAccessibleName("分析方式")
        self.cb_analysis.addItem("窗口标题筛选（无需模型）", "rules")
        self.cb_analysis.addItem("OCR＋窗口标题（可选依赖）", "ocr")
        self.cb_analysis.addItem("仅留图", "off")
        self.cb_analysis.setToolTip("OCR 不可用时继续留图并提示降级；自动疑点必须人工或模型复核。")
        sg.addWidget(self.cb_analysis, 2, 1, 1, 3)
        self.b_events = QPushButton("查看自动疑点"); self.b_events.setObjectName("ghost")
        self.b_events.setMinimumHeight(44); self.b_events.clicked.connect(self._show_events)
        sg.addWidget(self.b_events, 2, 4, 1, 2)
        self.lb_stats = QLabel("尚未开始采集；自动疑点不是已确认缺陷。")
        self.lb_stats.setWordWrap(True); self.lb_stats.setAccessibleName("盯屏运行统计")
        sg.addWidget(self.lb_stats, 3, 0, 1, 6)
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
        # 自定义下拉箭头(避免默认带边框方块) + 弹出列表圆角
        chevron = icons.save_png("chevron", "#8a9099", 24)
        self.setStyleSheet(QSS + f'\nQComboBox::down-arrow {{ image: url("{chevron}"); }}\n')

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
    def _game_admission(self):
        from .admission_gui import AdmissionDialog
        AdmissionDialog(self).exec()

    def _refresh_profiles(self):
        self.cb_profile.clear(); self.cb_profile.addItems(_profile.list_profiles())

    def _load_profile(self):
        try:
            if self._watch_busy:
                self._log("请等待当前盯屏操作完成，再切换配置")
                return
            if self.profile and sessions.watch_status(self.profile)["running"]:
                self._log("请先停止当前会话，再切换 profile")
                return
            name = self.cb_profile.currentText()
            if not name:
                return
            self.profile = _profile.load_profile(name)
            self._owns_watch = False
            self._last_warning = self._last_error = ""
            self._poll_watch()
            sd = self.profile.savedata_dir or "(未设，不能改档)"
            self.lb_status.setText(f"已加载：{name}\n进程：{self.profile.proc}\n存档：{sd}")
            self._log(f"加载 profile：{name}（proc={self.profile.proc}）")
        except Exception as e:  # noqa: BLE001
            self._log(f"加载失败：{e}")

    def _profile_editable(self):
        try:
            busy = self._watch_running or self._watch_busy or (
                self.profile and sessions.watch_status(self.profile).get("running"))
        except Exception as exc:
            self._log(f"无法确认盯屏状态，暂不修改配置：{exc}")
            return False
        if busy:
            self._log("请先停止当前盯屏，再修改游戏配置")
        return not busy

    def _new_profile(self):
        if not self._profile_editable():
            return
        dlg = ProfileDialog(self)
        if dlg.exec():
            if not self._profile_editable():
                return
            try:
                p = dlg.result_profile()
                _profile.save_profile(p)
                self._refresh_profiles()
                self.cb_profile.setCurrentText(p.name)
                self._load_profile()
                self.profile = p
                self._log(f"已创建 profile：{p.name}")
            except Exception as e:  # noqa: BLE001
                self._log(f"创建失败：{e}")

    def _edit_profile(self):
        if not self._profile_editable():
            return
        name = self.cb_profile.currentText()
        if not name:
            self._log("请先选中一个 profile 再编辑")
            return
        try:
            cur = _profile.load_profile(name)
        except Exception as e:  # noqa: BLE001
            self._log(f"读取失败：{e}")
            return
        dlg = ProfileDialog(self, existing=cur)
        if dlg.exec():
            if not self._profile_editable():
                return
            try:
                p = dlg.result_profile()
                _profile.save_profile(p)
                self._refresh_profiles()
                self.cb_profile.setCurrentText(p.name)
                self._load_profile()
                self.profile = p
                self._log(f"已保存 profile：{p.name}")
            except Exception as e:  # noqa: BLE001
                self._log(f"保存失败：{e}")

    def _check_window(self):
        p = self._need_profile()
        try:
            hwnd = win32.find_profile_window(p)
            info = win32.window_info(hwnd)
            self.sig.log.emit(f"窗口就绪 hwnd={hwnd} 客户区={info['client']} "
                              f"{'(被前台遮挡)' if info['occluded_by_foreground'] else '(前台)'}")
        except Exception as e:  # noqa: BLE001
            self.sig.log.emit(f"未找到游戏窗口：{e}")

    def _set_watch_state(self, running):
        if running:
            self.lb_watch.setText("● 运行中"); self.lb_watch.setStyleSheet("background:#e7f6ec;color:#2f9e52;")
            self.card_watch.set_icon("stop"); self.card_watch.set_text("停止盯屏", "点此结束本次盯屏会话")
        else:
            self.lb_watch.setText("● 未运行"); self.lb_watch.setStyleSheet("background:#fdecea;color:#c0504d;")
            self.card_watch.set_icon("play"); self.card_watch.set_text("开始盯屏", "自动留证，画面变化才截图")

    def _toggle_watch(self):
        if self._watch_busy:
            return
        try:
            p = self._need_profile()
            stopping = self._watch_running
            options = {} if stopping else dict(interval=float(self.e_interval.text() or 2.0),
                                                threshold=int(self.e_threshold.text() or 4),
                                                region=self._parse_region(),
                                                analysis_mode=self.cb_analysis.currentData())
            if not stopping:
                _watch.validate_options(**options)
            self._watch_busy = True
            self.card_watch.setEnabled(False)
            self._log("正在停止盯屏…" if stopping else "正在启动后台盯屏…")
            def action():
                try:
                    result = sessions.stop_watch(p) if stopping else sessions.start_watch(p, **options)
                    result["_owner"] = not stopping and not result.get("already_running", False)
                    if self._closing and result["_owner"]:
                        sessions.stop_watch(p)
                except Exception as exc:
                    result = {"error": str(exc)}
                if not self._closing:
                    self.sig.watch_result.emit(result)
            threading.Thread(target=action, name="GameQA-Control", daemon=False).start()
        except Exception as exc:
            self._log(f"无法开始盯屏：{exc}")

    def _watch_result(self, result):
        self._watch_busy = False
        if "error" in result:
            self._log(f"盯屏错误：{result['error']}")
            self.card_watch.setEnabled(True)
            return
        if result.get("_owner"):
            self._owns_watch = True
        self._apply_stats(result)

    def _poll_watch(self):
        if self.profile and not self._closing:
            self._apply_stats(sessions.watch_status(self.profile))

    def _apply_stats(self, result):
        self._watch_running = result.get("running", False)
        self._set_watch_state(self._watch_running)
        stopping = result.get("watch") == "stopping"
        self.card_watch.setEnabled(not self._watch_busy and not stopping)
        if stopping:
            self.card_watch.set_text("停止中", "正在排空分析队列，证据会保留")
        self.cb_analysis.setEnabled(not self._watch_running and not self._watch_busy)
        counts = "  ·  ".join(f"{label} {result.get(key, 0)}" for label, key in (
            ("采集", "captured"), ("留图", "saved"), ("分析", "analyzed"),
            ("疑点", "suspects"), ("分析丢帧", "dropped"), ("积压", "queue_depth")))
        warning = result.get("analysis_warning", "")
        error = result.get("last_error", "")
        suffix = ("\n降级：" + warning) if warning else ""
        if error:
            suffix += "\n最近错误：" + error
        self.lb_stats.setText(counts + suffix)
        if warning and warning != self._last_warning:
            self._log(f"分析降级：{warning}")
        if error and error != self._last_error:
            self._log(error)
        self._last_warning, self._last_error = warning, error

    def _show_events(self):
        try:
            SuspectDialog(self._need_profile(), self).exec()
        except Exception as exc:
            self._log(f"无法查看疑点：{exc}")

    def _mark(self):
        p = self._need_profile()
        r = _watch.mark(self.e_note.text().strip(), p.proc, p.state_dir, p.watch_dir,
                        title_substr=p.title_substr, exe_path=p.exe_path,
                        target_pid=win32.profile_target(p)["pid"])
        self.sig.log.emit(f"已标记：{r['marked']}")
        self.sig.thumb.emit(r["marked"])

    def _capture(self):
        p = self._need_profile()
        hwnd = win32.find_profile_window(p)
        out = p.shots_dir / f"shot_{datetime.now():%Y%m%d_%H%M%S}.png"
        win32.capture(hwnd, out, region=self._parse_region())
        self.sig.log.emit(f"截图：{out}")
        self.sig.thumb.emit(str(out))

    def _report(self):
        p = self._need_profile()
        r = _watch.watch_report(p.state_dir, p.watch_dir)
        self.sig.log.emit(f"复核日志：{r['report']}（画面 {r['frames']} 标记 {r['marks']} 自动疑点 {r['suspects']}）")

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
        self._closing = True
        self.watch_timer.stop()
        if self.profile and self._owns_watch:
            try:
                sessions.stop_watch(self.profile)
            except Exception as exc:
                self._log(f"停止请求失败：{exc}")
        super().closeEvent(e)


def main():
    # 全局异常钩子：防止任何槽函数未捕获异常触发 Qt abort 导致闪退
    def _hook(exc_type, exc, tb):
        import traceback
        traceback.print_exception(exc_type, exc, tb)
    sys.excepthook = _hook
    app = QApplication(sys.argv)
    App().show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
