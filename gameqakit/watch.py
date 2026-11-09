"""后台采集、画面去重、异步疑点筛选及兼容旧会话的复核报告。"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
import os
import threading
import time
from pathlib import Path

from .win32 import find_window, grab_image, clamp_region, capture, window_text
from .dedup import dhash_from_image, hamming
from .analysis import FrameAnalyzer
from .sessions import SessionLease, write_json


def _ts():
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def _log(obj):
    import sys
    print(json.dumps(obj, ensure_ascii=False), file=sys.stderr, flush=True)


def validate_options(interval=2.0, threshold=4, region=None, downscale=None, analysis_mode="rules"):
    if isinstance(interval, bool) or not isinstance(interval, (int, float)) or not math.isfinite(interval) or interval <= 0:
        raise ValueError("采集间隔必须是大于零的有限秒数")
    if type(threshold) is not int or not 0 <= threshold <= 64:
        raise ValueError("变化阈值必须是 0–64 的整数")
    if downscale is not None and (type(downscale) is not int or downscale < 1):
        raise ValueError("缩小倍数必须是正整数")
    if region is not None and (not isinstance(region, (list, tuple)) or len(region) != 4 or
                               any(type(v) is not int for v in region) or
                               region[0] < 0 or region[1] < 0 or region[2] <= 0 or region[3] <= 0):
        raise ValueError("区域必须是 [x,y,w,h] 整数数组，原点非负、宽高大于零")
    if analysis_mode not in ("off", "rules", "ocr"):
        raise ValueError("分析模式必须是 off、rules 或 ocr")


def watch(out_dir: Path, proc: str, state_dir: Path, interval: float = 2.0,
          threshold: int = 4, region=None, downscale: int | None = None,
          stop_event=None, on_event=None, title_substr: str | None = None,
          analysis_mode: str = "rules", exe_path=None, target_pid=None) -> dict:
    """同一 profile 跨进程仅一个会话。停止采集后排空至多四帧的分析队列。"""
    validate_options(interval, threshold, region, downscale, analysis_mode)
    with SessionLease(Path(state_dir) / "watch.lock"):
        return _run_watch(Path(out_dir).resolve(), proc, Path(state_dir), interval, threshold,
                          region, downscale, stop_event or threading.Event(), on_event,
                          title_substr, analysis_mode, exe_path, target_pid)


def _run_watch(out_dir, proc, state_dir, interval, threshold, region, downscale,
               stop_event, on_event, title_substr, analysis_mode, exe_path, target_pid):
    if (out_dir / "index.jsonl").exists() or (out_dir / "status.json").exists():
        raise ValueError("会话目录已有记录，请使用新的输出目录，避免覆盖证据")
    out_dir.mkdir(parents=True, exist_ok=True)
    status = {"ok": True, "running": True, "watch": "started", "out": str(out_dir),
              "pid": os.getpid(), "target_pid": target_pid, "target_exe_path": exe_path,
              "started_at": _dt.datetime.now().isoformat(),
              "interval": interval, "threshold": threshold, "analysis_mode": analysis_mode,
              "analysis_status": "loading" if analysis_mode != "off" else "disabled",
              "analysis_warning": "", "last_error": "", "polls": 0, "captured": 0,
              "saved": 0, "analyzed": 0, "dropped": 0, "suspects": 0,
              "capture_errors": 0, "analysis_errors": 0, "storage_errors": 0,
              "capture_ms": 0.0, "analysis_ms": 0.0, "queue_depth": 0}
    counters = {"polls", "captured", "saved", "analyzed", "dropped", "suspects",
                "capture_errors", "analysis_errors", "storage_errors"}
    lock = threading.Lock()
    analyzer = None

    def update(**changes):
        with lock:
            for key, value in changes.items():
                status[key] = status[key] + value if key in counters else value

    def emit(obj):
        try:
            (on_event or _log)(obj)
        except Exception as exc:
            update(last_error=f"通知回调失败：{exc}")

    def publish():
        with lock:
            status["queue_depth"] = analyzer.frames.qsize() if analyzer else 0
            snapshot = dict(status)
        write_json(out_dir / "status.json", snapshot)
        emit({"kind": "stats", **snapshot})
        return snapshot

    write_json(state_dir / "watch_current.json", {"dir": str(out_dir), "started": status["started_at"]})
    publish()
    prev_hash = prev_digest = prev_hwnd = None

    def stopped():
        return stop_event.is_set() or (out_dir / "stop.request").exists()

    def wait(seconds):
        deadline = time.monotonic() + seconds
        while not stopped():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            stop_event.wait(min(remaining, 0.05))

    try:
        if analysis_mode != "off":
            analyzer = FrameAnalyzer(out_dir, analysis_mode, update, emit)
        while not stopped():
            started = time.perf_counter()
            update(polls=1)
            try:
                hwnd = find_window(proc, title_substr, exe_path=exe_path, pid=target_pid)
                img = grab_image(hwnd)
                native = window_text(hwnd)
                capture_region = [0, 0, *img.size]
                if region:
                    x, y, rw, rh = clamp_region(region, *img.size)
                    img = img.crop((x, y, x + rw, y + rh))
                    capture_region = [x, y, rw, rh]
                update(captured=1, capture_ms=round((time.perf_counter() - started) * 1000, 2))
            except Exception as exc:
                update(capture_errors=1, last_error=f"采集失败：{exc}")
                prev_digest = None
                publish()
                wait(interval)
                continue
            if hwnd != prev_hwnd:
                prev_hash = prev_digest = None
                prev_hwnd = hwnd
            captured_at = _dt.datetime.now().isoformat(timespec="microseconds")
            digest = hashlib.blake2s(img.tobytes() + native.encode("utf-8", "replace") +
                                     str(img.size).encode("ascii")).digest()
            if analyzer and (digest != prev_digest or analyzer.retry.is_set()):
                analyzer.retry.clear()
                # Pillow 保存时会临时修改图像对象，分析线程必须持有独立副本。
                analyzer.submit({"hwnd": hwnd, "image": img.copy(), "native_text": native[:16000],
                                 "capture_region": capture_region, "captured_at": captured_at})
                prev_digest = digest
            cur = dhash_from_image(img)
            if prev_hash is None or hamming(cur, prev_hash) > threshold:
                try:
                    out = out_dir / f"watch_{_ts()}.png"
                    save_img = img.resize((max(1, img.width // downscale), max(1, img.height // downscale))) \
                        if downscale and downscale > 1 else img
                    save_img.save(str(out), "PNG")
                    with (out_dir / "index.jsonl").open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps({"t": captured_at, "file": out.name, "hwnd": hwnd,
                                                 "hash": f"{cur:016x}"}, ensure_ascii=False) + "\n")
                    prev_hash = cur
                    update(saved=1)
                    emit({"kind": "frame", "file": str(out)})
                except Exception as exc:
                    update(storage_errors=1, last_error=f"留图失败：{exc}")
            publish()
            wait(max(0, interval - (time.perf_counter() - started)))
    except KeyboardInterrupt:
        stop_event.set()
    except Exception as exc:
        update(last_error=str(exc), ok=False)
        raise
    finally:
        update(watch="stopping")
        try:
            publish()
        finally:
            if analyzer:
                analyzer.close()
            update(running=False, watch="stopped", stopped_at=_dt.datetime.now().isoformat())
            publish()
    return dict(status)


def _active_watch_dir(state_dir: Path, watch_dir: Path) -> Path | None:
    ptr = Path(state_dir) / "watch_current.json"
    if ptr.exists():
        try:
            directory = Path(json.loads(ptr.read_text(encoding="utf-8"))["dir"])
            if directory.exists():
                return directory
        except (ValueError, KeyError, OSError):
            pass
    directories = [p for p in Path(watch_dir).glob("*") if p.is_dir()]
    return max(directories, key=lambda p: p.stat().st_mtime) if directories else None


def mark(note: str, proc: str, state_dir: Path, watch_dir: Path,
         title_substr: str | None = None, exe_path=None, target_pid=None) -> dict:
    directory = _active_watch_dir(state_dir, watch_dir) or (Path(watch_dir) / _ts())
    directory.mkdir(parents=True, exist_ok=True)
    hwnd = find_window(proc, title_substr, exe_path=exe_path, pid=target_pid)
    out = directory / f"mark_{_ts()}.png"
    width, height = capture(hwnd, out)
    row = {"t": _dt.datetime.now().isoformat(timespec="microseconds"),
           "file": out.name, "note": note, "hwnd": hwnd, "size": [width, height]}
    with (directory / "marks.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    return {"ok": True, "marked": str(out), "note": note, "dir": str(directory)}


def _read_jsonl(path):
    rows = []
    if Path(path).exists():
        with Path(path).open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    return rows


def watch_events(state_dir, watch_dir, directory=None, *, limit=20, offset=0):
    if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
        raise ValueError("limit 必须为 1–100，offset 必须为非负整数")
    directory = Path(directory) if directory else _active_watch_dir(state_dir, watch_dir)
    if directory is None or not directory.is_dir():
        return {"events": [], "total": 0, "offset": offset, "dir": None}
    files = sorted((directory / "events").glob("*/metadata.json"))
    rows, errors = [], []
    for path in files[offset:offset + limit]:
        try:
            rows.append(json.loads(path.read_text(encoding="utf-8")))
        except (ValueError, OSError) as exc:
            errors.append(f"无法读取 {path.name}：{exc}")
    return {"events": rows, "total": len(files), "offset": offset, "dir": str(directory), "errors": errors}


def watch_report(state_dir: Path, watch_dir: Path, out_dir: Path | None = None) -> dict:
    directory = Path(out_dir) if out_dir else _active_watch_dir(state_dir, watch_dir)
    if directory is None or not directory.exists():
        raise RuntimeError("找不到盯屏会话目录；先跑 watch，或显式指定 out_dir。")
    frames = _read_jsonl(directory / "index.jsonl")
    marks = _read_jsonl(directory / "marks.jsonl")
    suspects = []
    offset = 0
    while True:
        page = watch_events(state_dir, watch_dir, directory, limit=100, offset=offset)
        suspects.extend(page["events"])
        offset += 100
        if offset >= page["total"]:
            break
    lines = [f"复核日志（盯屏会话 {directory.name}）", "=" * 48,
             f"自动留图（画面变化帧）：{len(frames)} 张", f"用户标记（优先复核）：{len(marks)} 处",
             f"自动疑点（不是已确认缺陷）：{len(suspects)} 处",
             "复核顺序：人工标记 → 自动疑点 → 全部画面；无疑点不等于没有缺陷。",
             "疑似日文先排除繁体同形字，须核对原图；OCR 文字不是缺陷证明。",
             "结论栏填法：OK / 待查 / 已记录(编号)", "", "【用户标记帧 · 优先复核】"]
    for row in marks:
        lines.extend([f"{row.get('t', '')}  {row.get('file', '')}", f"备注：{row.get('note') or '—'}", "复核结论：____"])
    if not marks:
        lines.append("（本次无手动标记）")
    lines.extend(["", "【自动疑点 · 待复核】"])
    for row in suspects:
        categories = ", ".join(item["category"] for item in row["findings"])
        lines.extend([f"[{row['sequence']}] {row['captured_at']}  {categories}",
                      f"原图：{row['image']}", f"来源：{row['source']}；待复核", "复核结论：____"])
    lines.extend(["", "【全部画面 · 时间线】"])
    for index, row in enumerate(frames, 1):
        lines.append(f"[{index}] {row.get('t', '')}  {row.get('file', '')}   复核结论：____")
    report = directory / "复核日志.txt"
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"ok": True, "report": str(report), "frames": len(frames), "marks": len(marks), "suspects": len(suspects)}
