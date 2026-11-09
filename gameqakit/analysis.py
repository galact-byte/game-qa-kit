"""有界异步分析与疑点证据。筛选结果始终是待复核，不是缺陷结论。"""
from __future__ import annotations

import hashlib
import json
import queue
import re
import threading
import time
from pathlib import Path

from .sessions import write_json

RULES = (
    ("NETWORK_TIMEOUT", r"\b(?:NETWORK[_ -]?TIMEOUT|ETIMEDOUT|TIMEOUTERROR)\b|请求超时|连接超时|网络超时"),
    ("FILE_NOT_FOUND", r"\b(?:FILE[_ -]?NOT[_ -]?FOUND|FILENOTFOUNDERROR|ENOENT)\b|文件不存在|找不到文件"),
    ("ACCESS_DENIED", r"\b(?:ACCESS[_ -]?DENIED|PERMISSIONERROR|EACCES)\b|访问被拒绝|权限不足"),
    ("OUT_OF_MEMORY", r"\b(?:OUT[_ -]?OF[_ -]?MEMORY|MEMORYERROR|CUDA OUT OF MEMORY)\b|内存不足|显存不足"),
    ("APP_ERROR", r"\b(?:FATAL ERROR|UNHANDLED EXCEPTION|ASSERTIONERROR|SEGMENTATION FAULT)\b|程序已停止工作|应用程序错误|渲染失败"),
    ("PYTHON_TRACEBACK", r"Traceback \(most recent call last\)"),
    ("POSSIBLE_JAPANESE", r"[\u3041-\u3096\u30a1-\u30fa][\u3041-\u3096\u30a1-\u30faー]{1,}"),
    ("POSSIBLE_ENCODING", r"\ufffd"),
)
COMPILED = [(name, re.compile(pattern, re.I)) for name, pattern in RULES]


def classify(text):
    text = (text or "")[:16000]
    return [{"category": name, "evidence": match.group(0)}
            for name, pattern in COMPILED if (match := pattern.search(text))]


def load_ocr():
    """可选包自带本地模型；不安装依赖、不自动下载模型。"""
    try:
        from rapidocr_onnxruntime import RapidOCR
        import numpy as np
    except ImportError as exc:
        raise ImportError("未安装可选 OCR：需要 rapidocr_onnxruntime；仍保留截图和窗口文字筛选") from exc
    engine = RapidOCR()

    def read(image):
        rows, _ = engine(np.asarray(image.convert("RGB"))[:, :, ::-1].copy())
        return "\n".join(row[1] for row in (rows or []))
    return read


class EventStore:
    def __init__(self, out):
        self.out = Path(out)
        self.active = {}
        self.count = 0

    def inspect(self, task, ocr_text="", source="Win32"):
        text = task["native_text"] + "\n" + ocr_text
        findings = classify(text)
        hwnd = task["hwnd"]
        if not findings:
            self.active.pop(hwnd, None)
            return None
        fingerprint = hashlib.sha256(re.sub(r"\s+", " ", text).strip().encode("utf-8")).hexdigest()
        if self.active.get(hwnd) == fingerprint:
            return None
        sequence = self.count + 1
        folder = self.out / "events" / f"{sequence:04d}_{findings[0]['category']}"
        folder.mkdir(parents=True, exist_ok=False)
        raw = folder / "raw.png"
        try:
            task["image"].save(raw, "PNG")
            metadata = {key: task[key] for key in ("hwnd", "captured_at", "capture_region", "native_text")}
            metadata.update(sequence=sequence, findings=findings, source=source,
                            ocr_text=ocr_text[:16000], verdict="待复核", image=str(raw.resolve()),
                            image_size=list(task["image"].size),
                            note="屏幕内容筛选，不证明应用内部异常或汉化缺陷。")
            write_json(folder / "metadata.json", metadata)
            with (self.out / "events.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(metadata, ensure_ascii=False) + "\n")
        except Exception:
            # 留下失败证据，但下一次必须可以换新目录重试。
            self.count = sequence
            raise
        self.count = sequence
        self.active[hwnd] = fingerprint
        return metadata


class FrameAnalyzer:
    def __init__(self, out, mode, update, notify):
        self.mode, self.update, self.notify = mode, update, notify
        self.store = EventStore(out)
        self.frames = queue.Queue(maxsize=4)
        self.stopping = threading.Event()
        self.retry = threading.Event()
        self.thread = threading.Thread(target=self._run, name="GameQA-Analysis", daemon=False)
        self.thread.start()

    def submit(self, task):
        try:
            self.frames.put_nowait(task)
        except queue.Full:
            try:
                self.frames.get_nowait()
                self.update(dropped=1)
            except queue.Empty:
                pass
            self.frames.put_nowait(task)

    def close(self):
        self.stopping.set()
        self.thread.join()

    def _run(self):
        ocr = None
        if self.mode == "ocr":
            try:
                ocr = load_ocr()
                self.update(analysis_status="ready")
            except Exception as exc:
                self.update(analysis_status="degraded", analysis_warning=str(exc))
        else:
            self.update(analysis_status="ready")
        while not self.stopping.is_set() or not self.frames.empty():
            try:
                task = self.frames.get(timeout=0.05)
            except queue.Empty:
                continue
            started = time.perf_counter()
            try:
                text = ocr(task["image"]) if ocr else ""
                item = self.store.inspect(task, text, "Win32+OCR" if ocr else "Win32")
                self.update(analyzed=1, suspects=1 if item else 0)
                if item:
                    self.notify({"kind": "suspect", **item})
            except Exception as exc:
                self.retry.set()
                self.update(analysis_errors=1, last_error=f"分析或归档失败：{exc}")
            finally:
                self.update(analysis_ms=round((time.perf_counter() - started) * 1000, 2))
