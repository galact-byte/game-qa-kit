"""macOS 创建者管道监督器；能力只存在于内存和管道，不从 PID 文件恢复。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import threading
import time


class MacOSProcess:
    def __init__(self, command, *, cwd=None, env=None):
        if sys.platform != 'darwin':
            raise RuntimeError('macOS 进程监督器不能用于其他平台')
        self.command = [str(item) for item in command]
        if not self.command or not Path(self.command[0]).is_absolute():
            raise ValueError('受管程序必须使用绝对路径')
        self.cwd, self.env = str(cwd or Path.cwd()), env or {}
        self.process = None
        self.pid = None
        self.started_at = None
        self._ran = False

    def __enter__(self):
        config = json.dumps({'command': self.command, 'cwd': self.cwd, 'env': self.env}, ensure_ascii=False)
        self.process = subprocess.Popen([sys.executable, '-m', 'gameqakit.macos_process', config],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            close_fds=True, start_new_session=True, bufsize=0)
        timer = threading.Timer(5, self.process.kill)
        timer.start()
        try:
            ready = json.loads(self.process.stdout.readline(8192))
            self.pid = ready['pid']
            from .process_identity import query
            identity = query(self.pid)
            if not identity:
                raise RuntimeError('无法核对受管进程的启动身份')
            self.started_at = identity['started_at']
            return self
        except Exception:
            self.close()
            raise
        finally:
            timer.cancel()
            timer.join()

    def run(self):
        if self._ran or not self.alive():
            raise RuntimeError('受管进程不能重复运行或已退出')
        self.process.stdin.write(b'run\n')
        self.process.stdin.flush()
        self._ran = True

    def alive(self):
        return self.process is not None and self.process.poll() is None

    def close(self):
        if self.process is None:
            return
        # EOF 让监督器回收仍由它作为父进程持有的进程组；不凭外部 PID 杀进程。
        self.process.stdin.close()
        try:
            self.process.wait(timeout=5)
        finally:
            self.process.stdout.close()
        if self.process.returncode != 0:
            raise RuntimeError(f'macOS 自有进程组未确认回收（监督器退出码 {self.process.returncode}）')

    def __exit__(self, *_args):
        self.close()


def _signal_owned_group(pid, signum):
    try:
        os.killpg(pid, signum)
    except ProcessLookupError:
        pass
    except PermissionError:
        # XNU killpg1 跳过 SZOMB；仅剩僵尸的组会返回 EPERM。组长尚未 wait，组 ID 不会复用。
        try:
            row = subprocess.run(['/bin/ps', '-o', 'stat=', '-g', str(pid)],
                                 capture_output=True, text=True, timeout=1)
            states = row.stdout.split()
            if row.returncode == 0 and states and all(state.startswith('Z') for state in states):
                return
        except (OSError, subprocess.TimeoutExpired):
            pass
        raise


def supervise(config):
    if sys.platform != 'darwin':
        raise RuntimeError('仅支持 macOS')
    command = config['command']
    if (not isinstance(command, list) or not command or not all(isinstance(item, str) for item in command)
            or not Path(command[0]).is_absolute()):
        raise ValueError('无效受管命令')
    gate_read, gate_write = os.pipe()
    pid = os.fork()
    if pid == 0:
        try:
            os.close(gate_write)
            os.setsid()
            # 不把创建者控制通道传给目标或它的后代。
            os.close(0)
            os.close(1)
            null = os.open(os.devnull, os.O_RDWR)
            os.dup2(null, 0)
            os.dup2(null, 1)
            os.dup2(null, 2)
            if null > 2:
                os.close(null)
            go = os.read(gate_read, 1)
            os.close(gate_read)
            if go != b'R':
                os._exit(0)
            os.chdir(config['cwd'])
            os.execve(command[0], command, {**os.environ, **config['env']})
        except BaseException:
            os._exit(127)
    os.close(gate_read)
    # setsid 必须先完成；此前进程组仍属于监督器，不能对该组发信号。
    deadline = time.monotonic() + 2
    while os.getpgid(pid) != pid:
        if time.monotonic() >= deadline:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
            os.close(gate_write)
            return 1
        time.sleep(.005)
    print(json.dumps({'pid': pid}), flush=True)
    events = select.kqueue()
    events.control([select.kevent(pid, filter=select.KQ_FILTER_PROC,
                                 flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
                                 fflags=select.KQ_NOTE_EXIT)], 0, 0)
    reaped = False
    try:
        while True:
            if select.select([0], [], [], .05)[0]:
                data = os.read(0, 4096)
                if data == b'run\n' and gate_write is not None:
                    os.write(gate_write, b'R')
                    os.close(gate_write)
                    gate_write = None
                else:
                    break
            # 观察退出但暂不 waitpid，保持 PID/进程组身份直到整组回收。
            if events.control(None, 1, 0):
                break
    finally:
        events.close()
        if gate_write is not None:
            os.close(gate_write)
        _signal_owned_group(pid, signal.SIGTERM)
        time.sleep(.1)
        _signal_owned_group(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
        reaped = True
    return 0 if reaped else 1


def main():
    return supervise(json.loads(sys.argv[1]))


if __name__ == '__main__':
    raise SystemExit(main())
