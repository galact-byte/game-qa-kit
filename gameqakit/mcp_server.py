#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""game-qa-kit 的 MCP 封装（零依赖，手写 JSON-RPC 2.0 over stdio）。

按激活 profile（环境变量 GAMEQAKIT_PROFILE=<名字或json路径>）暴露引擎无关的
截图/盯屏/存档/输入能力。stdout 只走协议消息，日志走 stderr。

注册到 ~/.pi/agent/mcp.json：
  "gameqakit": {"command": "python", "args": ["<绝对路径>/mcp_server.py"],
                "env": {"GAMEQAKIT_PROFILE": "<profile 名>"}}
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
from gameqakit import win32, dedup, watch as _watch, saves as _saves, inputs, sessions  # noqa: E402

SERVER_NAME = "gameqakit"
SERVER_VERSION = "0.2.1"
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
    return {"active": p.name, "proc": p.proc, "exe_path": p.exe_path,
            "target_pid": win32.profile_target(p)["pid"], "data_dir": str(p.data_path),
            "savedata": str(p.savedata_path) if p.savedata_path else None,
            "save_globs": p.save_globs, "regions": p.regions,
            "available": _profile.list_profiles()}


def t_win(_a):
    p = P()
    return win32.window_info(win32.find_profile_window(p))


def t_capture(a):
    p = P()
    hwnd = win32.find_profile_window(p)
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
    return _watch.mark(a.get("note", ""), p.proc, p.state_dir, p.watch_dir,
                       title_substr=p.title_substr, exe_path=p.exe_path,
                       target_pid=win32.profile_target(p)["pid"])


def t_watch_report(a):
    p = P()
    return _watch.watch_report(p.state_dir, p.watch_dir, a.get("dir"))


def t_watch_start(a):
    p = P()
    region = a.get("region")
    if isinstance(region, str):
        if region not in p.regions:
            raise ValueError(f"未定义的截图区域：{region}")
        region = p.regions[region]
    return sessions.start_watch(p, interval=a.get("interval", 2.0),
                                threshold=a.get("threshold", 4), region=region,
                                analysis_mode=a.get("analysis_mode", "rules"))


def t_watch_stop(_a):
    return sessions.stop_watch(P())


def t_watch_status(_a):
    return sessions.watch_status(P())


def t_watch_events(a):
    p = P()
    return _watch.watch_events(p.state_dir, p.watch_dir, a.get("dir"),
                               limit=a.get("limit", 20), offset=a.get("offset", 0))


def t_click(a):
    p = P()
    cap = _region(a.get("capture_region"), "capture_region")
    return inputs.click(int(a["cx"]), int(a["cy"]), p.proc,
                        hold_ms=int(a.get("hold_ms", 90)),
                        verify_region=_region(a.get("verify_region"), "verify_region"),
                        origin=(cap[0], cap[1]) if cap else None,
                        guard_ms=int(a.get("guard_ms", 1200)), force=bool(a.get("force", False)),
                        hwnd=win32.find_profile_window(p))


def t_key(a):
    p = P()
    vk = a["vk"]
    vk = int(vk, 0) if isinstance(vk, str) else int(vk)
    return inputs.press_key(vk, p.proc,
                            verify_region=_region(a.get("verify_region"), "verify_region"),
                            guard_ms=int(a.get("guard_ms", 1200)), force=bool(a.get("force", False)),
                            hwnd=win32.find_profile_window(p))


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
    p = P()
    if p.exe_path:
        return _saves.kill_game(p.proc, pid=win32.window_pid(win32.find_profile_window(p)))
    return _saves.kill_game(p.proc)


def t_processes(_a):
    return {"processes": win32.list_process_targets()}


def t_engine_inspect(a):
    from gameqakit.engines import inspect_game
    path = a.get("path") or P().exe_path
    if not path:
        raise ValueError("请指定游戏目录或先选择精确程序路径")
    return inspect_game(path)


def _bridge(a):
    from gameqakit.control import BridgeClient
    p = P()
    return BridgeClient(a.get("directory") or p.state_dir / "control",
                        pid=p.target_pid, exe_path=p.exe_path)


def t_control_status(a):
    return _bridge(a).status()


def t_control_action(a):
    options = {key: a[key] for key in ("frames", "direction", "button", "x", "y") if key in a}
    return _bridge(a).action(a["action"], command_id=a.get("command_id"), timeout=a.get("timeout", 5), **options)


def t_control_result(a):
    return _bridge(a).result(a["command_id"])


def t_control_pause(a):
    return _bridge(a).pause()


def t_control_resume(a):
    return _bridge(a).resume(command_id=a.get("command_id"), timeout=a.get("timeout", 5))


PROFILE_NOTE = "按激活 profile 操作（GAMEQAKIT_PROFILE）。"
CONFIRM = {"confirm": {"type": "boolean", "description": "危险操作必须显式传 true 才执行"}}
DANGEROUS_TOOLS = {"click", "key", "load", "kill_game", "control_action", "control_resume"}
BRIDGE_FIELDS = {"directory": {"type": "string", "description": "插件会话目录；默认 profile 的 state/control，不是游戏目录"}}
COMMAND_FIELDS = {**BRIDGE_FIELDS, "command_id": {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,64}$"},
                  "timeout": {"type": "number", "minimum": 0.05, "maximum": 15},
                  "include_image": {"type": "boolean", "description": "回传已校验的操作后 PNG；过大时仅返回归档路径"}}

