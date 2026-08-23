#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""game-qa-kit 的 MCP 封装（零依赖，手写 JSON-RPC 2.0 over stdio）。

按激活 profile（环境变量 GAMEQAKIT_PROFILE=<名字或json路径>）暴露引擎无关的
截图/盯屏/存档/输入能力。stdout 只走协议消息，日志走 stderr。

注册到 ~/.pi/agent/mcp.json：
  "gameqakit": {"command": "python", "args": ["<绝对路径>/mcp_server.py"],
                "env": {"GAMEQAKIT_PROFILE": "example"}}
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

for _s, _kw in ((sys.stdout, {"newline": "\n"}), (sys.stdin, {}), (sys.stderr, {})):
    try:
        _s.reconfigure(encoding="utf-8", **_kw)
    except (AttributeError, ValueError):
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gameqakit import profile as _profile          # noqa: E402
from gameqakit import win32, dedup, watch as _watch, saves as _saves, inputs  # noqa: E402

SERVER_NAME = "gameqakit"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2024-11-05"

_PROFILE = None


def P():
    """惰性加载激活 profile，缺失时给清晰报错。"""
    global _PROFILE
    if _PROFILE is None:
        _PROFILE = _profile.active_profile()
    return _PROFILE


def _obj(props: dict, required=None) -> dict:
    return {"type": "object", "properties": props, "required": required or []}


def _region(v, what="region"):
    if v is None:
        return None
    if not (isinstance(v, (list, tuple)) and len(v) == 4):
        raise ValueError(f"{what} 需为 [x,y,w,h] 四元数组")
    return tuple(int(x) for x in v)


def _ts():
    import datetime as d
    return d.datetime.now().strftime("%Y%m%d_%H%M%S")


# ---------- handlers ----------
def t_profile(_a):
    p = P()
    return {"active": p.name, "proc": p.proc, "data_dir": str(p.data_path),
            "savedata": str(p.savedata_path) if p.savedata_path else None,
            "save_globs": p.save_globs, "regions": p.regions,
            "available": _profile.list_profiles()}


def t_win(_a):
    p = P()
    return win32.window_info(win32.find_window(p.proc, p.title_substr))


def t_capture(a):
    p = P()
    hwnd = win32.find_window(p.proc, p.title_substr)
    out = Path(a["out"]) if a.get("out") else p.shots_dir / f"shot_{_ts()}.png"
    region = a.get("region")
    if isinstance(region, str):                       # 支持命名区域
        region = p.regions.get(region)
    region = _region(region)
    if region:
        cw, ch = win32.client_size(hwnd)
        region = win32.clamp_region(region, cw, ch)
    w, h = win32.capture(hwnd, out, region=region)
    res = {"ok": True, "hwnd": hwnd, "size": [w, h], "out": str(out),
           "capture_region": list(region) if region else [0, 0, w, h]}
    if a.get("dedupe"):
        res["dedupe"] = dedup.dedupe_frame(out, p.state_dir)
    return res


def t_watch_mark(a):
    p = P()
    return _watch.mark(a.get("note", ""), p.proc, p.state_dir, p.watch_dir)


def t_watch_report(a):
    p = P()
    return _watch.watch_report(p.state_dir, p.watch_dir, a.get("dir"))


def t_click(a):
    p = P()
    cap = _region(a.get("capture_region"), "capture_region")
    return inputs.click(int(a["cx"]), int(a["cy"]), p.proc,
                        hold_ms=int(a.get("hold_ms", 90)),
                        verify_region=_region(a.get("verify_region"), "verify_region"),
                        origin=(cap[0], cap[1]) if cap else None,
                        guard_ms=int(a.get("guard_ms", 1200)), force=bool(a.get("force", False)))


def t_key(a):
    p = P()
    vk = a["vk"]
    vk = int(vk, 0) if isinstance(vk, str) else int(vk)
    return inputs.press_key(vk, p.proc,
                            verify_region=_region(a.get("verify_region"), "verify_region"),
                            guard_ms=int(a.get("guard_ms", 1200)), force=bool(a.get("force", False)))


def _savedata(p):
    if not p.savedata_path:
        raise ValueError(f"profile {p.name} 未配置 savedata_dir，无法操作存档")
    return p.savedata_path


def t_save(a):
    p = P()
    dest = _saves.snapshot_save(a["name"], _savedata(p), p.save_globs, p.saves_dir,
                                note=a.get("note", ""))
    return {"ok": True, "snapshot": str(dest)}


