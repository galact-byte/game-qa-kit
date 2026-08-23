#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""game-qa-kit 命令行入口：盯屏工作流（人手玩时跑）。

用法（激活 profile 用 --profile 或环境变量 GAMEQAKIT_PROFILE）：
  python -m gameqakit.cli watch --profile example        # 开始盯屏（Ctrl+C 停）
  python -m gameqakit.cli mark  --profile example -m "这里疑似日文"
  python -m gameqakit.cli report --profile example       # 汇总复核日志
  python -m gameqakit.cli capture --profile example -o shot.png [-r x,y,w,h]
  python -m gameqakit.cli profiles                    # 列出可用 profile
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import profile as _profile
from . import win32, watch as _watch


def _resolve(args) -> _profile.GameProfile:
    if getattr(args, "profile", None):
        os.environ["GAMEQAKIT_PROFILE"] = args.profile
    return _profile.active_profile()


def _parse_region(s):
    if not s:
        return None
    return [int(x) for x in s.split(",")]


def main(argv=None):
    ap = argparse.ArgumentParser(prog="gameqakit", description="跨游戏 QA 盯屏/证据工具")
    ap.add_argument("--profile", "-p", help="profile 名字或 json 路径（或设 GAMEQAKIT_PROFILE）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pw = sub.add_parser("watch", help="自动盯屏：边玩边留证，只在画面变化时截图")
    pw.add_argument("--interval", "-n", type=float, default=2.0)
    pw.add_argument("--threshold", "-d", type=int, default=4)
    pw.add_argument("--region", "-r", help="只盯矩形 x,y,w,h")
    pw.add_argument("--downscale", type=int, default=1)
    pw.add_argument("--out", "-o", help="输出目录；缺省自动建 watch/<时间戳>")

    pm = sub.add_parser("mark", help="标记当前画面（截图+备注到本次盯屏会话）")
    pm.add_argument("--note", "-m", default="")

    sub.add_parser("report", help="把盯屏会话汇总为 复核日志.txt")

    pc = sub.add_parser("capture", help="截一张图")
    pc.add_argument("--out", "-o", required=True)
    pc.add_argument("--region", "-r", help="裁剪 x,y,w,h")

    sub.add_parser("profiles", help="列出可用 profile")

    args = ap.parse_args(argv)

    if args.cmd == "profiles":
        print(json.dumps(_profile.list_profiles(), ensure_ascii=False))
        return 0

    p = _resolve(args)

    if args.cmd == "watch":
        from pathlib import Path
        from datetime import datetime
        out = Path(args.out) if args.out else p.watch_dir / datetime.now().strftime("%Y%m%d_%H%M%S")
        _watch.watch(out, p.proc, p.state_dir, interval=args.interval, threshold=args.threshold,
                     region=_parse_region(args.region), title_substr=p.title_substr,
                     downscale=args.downscale if args.downscale > 1 else None)
    elif args.cmd == "mark":
        print(json.dumps(_watch.mark(args.note, p.proc, p.state_dir, p.watch_dir,
                                     title_substr=p.title_substr), ensure_ascii=False))
    elif args.cmd == "report":
        print(json.dumps(_watch.watch_report(p.state_dir, p.watch_dir), ensure_ascii=False))
    elif args.cmd == "capture":
        from pathlib import Path
        hwnd = win32.find_window(p.proc, p.title_substr)
        w, h = win32.capture(hwnd, Path(args.out), region=_parse_region(args.region))
        print(json.dumps({"ok": True, "out": args.out, "size": [w, h]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
