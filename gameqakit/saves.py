#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""存档快照/恢复 + 进程护栏（引擎无关；哪些文件由 profile.save_globs 决定）。

铁律（本轮示例游戏教训）：
- 改存档前先游戏内存盘：磁盘档只反映最后一次游戏内保存，内存未落盘进度会随关进程丢失。
- 恢复后运行中的游戏不重读磁盘：必须 kill→重启→游戏内 AUTO LOAD 才生效，否则读到假数据。
- 覆盖当前存档前自动备份 + 全程 SHA-256 校验，绝不用损坏快照覆盖存档。
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from .win32 import find_window


def _ts() -> str:
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _iter_save_files(savedata: Path, save_globs: list[str]):
    if not savedata.is_dir():
        raise RuntimeError(f"savedata 目录不存在: {savedata}")
    seen = set()
    for pat in save_globs:
        for p in sorted(savedata.glob(pat)):
            if p.is_file() and p.name not in seen:
                seen.add(p.name)
                yield p


def snapshot_save(name: str, savedata: Path, save_globs: list[str], saves_dir: Path,
                  note: str = "") -> Path:
    dest = saves_dir / name
    if dest.exists():
        raise RuntimeError(f"快照已存在: {name}（换个名字或先删）")
    dest.mkdir(parents=True, exist_ok=True)
    manifest = {"name": name, "note": note, "created": _dt.datetime.now().isoformat(),
                "source": str(savedata), "files": {}}
    for p in _iter_save_files(savedata, save_globs):
        shutil.copy2(p, dest / p.name)
        manifest["files"][p.name] = {"size": p.stat().st_size, "sha256": _sha256(p)}
    (dest / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return dest


def _verify_snapshot(src: Path) -> dict:
    mf = src / "manifest.json"
    if not mf.exists():
        raise RuntimeError(f"快照缺少 manifest.json，拒绝恢复: {src.name}")
    try:
        manifest = json.loads(mf.read_text(encoding="utf-8"))
    except Exception as e:
        raise RuntimeError(f"快照 manifest.json 解析失败，拒绝恢复: {e}")
    files = manifest.get("files", {})
    if not files:
        raise RuntimeError("快照 manifest 未记录任何文件，拒绝恢复")
    for fname, meta in files.items():
        fp = src / fname
        if not fp.is_file():
            raise RuntimeError(f"快照文件缺失，拒绝恢复: {fname}")
        if meta.get("sha256", "") != _sha256(fp):
            raise RuntimeError(f"快照 SHA-256 校验失败，拒绝恢复: {fname}")
    return manifest


def restore_save(name: str, savedata: Path, save_globs: list[str], saves_dir: Path) -> Path:
    src = saves_dir / name
    if not src.is_dir():
        raise RuntimeError(f"快照不存在: {name}")
    manifest = _verify_snapshot(src)                       # 1) 触碰存档前先校验
    try:                                                   # 2) 恢复前自动备份当前
        snapshot_save(f"_autobackup_{_ts()}", savedata, save_globs, saves_dir,
                      note=f"restore {name} 前自动备份")
    except Exception as e:
        raise RuntimeError(f"恢复前自动备份失败，已中止恢复以保护当前存档：{e}")
    keep = set(manifest.get("files", {}))                  # 3) 清理不属于快照的存档文件
    for p in _iter_save_files(savedata, save_globs):
        if p.name not in keep:
            p.unlink()
    for fname in manifest.get("files", {}):                # 4) 按 manifest 恢复
        shutil.copy2(src / fname, savedata / fname)
    return src


def list_saves(saves_dir: Path) -> list[dict]:
    out = []
    if not Path(saves_dir).is_dir():
        return out
    for d in sorted(Path(saves_dir).iterdir()):
        if not d.is_dir():
            continue
        info = {"name": d.name}
        mf = d / "manifest.json"
        if mf.exists():
            try:
                m = json.loads(mf.read_text(encoding="utf-8"))
                info.update(created=m.get("created", ""), note=m.get("note", ""),
                            files=list(m.get("files", {}).keys()))
            except Exception:
                pass
        out.append(info)
    return out


def is_game_running(proc: str) -> bool:
    try:
        find_window(proc)
        return True
    except RuntimeError:
        return False


def kill_game(proc: str) -> dict:
    """可靠终止游戏进程（subprocess 列参，避开 Git Bash 对 $ 变量的提前展开坑）。"""
    name = proc if proc.lower().endswith(".exe") else proc + ".exe"
    was = is_game_running(proc)
    r = subprocess.run(["taskkill", "/F", "/IM", name], capture_output=True, text=True)
    return {"ok": True, "process": name, "was_running": was,
            "killed": bool(was and r.returncode == 0), "returncode": r.returncode,
            "stdout": (r.stdout or "").strip(), "stderr": (r.stderr or "").strip(),
            "reminder": "改档流程：先游戏内存档→kill_game→确认磁盘快照→重启 exe→游戏内 AUTO LOAD→截图核对"}
