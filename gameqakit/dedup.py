#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""画面去重（dHash 感知哈希）与像素级摘要（引擎无关）。

dHash：缩放 9x8 灰度，比较每行相邻像素得 64 位；对亮度/微噪鲁棒，只关心结构差异。
用途：盯屏时近乎相同的帧不落盘（省盘）；给 LLM 时提示“可跳过不看”（省视觉 token）。
注意：dedupe 只作“是否需重复看”的提示，绝不能当作“画面未变/可跳过错误”的依据。
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
from pathlib import Path


def dhash_from_image(img, hash_size: int = 8) -> int:
    small = img.convert("L").resize((hash_size + 1, hash_size))
    px = list(small.getdata())
    w = hash_size + 1
    bits = 0
    for row in range(hash_size):
        base = row * w
        for col in range(hash_size):
            bits = (bits << 1) | (1 if px[base + col] > px[base + col + 1] else 0)
    return bits


def dhash_from_file(path: Path, hash_size: int = 8) -> int:
    from PIL import Image
    with Image.open(path) as im:
        return dhash_from_image(im, hash_size)


def pixel_digest_from_file(path: Path) -> str:
    """像素内容 SHA-256，用于输入后严格判断画面是否真的改变。"""
    from PIL import Image
    with Image.open(path) as im:
        norm = im.convert("RGBA")
        payload = f"{norm.width}x{norm.height}".encode("ascii") + norm.tobytes()
    return hashlib.sha256(payload).hexdigest()


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def dedupe_frame(path, state_dir: Path, threshold: int = 4) -> dict:
    """算 path 帧 dHash 与上一帧比较，汉明距离<=threshold 视为重复；state 落盘供跨进程比较。"""
    path = Path(path)
    cur = dhash_from_file(path)
    state_dir = Path(state_dir)
    state_path = state_dir / "frame_dedupe.json"
    prev = None
    if state_path.exists():
        try:
            prev = int(json.loads(state_path.read_text(encoding="utf-8")).get("last_hash"))
        except Exception:
            prev = None
    dist = hamming(cur, prev) if prev is not None else None
    dup = (dist is not None and dist <= threshold)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(
        {"last_hash": cur, "last_path": str(path), "t": _dt.datetime.now().isoformat()},
        ensure_ascii=False), encoding="utf-8")
    return {"duplicate": dup, "distance": dist, "hash": f"{cur:016x}",
            "prev_hash": f"{prev:016x}" if prev is not None else None, "threshold": threshold}
