"""持续核对后台焦点；监视器不激活窗口，也不恢复用户前台。"""
import math
from pathlib import Path
import subprocess
import time

from .sessions import read_json


def validate_focus(directory, pid, started_at):
    row = read_json(Path(directory) / 'focus-state.json')
    updated = row.get('updated_at')
    baseline = row.get('original_frontmost')
    if (row.get('target_pid') != pid or row.get('target_started_at') != started_at
            or type(baseline) is not int or baseline <= 0 or baseline == pid
            or row.get('frontmost') != baseline or row.get('target_activated') is not False
            or row.get('frontmost_changed') is not False or row.get('stopped') is not False
            or type(row.get('samples')) is not int or row['samples'] <= 0
            or type(updated) not in (int, float) or not math.isfinite(updated)
            or not 0 <= time.time() - updated < 1):
        raise RuntimeError('focus_guard_failed：焦点变化或监视记录失效，停止输入')
    return row


class FocusMonitor:
    def __init__(self, directory, pid, started_at):
        from .macos_capture import helper_command
        self.directory = Path(directory)
        self.pid, self.started_at = pid, started_at
        self.process = subprocess.Popen([*helper_command(), '--focus-monitor', str(self.directory),
                                         str(pid), started_at], stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 3
            while not (self.directory / 'focus-ready.json').exists():
                if self.process.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError('焦点监视器未就绪')
                time.sleep(.02)
            self.check()
        except BaseException:
            self.close()
            raise

    def check(self):
        if self.process.poll() is not None:
            raise RuntimeError('focus_guard_failed：焦点监视器已退出')
        return validate_focus(self.directory, self.pid, self.started_at)

    def close(self):
        (self.directory / 'focus-stop').touch()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=3)
            raise RuntimeError('焦点监视器未确认退出')
        if self.process.returncode != 0:
            raise RuntimeError('焦点监视器异常退出')
