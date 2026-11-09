"""跨进程盯屏会话：文件锁、原子状态与共享控制入口。"""
from __future__ import annotations

import json
import os
import tempfile
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

_CHILDREN = {}


def write_json(path, value):
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2))


def write_text(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".part", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(value)
        # Windows 的短期读句柄会阻止替换；限时重试，持续错误仍向上传递。
        deadline = time.monotonic() + 0.25
        while True:
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if os.name != "nt" or time.monotonic() >= deadline:
                    raise
                time.sleep(0.01)
    finally:
        Path(temporary).unlink(missing_ok=True)


class SessionLease:
    """锁随进程退出自动释放；不依赖 PID 文件判断存活。"""
    def __init__(self, path):
        self.path = Path(path)
        self.stream = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open("a+b")
        self.stream.seek(0, 2)
        if not self.stream.tell():
            self.stream.write(b"0")
            self.stream.flush()
        self.stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.stream.close()
            self.stream = None
            raise RuntimeError("此 profile 已有盯屏会话或控制操作，请查询状态后重试") from exc
        return self

    def __exit__(self, *_args):
        if self.stream is not None:
            if os.name == "nt":
                import msvcrt
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            self.stream.close()
            self.stream = None


def read_text(path):
    deadline = time.monotonic() + 0.25
    while True:
        try:
            with Path(path).open('rb') as stream:
                body = stream.read(131073)
            if len(body) > 131072:
                raise ValueError('会话控制文件超过 128 KiB')
            return body.decode('utf-8')
        except (PermissionError, FileNotFoundError):
            # Unity Mono may briefly remove the destination during replacement.
            if os.name != 'nt' or time.monotonic() >= deadline:
                raise
            time.sleep(0.005)


def read_json(path):
    return json.loads(read_text(path))


def _read(path):
    return read_json(path)


def watch_status(profile):
    for pid, child in list(_CHILDREN.items()):
        if child.poll() is not None:
            _CHILDREN.pop(pid, None)
    pointer = profile.state_dir / "watch_current.json"
    if not pointer.exists():
        return {"running": False, "watch": "idle", "out": None}
    try:
        directory = Path(_read(pointer)["dir"])
        status_path = directory / "status.json"
        status = _read(status_path) if status_path.exists() else {
            "running": False, "watch": "idle", "out": str(directory)}
        if status.get("running"):
            try:
                with SessionLease(profile.state_dir / "watch.lock"):
                    status.update(running=False, watch="interrupted",
                                  last_error="采集进程已退出，请重新启动盯屏")
            except RuntimeError:
                pass
        return status
    except (ValueError, KeyError, OSError) as exc:
        return {"running": False, "watch": "error", "out": None, "last_error": f"会话状态读取失败：{exc}"}


def start_watch(profile, *, interval=2.0, threshold=4, region=None, downscale=None, analysis_mode="rules"):
    from .watch import validate_options
    options = dict(interval=interval, threshold=threshold, region=region,
                   downscale=downscale, analysis_mode=analysis_mode)
    validate_options(**options)
    with SessionLease(profile.state_dir / "watch_control.lock"):
        current = watch_status(profile)
        if current["running"]:
            return {**current, "already_running": True}
        directory = profile.watch_dir / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        directory.mkdir(parents=True, exist_ok=False)
        config = directory / "worker.json"
        write_json(config, {"out": str(directory.resolve()), "proc": profile.proc,
                            "state_dir": str(profile.state_dir.resolve()),
                            "title_substr": profile.title_substr, "exe_path": profile.exe_path,
                            "target_pid": profile.target_pid, **options})
        with (directory / "worker.log").open("x", encoding="utf-8") as log:
            child = subprocess.Popen([sys.executable, "-m", "gameqakit.sessions", str(config.resolve())],
                                     cwd=str(Path(__file__).resolve().parent.parent),
                                     stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                     creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        _CHILDREN[child.pid] = child
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status_path = directory / "status.json"
            if status_path.exists():
                status = _read(status_path)
                if status.get("watch") == "failed":
                    raise RuntimeError(status.get("last_error", "盯屏启动失败"))
                return {**status, "already_running": False}
            if child.poll() is not None:
                raise RuntimeError(f"盯屏进程启动失败，请查看 {directory / 'worker.log'}")
            time.sleep(0.02)
        # 超时只撤回本次启动，不终止其他会话或游戏。
        (directory / "stop.request").touch()
        raise RuntimeError(f"盯屏启动超时，已请求停止；请查看 {directory / 'worker.log'}")


def stop_watch(profile):
    with SessionLease(profile.state_dir / "watch_control.lock"):
        status = watch_status(profile)
        if status["running"]:
            (Path(status["out"]) / "stop.request").touch()
            status["watch"] = "stopping"
        return status


def main():
    import traceback
    config = _read(sys.argv[1])
    try:
        from .watch import watch
        watch(config.pop("out"), config.pop("proc"), config.pop("state_dir"),
              on_event=lambda _obj: None, **config)
    except Exception as exc:
        directory = Path(sys.argv[1]).parent
        write_json(directory / "status.json", {"running": False, "watch": "failed", "ok": False,
                                               "out": str(directory), "last_error": str(exc)})
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
