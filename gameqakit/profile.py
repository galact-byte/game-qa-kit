#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""游戏 QA 工具的每游戏配置（profile）。

引擎无关的核心逻辑读 profile 取得：进程名、存档目录/文件模式、QA 数据目录、
常用截图区域。换一个游戏 = 换一份 profile，核心代码不动。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

# profiles/ 目录：<repo>/profiles，与本包同级
_PKG_DIR = Path(__file__).resolve().parent
_PROFILES_DIR = _PKG_DIR.parent / "profiles"


@dataclass
class GameProfile:
    """一个游戏的 QA 配置。"""

    name: str
    proc: str                                   # 进程名（不含 .exe）
    title_substr: str | None = None             # 找窗口的标题兜底子串
    client_size: list[int] | None = None        # 期望客户区 [w,h]（仅提示/坐标系说明）
    savedata_dir: str | None = None             # 游戏存档目录（绝对路径）
    save_globs: list[str] = field(default_factory=list)  # 需快照的存档文件通配
    data_dir: str | None = None                 # QA 数据根（快照/截图/盯屏/state）
    regions: dict[str, list[int]] = field(default_factory=dict)  # 命名截图区域 [x,y,w,h]
    notes: str = ""

    # ---- 派生目录 ----
    @property
    def data_path(self) -> Path:
        d = (self.data_dir
             or os.environ.get("GAMEQAKIT_DATA")
             or str(Path.home() / ".gameqakit" / self.name))
        return Path(d)

    @property
    def saves_dir(self) -> Path:
        return self.data_path / "saves"

    @property
    def shots_dir(self) -> Path:
        return self.data_path / "shots"

    @property
    def watch_dir(self) -> Path:
        return self.data_path / "watch"

    @property
    def state_dir(self) -> Path:
        return self.data_path / "state"

    @property
    def savedata_path(self) -> Path | None:
        return Path(self.savedata_dir) if self.savedata_dir else None

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in (
            "name", "proc", "title_substr", "client_size", "savedata_dir",
            "save_globs", "data_dir", "regions", "notes")}


def from_dict(d: dict) -> GameProfile:
    known = {"name", "proc", "title_substr", "client_size", "savedata_dir",
             "save_globs", "data_dir", "regions", "notes"}
    return GameProfile(**{k: v for k, v in d.items() if k in known})


def load_profile(path_or_name: str) -> GameProfile:
    """按路径或名字加载 profile。名字在 profiles/<name>.json 查找。"""
    p = Path(path_or_name)
    if p.suffix.lower() == ".json" and p.exists():
        return from_dict(json.loads(p.read_text(encoding="utf-8")))
    cand = _PROFILES_DIR / f"{path_or_name}.json"
    if cand.exists():
        return from_dict(json.loads(cand.read_text(encoding="utf-8")))
    raise FileNotFoundError(f"找不到 profile：{path_or_name}（也不在 {_PROFILES_DIR}）")


def active_profile() -> GameProfile:
    """当前激活 profile：环境变量 GAMEQAKIT_PROFILE 指定名字/路径；否则若只有一份则用它。"""
    name = os.environ.get("GAMEQAKIT_PROFILE")
    if name:
        return load_profile(name)
    jsons = sorted(_PROFILES_DIR.glob("*.json")) if _PROFILES_DIR.exists() else []
    if len(jsons) == 1:
        return from_dict(json.loads(jsons[0].read_text(encoding="utf-8")))
    raise RuntimeError(
        "未指定激活 profile：设置环境变量 GAMEQAKIT_PROFILE=<名字或json路径>"
        f"（profiles/ 下现有 {[p.stem for p in jsons]}）")


def list_profiles() -> list[str]:
    if not _PROFILES_DIR.exists():
        return []
    return [p.stem for p in sorted(_PROFILES_DIR.glob("*.json"))]
