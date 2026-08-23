#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自动盯屏：人正常玩，脚本只在画面变化时留证，事后异步复核（引擎无关）。

本轮示例游戏巡检验证的核心工作流，也是最值得跨游戏复用的能力：
  人快速手玩 → watch 自动去重留图 + mark 随手标疑点 → watch_report 汇总 → 模型/人异步复核。
只“录”不“判”：不标记任何帧为缺陷，只保证不漏画面；真正判定是事后另一步。
"""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

from .win32 import find_window, grab_image, clamp_region, capture
from .dedup import dhash_from_image, hamming


def _ts() -> str:
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def _log(obj) -> None:
    import sys
    print(json.dumps(obj, ensure_ascii=False), file=sys.stderr, flush=True)


def watch(out_dir: Path, proc: str, state_dir: Path, interval: float = 2.0,
          threshold: int = 4, region=None, downscale: int | None = None,
          stop_event=None, on_event=None, title_substr: str | None = None) -> dict:
    """循环后台抓帧，只在结构变化（dHash 汉明距离 > threshold）时落盘。
    CLI 下 Ctrl+C 停止；GUI 传 stop_event(threading.Event) 优雅停止。
    on_event(dict) 可选回调，供 GUI 实时显示；缺省走 stderr 日志。
    返回汇总 {saved, polls, out}。内存恒定；静止不重复落盘；游戏重启自动跟随新句柄。"""
    import time

    def emit(obj):
        (on_event or _log)(obj)

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    idx_path = out_dir / "index.jsonl"
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "watch_current.json").write_text(json.dumps(
        {"dir": str(out_dir), "started": _dt.datetime.now().isoformat(timespec="seconds")},
        ensure_ascii=False), encoding="utf-8")
    prev_hash = None
    saved = polls = 0
    emit({"ok": True, "watch": "started", "out": str(out_dir), "interval": interval,
          "threshold": threshold, "hint": "正常玩即可，只在画面变化时自动留图"})
    try:
        while not (stop_event and stop_event.is_set()):
            polls += 1
            try:
                hwnd = find_window(proc, title_substr)
                img = grab_image(hwnd)
            except Exception:
                time.sleep(max(interval, 1.0))
                continue
            if region:
                x, y, rw, rh = clamp_region(region, *img.size)
                img = img.crop((x, y, x + rw, y + rh))
            cur = dhash_from_image(img)
            if prev_hash is not None and hamming(cur, prev_hash) <= threshold:
                time.sleep(interval)
                continue
            prev_hash = cur
            out = out_dir / f"watch_{_ts()}.png"
            save_img = img.resize((img.width // downscale, img.height // downscale)) \
                if downscale and downscale > 1 else img
            save_img.save(str(out), "PNG")
            saved += 1
            with idx_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"t": _dt.datetime.now().isoformat(timespec="seconds"),
                                    "file": out.name, "hwnd": hwnd, "hash": f"{cur:016x}"},
                                   ensure_ascii=False) + "\n")
            emit({"saved": saved, "file": str(out)})
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
    emit({"ok": True, "watch": "stopped", "saved": saved, "polls": polls, "out": str(out_dir)})
    return {"saved": saved, "polls": polls, "out": str(out_dir)}


def _active_watch_dir(state_dir: Path, watch_dir: Path) -> Path | None:
    ptr = Path(state_dir) / "watch_current.json"
    if ptr.exists():
        try:
            d = Path(json.loads(ptr.read_text(encoding="utf-8"))["dir"])
            if d.exists():
                return d
        except Exception:
            pass
    watch_dir = Path(watch_dir)
    if watch_dir.exists():
        subs = [p for p in watch_dir.iterdir() if p.is_dir()]
        if subs:
            return max(subs, key=lambda p: p.stat().st_mtime)
    return None


def mark(note: str, proc: str, state_dir: Path, watch_dir: Path,
         title_substr: str | None = None) -> dict:
    """玩时看到可疑画面随手标：即刻截当前帧+写备注到本次盯屏会话。复核时优先看标记帧。"""
    d = _active_watch_dir(state_dir, watch_dir) or (Path(watch_dir) / _ts())
    d.mkdir(parents=True, exist_ok=True)
    hwnd = find_window(proc, title_substr)
    out = d / f"mark_{_ts()}.png"
    w, h = capture(hwnd, out)
    rec = {"t": _dt.datetime.now().isoformat(timespec="seconds"),
           "file": out.name, "note": note, "hwnd": hwnd, "size": [w, h]}
    with (d / "marks.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return {"ok": True, "marked": str(out), "note": note, "dir": str(d)}


def _read_jsonl(path: Path) -> list[dict]:
    if not Path(path).exists():
        return []
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    return rows


def watch_report(state_dir: Path, watch_dir: Path, out_dir: Path | None = None) -> dict:
    """把盯屏会话汇总为 复核日志.txt：标记帧在前（优先看），全部画面时间线在后。"""
    d = Path(out_dir) if out_dir else _active_watch_dir(state_dir, watch_dir)
    if d is None or not d.exists():
        raise RuntimeError("找不到盯屏会话目录；先跑 watch，或显式指定 out_dir。")
    frames = _read_jsonl(d / "index.jsonl")
    marks = _read_jsonl(d / "marks.jsonl")
    L = [f"复核日志（盯屏会话 {d.name}）", "=" * 48,
         f"自动留图（画面变化帧）：{len(frames)} 张",
         f"用户标记（优先复核）：{len(marks)} 处",
         "复核方式：先看【标记帧】→再走时间线；疑似日文先排除繁体同形字，确认后再录入",
         "结论栏填法：OK / 待查 / 已记录(编号)", "",
         "【用户标记帧 · 优先看】", "-" * 48]
    if marks:
        for i, m in enumerate(marks, 1):
            L.append(f"[{i}] {m.get('t','')}  {m.get('file','')}")
            L.append(f"     备注：{m.get('note','') or '—'}")
            L.append("     复核结论：____")
    else:
        L.append("（本次无手动标记）")
    L += ["", "【全部画面 · 时间线】", "-" * 48]
    for i, fr in enumerate(frames, 1):
        L.append(f"[{i}] {fr.get('t','')}  {fr.get('file','')}   复核结论：____")
    txt = d / "复核日志.txt"
    txt.write_text("\n".join(L) + "\n", encoding="utf-8")
    return {"ok": True, "report": str(txt), "frames": len(frames), "marks": len(marks)}
