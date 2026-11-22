"""Run only the registered authored Godot scene with creator-owned lifetime."""
import os
from pathlib import Path
import sys
import shutil
import time
import uuid

from gameqakit.admission import no_links, revalidate_plan
from gameqakit.admission_runtime import validate_ready
from gameqakit.control import BridgeClient, write_connection
from gameqakit.process_identity import _pid_path
from gameqakit.sessions import read_json, write_json
from . import prepare as recipe


class GodotSession:
    def __init__(self, plan):
        self.plan = plan
        self.root = Path(plan["destination"])
        self.runtime = self.root / "runtime"
        self.host = self.bridge = self.focus = None
        self.baseline = {**plan["source_digests"], **plan["artifact_digests"]}
        self.prepared = False

    def _verify_runtime(self):
        no_links(self.runtime)
        if {item.name for item in self.runtime.iterdir()} != self.baseline.keys():
            raise ValueError("runtime_changed: unexpected Godot files")
        for name, digest in self.baseline.items():
            recipe.checked_digest(self.runtime / name, digest)

    def prepare(self):
        revalidate_plan(self.plan)
        self.root.mkdir(exist_ok=False)
        for name in ("runtime", "bridge", "data", "appdata", "localappdata", "tmp"):
            (self.root / name).mkdir()
        for name in self.baseline:
            file = (Path(self.plan['source']) / name if name == recipe.PACK else
                    recipe.BRIDGE if name == recipe.BRIDGE.name else Path(self.plan['artifacts']) / name)
            no_links(file)
            shutil.copyfile(file, self.runtime / file.name)
            if sys.platform == 'darwin' and name == recipe.component()['runtime']:
                (self.runtime / name).chmod(0o755)
        self._verify_runtime()
        self.prepared = True

    def start(self, cancelled=lambda: False):
        if not self.prepared or self.host is not None:
            raise RuntimeError("启动前须准备，且不能重复启动")
        if (recipe.source_digests(self.plan["source"]) != self.plan["source_digests"]
                or recipe.artifact_digests(self.plan["artifacts"]) != self.plan["artifact_digests"]):
            raise RuntimeError("Godot 原样本或组件已变化")
        self._verify_runtime()
        directory = self.root / "bridge"
        for path in (directory, self.root / "appdata", self.root / "localappdata", self.root / "tmp"):
            no_links(path)
            if any(path.iterdir()):
                raise ValueError(f"启动前目录须为空：{path.name}")
        for path in (self.root / "godot.log", self.root / "fixture-state.json", self.root / "fixture-state.json.part",
                     self.root / "fixture-state.json.blank"):
            no_links(path)
            if path.exists():
                raise ValueError(f"启动前文件已存在：{path.name}")
        if cancelled():
            raise InterruptedError("接入已取消")
        mac = sys.platform == 'darwin'
        executable = self.runtime / recipe.component()['runtime']
        session = uuid.uuid4().hex
        if mac:
            from gameqakit.macos_process import MacOSProcess as Host
        else:
            from gameqakit.desktop import DesktopProcess as Host
        command = [str(executable), "--main-pack", str(self.runtime / recipe.PACK),
            "--script", str(self.runtime / recipe.BRIDGE.name), "--audio-driver", "Dummy", "--max-fps", "60",
            "--log-file", str(self.root / "godot.log")]
        if mac:
            command += ['--rendering-driver', 'opengl3', '--', '--qa-no-focus']
        self.host = Host(command, cwd=self.runtime, env={
                "GAMEQAKIT_BRIDGE_DIR": directory.as_posix(), "GAMEQAKIT_SESSION_ID": session,
                "GAMEQA_FIXTURE_STATE": (self.root / "fixture-state.json").as_posix(),
                "APPDATA": str(self.root / "appdata"), "LOCALAPPDATA": str(self.root / "localappdata"),
                "TEMP": str(self.root / "tmp"), "TMP": str(self.root / "tmp"),
                **({'HOME': str(self.root / 'appdata'), 'TMPDIR': str(self.root / 'tmp')} if mac else {})})
        self.host.__enter__()
        if mac:
            from gameqakit.macos_focus import FocusMonitor
            self.focus = FocusMonitor(directory, self.host.pid, self.host.started_at)
        write_connection(directory, {"protocol": 1, "session_id": session, "pid": self.host.pid,
                                     "exe_path": str(executable), "engine": "godot",
                                     **({'started_at': self.host.started_at} if mac else {})})
        self.bridge = BridgeClient(directory, pid=self.host.pid, exe_path=str(executable))
        self.host.run()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.focus:
                self.focus.check()
            if cancelled():
                raise InterruptedError("接入已取消")
            if not self.host.alive():
                raise RuntimeError("target_exited_before_ready")
            state = self.bridge.status()
            if state.get("connected") and state.get("error"):
                raise RuntimeError(state["error"])
            if state.get("input_ready"):
                image = validate_ready(self.root, state)
                expected = (self.root / 'appdata/Library/Application Support/gameqa-godot-fixture'
                            if mac else self.root / 'appdata/gameqa-godot-fixture')
                if Path(state.get("user_data_dir", "")).resolve() != expected.resolve():
                    raise RuntimeError("Godot 用户目录未落在会话内")
                return {**state, **image, "state": "ready_paused", "bridge_directory": str(directory),
                        "host_pid": self.host.pid,
                        **({'host_supervisor_pid': self.host.process.pid,
                            'focus_monitor_pid': self.focus.process.pid} if mac else {})}
            time.sleep(.02)
        raise TimeoutError("startup_timeout：Godot 未取得有效首图")

    def stop(self):
        release = release_error = None
        pid = self.host.pid if self.host else None
        try:
            if self.bridge:
                release = False
                state = self.bridge.status()
                if state.get("connected"):
                    self.bridge.pause()
                    pause_id = read_json(self.root / "bridge/pause.json")["id"]
                    deadline = time.monotonic() + 3
                    while time.monotonic() < deadline:
                        state = self.bridge.status()
                        if (state.get("connected") and state.get("pause_id") == pause_id
                                and state.get("paused") is True and state.get("held") == []):
                            release = True
                            break
                        if not self.host.alive():
                            break
                        time.sleep(.02)
                if not release:
                    release_error = state.get("error") or "pause_not_acknowledged"
        except (OSError, ValueError, RuntimeError) as exc:
            release, release_error = False, str(exc)
        finally:
            try:
                if self.host:
                    self.host.close()
            finally:
                if self.focus:
                    try:
                        self.focus.check()
                    finally:
                        self.focus.close()
        exited = not pid or not _pid_path(pid)
        checks = {}
        for key, field, expected, digest in (
            ("source_unchanged", "source", "source_digests", recipe.source_digests),
            ("runtime_source_unchanged", "artifacts", "artifact_digests", recipe.artifact_digests),
        ):
            try:
                checks[key] = digest(self.plan[field]) == self.plan[expected]
            except (OSError, ValueError):
                checks[key] = False
        safe = exited and release is not False and all(checks.values())
        result = {"state": "stopped" if safe else "cleanup_failed", "target_exited": exited,
                  "release_confirmed": release, **checks, "runtime_retained": self.runtime.exists()}
        if release_error:
            result["release_error"] = release_error
        write_json(self.root / "cleanup.json", result)
        return result
