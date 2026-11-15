# Ren'Py 8.3.2 自写工程实验桥；使用现有 v2 毫秒协议。
init -990 python:
    import builtins as _gq_builtins
    import hashlib as _gq_hashlib
    import json as _gq_json
    import math as _gq_math
    import os as _gq_os
    import re as _gq_re
    import sys as _gq_sys
    import threading as _gq_threading
    import time as _gq_time
    import pygame_sdl2 as _gq_pygame

    class _GameQaRenpyBridge(_gq_builtins.object):
        def __init__(self):
            self.directory = _gq_os.environ.get("GAMEQAKIT_BRIDGE_DIR", "")
            self.session = _gq_os.environ.get("GAMEQAKIT_SESSION_ID", "")
            if not _gq_os.path.isabs(self.directory) or not self.session:
                raise Exception("GameQA requires an explicit session and absolute bridge directory")
            if tuple(renpy.version_tuple[:4]) != (8, 3, 2, 24090902):
                raise Exception("GameQA RenPy runtime has not been validated")
            self.pid = _gq_os.getpid()
            self.exe = _gq_os.path.realpath(_gq_sys.executable)
            self.paused = True
            self.pause_id = ""
            self.held = None
            self.active = None
            self.seq = 0
            self.last_observation = 0.0
            self.observation = ""
            self.fault = ""
            self.last_pump = _gq_time.monotonic()
            self.started_drawing = False
            self.keys = dict(up="UP", down="DOWN", left="LEFT", right="RIGHT", ok="RETURN", cancel="ESCAPE")
            self.bound = False
            self.scene_entered = False
            config.label_callbacks.append(self.entered_label)
            self.original_draw_hook = renpy.test.testexecution.execute
            renpy.test.testexecution.execute = self.after_draw
            config.periodic_callbacks.append(self.pump)
            _gq_threading.Thread(target=self.watchdog, name="gameqa-renpy-watchdog", daemon=True).start()

        def entered_label(self, name, abnormal):
            # 引擎自身的图形启动检查先绘制黑场；只在项目入口后开始观察。
            if name in ("main_menu", "start"):
                self.scene_entered = True

        def read(self, name):
            path = _gq_os.path.join(self.directory, name + ".json")
            deadline = _gq_time.monotonic() + .1
            while True:
                try:
                    if not _gq_os.path.exists(path):
                        return {}
                    if _gq_os.path.getsize(path) > 65536:
                        raise Exception("GameQA control file is too large")
                    with open(path, "r", encoding="utf-8") as stream:
                        value = _gq_json.load(stream)
                    break
                except OSError as error:
                    sharing_conflict = (isinstance(error, PermissionError)
                        or getattr(error, "winerror", None) in (5, 32, 33))
                    if not sharing_conflict or _gq_time.monotonic() >= deadline:
                        raise
                    _gq_time.sleep(.005)
            if not isinstance(value, _gq_builtins.dict):
                raise Exception("GameQA control file must be an object")
            return value

        def write(self, name, value):
            path = _gq_os.path.join(self.directory, name + ".json")
            temporary = path + ".part"
            _gq_os.makedirs(_gq_os.path.dirname(path), exist_ok=True)
            with open(temporary, "w", encoding="utf-8") as stream:
                _gq_json.dump(value, stream, allow_nan=False)
            deadline = _gq_time.monotonic() + .1
            while True:
                try:
                    _gq_os.replace(temporary, path)
                    break
                except OSError as error:
                    if getattr(error, "winerror", None) not in (5, 32, 33) or _gq_time.monotonic() >= deadline:
                        raise
                    _gq_time.sleep(.005)

        def validate_binding(self):
            meta = self.read("connection")
            if (meta.get("protocol") != 2 or meta.get("engine") != "renpy" or meta.get("session_id") != self.session
                    or meta.get("pid") != self.pid
                    or _gq_os.path.normcase(_gq_os.path.realpath(meta.get("exe_path", ""))) != _gq_os.path.normcase(self.exe)):
                raise Exception("GameQA connection does not match this process and session")
            self.bound = True

        def status(self):
            return dict(protocol=2, session_id=self.session, pid=self.pid, exe_path=self.exe,
                updated_at=_gq_time.time(), paused=self.paused,
                capabilities=["move", "confirm", "cancel", "wait"] if self.seq and not self.fault else [],
                timing_mode="duration_ms", pause_scope="input", simulation_pause=False,
                observation_seq=self.seq, max_duration_ms=1000, buttons=list(self.keys),
                held=[self.held] if self.held else [], pause_id=self.pause_id,
                observation_screenshot=self.observation, error=self.fault)

        def watchdog(self):
            # 主线程停滞时不能声称已松键；只结束本桥所在的隔离测试进程。
            while True:
                now = _gq_time.monotonic()
                if now - self.last_pump > (1.0 if self.started_drawing else 15.0):
                    _gq_os._exit(4)
                active = self.active
                if (active and active["release_started"] is not None and not active["release_consumed"]
                        and now - active["release_started"] > 1.0):
                    _gq_os._exit(4)
                _gq_time.sleep(.02)

        def queued(self):
            pending = _gq_pygame.event.copy_event_queue()
            pushed = renpy.display.interface.pushed_event
            return any(getattr(event, "test", False) for event in pending + ([pushed] if pushed else []))

        def release(self):
            if self.held and self.active["release_started"] is None:
                self.active["release_started"] = _gq_time.monotonic()
                renpy.test.testkey.up(None, self.keys[self.held])
                self.active["phase"] = "release"

        def fail(self, error):
            self.fault = str(error)
            if self.active:
                self.active["error"] = self.fault
            self.release()
            if not self.held:
                self.paused = True
            self.write("status", self.status())

        def lease_valid(self):
            lease = self.read("lease")
            expires = lease.get("expires_at")
            return (lease.get("session_id") == self.session and type(expires) in (int, float)
                and _gq_math.isfinite(expires) and 0 < expires - _gq_time.time() <= 2)

        def accept(self, request):
            ident = request.get("id", "")
            if not isinstance(ident, str) or not _gq_re.fullmatch(r"[A-Za-z0-9_-]{1,64}", ident):
                raise Exception("Invalid GameQA command id")
            if _gq_os.path.exists(_gq_os.path.join(self.directory, "results", ident + ".json")):
                return
            payload = request.get("payload", {})
            action = payload.get("action")
            fields = {"move": {"action", "direction", "duration_ms"},
                "confirm": {"action", "duration_ms"}, "cancel": {"action", "duration_ms"},
                "wait": {"action", "duration_ms"}, "resume": {"action"}}
            if (request.get("protocol") != 2 or request.get("session_id") != self.session
                    or request.get("pause_id", "") != self.pause_id or self.fault
                    or type(request.get("after_observation_seq")) is not int
                    or not 0 <= request["after_observation_seq"] <= self.seq
                    or action not in fields or set(payload) != fields[action]):
                raise Exception("Invalid GameQA request, pause generation or action fields")
            duration = payload.get("duration_ms", 0)
            if action != "resume" and (type(duration) is not int or not 1 <= duration <= 1000 or self.paused):
                raise Exception("Invalid duration or control is paused")
            key = payload.get("direction") if action == "move" else "ok" if action == "confirm" else "cancel" if action == "cancel" else None
            if action == "move" and key not in ("up", "down", "left", "right"):
                raise Exception("Invalid movement direction")
            if not self.lease_valid():
                raise Exception("GameQA lease is missing or expired")
            now = _gq_time.monotonic()
            self.active = _gq_builtins.dict(request=request, phase="press" if key else "wait", key=key,
                started=int(now * 1000), until=now + duration / 1000.0,
                press_consumed=False, release_consumed=False, release_started=None, error="")
            if action == "resume":
                self.paused = False
            if key:
                self.held = key
                renpy.test.testkey.down(None, self.keys[key])

        def pump(self):
            self.last_pump = _gq_time.monotonic()
            try:
                if not self.bound:
                    self.validate_binding()
                pause = self.read("pause")
                changed = pause.get("session_id") == self.session and pause.get("id", "") != self.pause_id
                if changed:
                    self.pause_id = pause["id"]
                    if self.active:
                        self.active["error"] = "GameQA action interrupted by pause"
                    if self.held:
                        self.release()
                    else:
                        self.paused = True
                if not self.paused and not self.lease_valid():
                    if self.active:
                        self.active["error"] = "GameQA lease expired"
                    if self.held:
                        if self.active["phase"] != "release":
                            self.release()
                    else:
                        self.paused = True
                if self.active and self.held:
                    if self.active["phase"] == "hold" and self.last_pump >= self.active["until"]:
                        self.release()
                if not self.active and not self.fault and self.seq:
                    request = self.read("request")
                    if request:
                        self.accept(request)
                if self.scene_entered:
                    self.write("status", self.status())
                renpy.display.interface.force_redraw = True
            except Exception as error:
                self.fail(error)

        def capture(self, ident):
            surface = renpy.display.draw.screenshot(renpy.display.interface.surftree)
            width, height = surface.get_size()
            # 保留既有的全画面黑白判据；不以单个亮点放行空白观察。
            dark = light = count = 0
            for y in range(1, height - 1):
                for x in range(1, width - 1):
                    color = surface.get_at((x, y))
                    dark += max(color[:3]) <= 8
                    light += min(color[:3]) >= 247
                    count += 1
            if not count or max(dark, light) / float(count) >= .999:
                raise Exception("GameQA rejected a blank observation")
            relative = "screenshots/" + ident + ".png"
            path = _gq_os.path.join(self.directory, relative)
            temporary = path + ".part.png"
            _gq_os.makedirs(_gq_os.path.dirname(path), exist_ok=True)
            _gq_pygame.image.save(surface, temporary)
            with open(temporary, "rb") as stream:
                digest = _gq_hashlib.sha256(stream.read()).hexdigest()
            _gq_os.replace(temporary, path)
            self.seq += 1
            self.last_observation = _gq_time.monotonic()
            self.observation = relative
            return digest

        def after_draw(self):
            self.original_draw_hook()
            self.started_drawing = True
            self.last_pump = _gq_time.monotonic()
            if not self.scene_entered:
                return
            try:
                if not self.bound:
                    self.validate_binding()
                active = self.active
                if active and not self.queued():
                    if active["phase"] == "press":
                        active["press_consumed"] = True
                        active["phase"] = "hold"
                        active["until"] = self.last_pump + active["request"]["payload"]["duration_ms"] / 1000.0
                    elif active["phase"] == "release":
                        active["release_consumed"] = True
                        self.held = None
                        if active["error"]:
                            self.paused = True
                ready = active and ((active["phase"] == "wait" and self.last_pump >= active["until"])
                    or active["release_consumed"])
                if ready:
                    request = active["request"]
                    result = dict(protocol=2, session_id=self.session, id=request["id"],
                        ok=not bool(active["error"]), error=active["error"],
                        timing_mode="duration_ms", pause_scope="input", simulation_pause=False,
                        pause_id=request.get("pause_id", ""), consumed=active["press_consumed"],
                        press_consumed=active["press_consumed"], release_consumed=active["release_consumed"],
                        held=[], started_monotonic_ms=active["started"], completed_monotonic_ms=int(self.last_pump * 1000))
                    if result["ok"]:
                        try:
                            result["screenshot_sha256"] = self.capture(request["id"])
                            result["screenshot"] = self.observation
                        except Exception as error:
                            result.update(ok=False, error=str(error))
                            self.fault = str(error)
                            self.paused = True
                    result["observation_seq"] = self.seq
                    self.write("status", self.status())
                    self.write("results/" + request["id"], result)
                    self.active = None
                elif not self.fault and _gq_time.monotonic() - self.last_observation >= .2:
                    self.capture("observation")
                self.write("status", self.status())
                renpy.display.interface.force_redraw = True
            except Exception as error:
                self.fail(error)

    # 会话与线程属于进程，不进入游戏的 rollback/save store。
    renpy._gameqa_bridge = _GameQaRenpyBridge()
