"""Run the registered Tyrano fixture under the existing Electron watchdog."""
import hashlib
from pathlib import Path
import shutil
from threading import Thread
import time
import uuid

from gameqakit.admission import file_digest, no_links, revalidate_plan
from gameqakit.admission_runtime import validate_ready
from gameqakit.control import BridgeClient
from gameqakit.sessions import read_json, write_json
from gameqakit.win32 import _pid_path
from . import host, prepare as recipe


class _Watchdog(Thread):
    def alive(self):
        return self.is_alive()


class TyranoSession:
    def __init__(self, plan):
        self.plan = plan
        self.root = Path(plan["destination"])
        self.runtime = self.root / "runtime"
        self.directory = self.root / "bridge"
        self.executable = self.runtime / recipe.EXECUTABLE
        self.session = uuid.uuid4().hex
        self.baseline = None
        self.host = self.bridge = None
        self.host_error = None
        self.pid = None

    def _copied_digests(self):
        return {p.relative_to(self.runtime).as_posix(): file_digest(p) for p in recipe.tree_files(self.runtime)}

    def prepare(self):
        revalidate_plan(self.plan)
        self.root.mkdir(exist_ok=False)
        self.directory.mkdir()
        artifacts = Path(self.plan["artifacts"])
        baseline = recipe.runtime_files(artifacts)
        sources = {name: artifacts / name for name in baseline if name != recipe.CONFIG}
        scene = recipe.APP + "data/scenario/first.ks"
        sources[scene] = Path(self.plan["source"]) / recipe.SCRIPT
        baseline[scene] = recipe.SCRIPT_SHA256
        for name, target in recipe.BRIDGES.items():
            sources[recipe.APP + target] = recipe.PLUGIN / name
            baseline[recipe.APP + target] = self.plan["artifact_digests"][name]
        for name, source in sources.items():
            no_links(source)
            target = self.runtime / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        config = artifacts / recipe.CONFIG
        no_links(config)
        original = config.read_bytes()
        if hashlib.sha256(original).hexdigest() != baseline[recipe.CONFIG]:
            raise ValueError("runtime_changed: TyranoScript config changed during preparation")
        generated = recipe.authored_files()
        generated[recipe.CONFIG] = original + b"\n;projectID=gameqa_tyrano_fixture\n;configSave=webstorage\n"
        for name, body in generated.items():
            path = self.runtime / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
            baseline[name] = hashlib.sha256(body).hexdigest()
        if self._copied_digests() != baseline:
            raise ValueError("runtime_changed: TyranoScript copied files do not match the registration")
        self.baseline = baseline

    def _watch(self):
        try:
            host.run(self.executable, self.directory, self.session, self.root / "data", fixture_root=self.root)
        except Exception as exc:
            self.host_error = str(exc)

    def start(self, cancelled=lambda: False):
        if self.baseline is None or self.host is not None:
            raise RuntimeError("启动前须准备，且不能重复启动")
        if (recipe.source_digests(self.plan["source"]) != self.plan["source_digests"]
                or recipe.artifact_digests(self.plan["artifacts"]) != self.plan["artifact_digests"]
                or self._copied_digests() != self.baseline):
            raise ValueError("runtime_changed: TyranoScript scene or runtime changed")
        no_links(self.directory)
        if any(self.directory.iterdir()) or {p.name for p in self.root.iterdir()} - {
                "runtime", "bridge", "session.json", "start.request.json", "stop.request"}:
            raise ValueError("TyranoScript 会话包含未登记的文件")
        if cancelled():
            raise InterruptedError("接入已取消")
        self.host = _Watchdog(target=self._watch, daemon=True)
        self.host.start()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if cancelled():
                raise InterruptedError("接入已取消")
            if not self.host.is_alive():
                raise RuntimeError(self.host_error or "target_exited_before_ready")
            if self.bridge is None and (self.directory / "connection.json").is_file():
                meta = read_json(self.directory / "connection.json")
                self.pid = meta["pid"]
                self.bridge = BridgeClient(self.directory, pid=self.pid, exe_path=str(self.executable))
            if self.bridge:
                state = self.bridge.status()
                if state.get("connected"):
                    if state.get("error"):
                        raise RuntimeError(state["error"])
                    if state.get("observation_seq", 0) > 0:
                        return {**state, **validate_ready(self.root, state), "state": "ready_paused",
                                "bridge_directory": str(self.directory)}
            time.sleep(.02)
        raise TimeoutError("startup_timeout: TyranoScript 未取得有效首图")

    def stop(self):
        release = release_error = None
        if self.host:
            try:
                release = False
                if self.bridge and self.host.is_alive():
                    self.bridge.pause()
                    pause_id = read_json(self.directory / "pause.json")["id"]
                    deadline = time.monotonic() + 1
                    while self.host.is_alive() and time.monotonic() < deadline:
                        state = self.bridge.status()
                        if (state.get("connected") and state.get("pause_id") == pause_id
                                and state.get("paused") is True and state.get("held") == []):
                            release = True
                            break
                        time.sleep(.02)
                if not release:
                    release_error = "pause_not_acknowledged"
            except (OSError, ValueError, RuntimeError) as exc:
                release_error = str(exc)
            finally:
                write_json(self.directory / "stop.json", {"session_id": self.session})
                self.host.join(3)
        termination = self.directory / "termination.json"
        ended = read_json(termination) if termination.is_file() else {}
        exited = not (self.host and self.host.is_alive()) and not _pid_path(ended.get("pid") or self.pid or 0)
        checks = {}
        for key, field, digest_field, verify in (
                ("source_unchanged", "source", "source_digests", recipe.source_digests),
                ("runtime_source_unchanged", "artifacts", "artifact_digests", recipe.artifact_digests)):
            try:
                checks[key] = verify(self.plan[field]) == self.plan[digest_field]
            except (OSError, ValueError):
                checks[key] = False
        safe = exited and release is not False and all(checks.values())
        result = {"state": "stopped" if safe else "cleanup_failed", "target_exited": exited,
                  "release_confirmed": release, **checks, "runtime_retained": self.runtime.exists()}
        if release_error:
            result["release_error"] = release_error
        if ended:
            result["termination_reason"] = ended["reason"]
        write_json(self.root / "cleanup.json", result)
        return result
