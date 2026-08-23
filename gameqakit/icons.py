#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""轻量线性图标（Feather 风，MIT）。内联 SVG 按需着色渲染为 QPixmap/QIcon，
避免 emoji 的“AI 风”，与 LiveAgent 的线性图标语言一致。零额外资源文件。
"""
from __future__ import annotations

from PyQt6.QtCore import QByteArray, Qt
from PyQt6.QtGui import QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer

# 描边类图标（fill=none, stroke=color）
_STROKE = {
    "eye": '<circle cx="12" cy="12" r="3"/><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z"/>',
    "search": '<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
    "image": '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/>',
    "file": '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>',
    "database": '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/>',
    "folder": '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>',
    "chevron": '<polyline points="6 9 12 15 18 9"/>',
}
# 填充类图标（fill=color, stroke=none）
_FILL = {
    "play": '<polygon points="6 4 20 12 6 20 6 4"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="1.5"/>',
    "star": '<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>',
}


def _svg(name: str, color: str) -> str:
    if name in _FILL:
        body, extra = _FILL[name], f'fill="{color}" stroke="none"'
    else:
        body = _STROKE.get(name, _STROKE["eye"])
        extra = f'fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"'
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" {extra}>{body}</svg>'


def pixmap(name: str, color: str = "#5b6470", size: int = 20) -> QPixmap:
    r = QSvgRenderer(QByteArray(_svg(name, color).encode("utf-8")))
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    r.render(p)
    p.end()
    return pm


def icon(name: str, color: str = "#5b6470", size: int = 18) -> QIcon:
    return QIcon(pixmap(name, color, size))


def save_png(name: str, color: str = "#5b6470", size: int = 24) -> str:
    """把图标渲染为 PNG 落盘到临时目录，返回正斜杠路径供 QSS url() 使用。"""
    import tempfile
    from pathlib import Path
    d = Path(tempfile.gettempdir()) / "gameqakit_icons"
    d.mkdir(parents=True, exist_ok=True)
    fp = d / f"{name}_{color.lstrip('#')}_{size}.png"
    if not fp.exists():
        pixmap(name, color, size).save(str(fp), "PNG")
    return str(fp).replace("\\", "/")