def t_load(a):
    p = P()
    running = _saves.is_game_running(p.proc)
    src = _saves.restore_save(a["name"], _savedata(p), p.save_globs, p.saves_dir)
    res = {"ok": True, "restored_from": str(src), "into": str(_savedata(p)), "game_running": running}
    if running:
        res["warning"] = ("游戏进程仍在运行！运行中不重读磁盘，恢复不生效、屏上仍是旧内存状态。"
                          "正确：kill_game→重启 exe→游戏内 AUTO LOAD→截图核对。")
    return res


def t_list_saves(_a):
    return {"saves": _saves.list_saves(P().saves_dir)}


def t_kill_game(_a):
    return _saves.kill_game(P().proc)


PROFILE_NOTE = "按激活 profile 操作（GAMEQAKIT_PROFILE）。"
CONFIRM = {"confirm": {"type": "boolean", "description": "危险操作必须显式传 true 才执行"}}
DANGEROUS_TOOLS = {"click", "key", "load", "kill_game"}

TOOLS = {
    "profile": ("显示当前激活 profile 与可用 profile 列表。", _obj({}), t_profile),
    "win": ("游戏窗口信息（hwnd/客户区/是否被遮挡）。" + PROFILE_NOTE, _obj({}), t_win),
    "capture": ("后台截图（不抢前台）。region 传 [x,y,w,h] 或 profile 里的命名区域字符串；dedupe=true 提示是否与上帧重复。",
                _obj({"out": {"type": "string"}, "region": {"type": ["array", "string"]},
                      "dedupe": {"type": "boolean"}}), t_capture),
    "mark": ("盯屏时随手标记当前画面（截图+备注到本次会话），复核优先看。", _obj({"note": {"type": "string"}}), t_watch_mark),
    "watch_report": ("把盯屏会话汇总为 复核日志.txt。", _obj({"dir": {"type": "string"}}), t_watch_report),
    "click": ("点击客户区坐标(cx,cy)。已在前台走快路，否则置顶+轮询；返回 foreground_confirmed（=是否真点到）。"
              "默认带抢鼠标护栏（用户在操作则跳过，force=true 强制）；capture_region=[x,y,w,h] 支持裁剪图相对坐标。需 confirm=true。",
              _obj({**CONFIRM, "cx": {"type": "integer"}, "cy": {"type": "integer"},
                    "hold_ms": {"type": "integer"}, "capture_region": {"type": "array"},
                    "guard_ms": {"type": "integer"}, "force": {"type": "boolean"},
                    "verify_region": {"type": "array"}}, ["cx", "cy"]), t_click),
    "key": ("发一个虚拟键码（13=Enter,27=Esc,32=Space）。同 click 的护栏/快路/确认语义。需 confirm=true。",
            _obj({**CONFIRM, "vk": {"type": ["integer", "string"]}, "guard_ms": {"type": "integer"},
                  "force": {"type": "boolean"}, "verify_region": {"type": "array"}}, ["vk"]), t_key),
    "save": ("快照当前存档到 QA 数据目录（含 SHA256）。先游戏内存盘再快照。", _obj({"name": {"type": "string"},
             "note": {"type": "string"}}, ["name"]), t_save),
    "load": ("从快照恢复存档（校 SHA-256，恢复前自动备份）。⚠ 游戏在跑时恢复不生效(返回带warning)。需 confirm=true。",
             _obj({**CONFIRM, "name": {"type": "string"}}, ["name"]), t_load),
    "list_saves": ("列出所有存档快照。", _obj({}), t_list_saves),
    "kill_game": ("可靠终止游戏进程（改档前必先游戏内存盘）。需 confirm=true。", _obj({**CONFIRM}), t_kill_game),
}


def _tool_list():
    return [{"name": n, "description": d, "inputSchema": s} for n, (d, s, _) in TOOLS.items()]


def handle(method, params):
    if method == "initialize":
        return {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION}}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": _tool_list()}
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if name not in TOOLS:
            raise ValueError(f"未知工具: {name}")
        if name in DANGEROUS_TOOLS and args.get("confirm") is not True:
            raise ValueError(f"危险操作 {name} 需显式确认：请加 confirm=true（会改存档或向游戏发送输入）。")
        result = TOOLS[name][2](args)
        return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}]}
    raise ValueError(f"未支持的方法: {method}")


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        if not isinstance(msg, dict):
            continue
        mid = msg.get("id")
        if mid is None:
            continue
        try:
            resp = {"jsonrpc": "2.0", "id": mid, "result": handle(msg.get("method", ""), msg.get("params") or {})}
        except Exception as e:
            print(traceback.format_exc(), file=sys.stderr, flush=True)
            resp = {"jsonrpc": "2.0", "id": mid, "error": {"code": -32000, "message": str(e)}}
        sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