TOOLS = {
    "profile": ("显示当前激活 profile、程序路径、会话目标 PID 与可用 profile 列表。", _obj({}), t_profile),
    "processes": ("只读列出有可见窗口的程序名、PID、窗口标题、程序路径和尺寸，供选择游戏目标；不截图、不控制。", _obj({}), t_processes),
    "engine_inspect": ("只读按目录证据识别引擎；识别不代表已支持控制，不修改游戏。", _obj({"path": {"type": "string"}}), t_engine_inspect),
    "control_status": ("查询已安装插件的身份、逻辑帧、场景、实际能力与暂停状态；不发送输入，无插件不降级。", _obj(BRIDGE_FIELDS), t_control_status),
    "control_action": ("统一后台动作，按插件能力执行并返回消费状态、新帧、场景与截图；不使用系统键鼠。"
                       "需 confirm=true。超时先查 command_id，禁止换编号盲目重发。press 也是限帧按键，不是无限长按。",
                       _obj({**CONFIRM, **COMMAND_FIELDS, "action": {"type": "string", "enum": ["move", "confirm", "cancel", "wait", "press", "release", "click"]},
                             "frames": {"type": "integer", "minimum": 1, "maximum": 120},
                             "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
                             "button": {"type": "string"}, "x": {"type": "integer"}, "y": {"type": "integer"}}, ["action"]), t_control_action),
    "control_result": ("按命令编号查询回执，不重发动作；historical=true 的旧会话证据不能指导当前操作。",
                       _obj({**BRIDGE_FIELDS, "command_id": {"type": "string"}, "include_image": {"type": "boolean"}}, ["command_id"]), t_control_result),
    "control_pause": ("优先请求暂停 AI 并释放按键，不等待普通动作锁；control_status 确认 paused 且 held 为空才算接管完成。", _obj(BRIDGE_FIELDS), t_control_pause),
    "control_resume": ("明确恢复已确认的插件控制；不会恢复暂停前的旧队列。需 confirm=true。", _obj({**CONFIRM, **COMMAND_FIELDS}), t_control_resume),
    "win": ("游戏窗口信息（hwnd/客户区/是否被遮挡）。" + PROFILE_NOTE, _obj({}), t_win),
    "capture": ("后台截图（不抢前台）。region 传 [x,y,w,h] 或 profile 里的命名区域字符串；dedupe=true 提示是否与上帧重复。",
                _obj({"out": {"type": "string"}, "region": {"type": ["array", "string"]},
                      "dedupe": {"type": "boolean"}}), t_capture),
    "mark": ("盯屏时随手标记当前画面（截图+备注到本次会话），复核优先看。", _obj({"note": {"type": "string"}}), t_watch_mark),
    "watch_report": ("汇总人工标记、自动疑点及完整留图时间线到复核日志.txt；疑点不是已确认缺陷。", _obj({"dir": {"type": "string"}}), t_watch_report),
    "watch_start": ("启动独立后台盯屏，与 GUI 共用会话；重复启动返回现有会话。不发送输入、不抢前台。"
                    "analysis_mode=rules 只筛选窗口标题；ocr 需要可选本地 OCR，缺失时明确降级；off 只留图。",
                    _obj({"interval": {"type": "number", "exclusiveMinimum": 0},
                          "threshold": {"type": "integer", "minimum": 0, "maximum": 64},
                          "region": {"type": ["array", "string"], "items": {"type": "integer"}},
                          "analysis_mode": {"type": "string", "enum": ["off", "rules", "ocr"]}}), t_watch_start),
    "watch_stop": ("请求停止当前 profile 的盯屏，不阻塞等待。watch_status 显示 running=false 才算停止，已有证据保留。", _obj({}), t_watch_stop),
    "watch_status": ("查询共享盯屏状态、采集/留图/分析/疑点/分析丢帧/积压/错误及 OCR 降级；不截图。", _obj({}), t_watch_status),
    "watch_events": ("分页读取自动疑点证据（含原图路径、文字和分类依据）。均待复核，无疑点不代表无缺陷。",
                     _obj({"dir": {"type": "string"},
                           "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                           "offset": {"type": "integer", "minimum": 0}}), t_watch_events),
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
        content = []
        if (name in {"control_action", "control_resume", "control_result"}
                and args.get("include_image") is True and result.get("status") == "done"):
            import base64
            shot = Path(result["screenshot"])
            if shot.stat().st_size <= 1024 * 1024:
                content.append({"type": "image", "mimeType": "image/png",
                                "data": base64.b64encode(shot.read_bytes()).decode("ascii")})
            else:
                result["image_omitted"] = "截图超过内联上限，请按 screenshot 路径读取归档图"
        return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}, *content]}
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
