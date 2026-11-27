"""内部 stdio MCP 客户端；独立子进程保留每支创建者生命周期。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading


class McpProcess:
    def __init__(self, log_directory, *, command=None):
        directory = Path(log_directory).absolute()
        self._log = (directory / 'mcp-stderr.log').open('x', encoding='utf-8')
        self._replies = queue.Queue()
        self._lock = threading.RLock()
        self._counter = 0
        self._invalid = self._closed = False
        self._clean_close = None
        frozen = bool(getattr(sys, 'frozen', False))
        command = command or [sys.executable, *([] if frozen else ['-B']), '-m', 'gameqakit.mcp_server']
        cwd = Path(sys.executable).parent if frozen else Path(__file__).resolve().parents[1]
        env = {**os.environ, 'PYTHONUTF8': '1', 'PYTHONDONTWRITEBYTECODE': '1',
               'TEMP': str(directory), 'TMP': str(directory)}
        # 批次不隐式采用人工 profile；控制目标须另行显式绑定。
        env.pop('GAMEQAKIT_PROFILE', None)
        try:
            self.process = subprocess.Popen(command, cwd=str(cwd), env=env, stdin=subprocess.PIPE,
                                            stdout=subprocess.PIPE, stderr=self._log, text=True,
                                            encoding='utf-8', bufsize=1,
                                            # MCP 仅用显式管道；无窗口控制台仍会产生异步退出的 conhost。
                                            creationflags=subprocess.DETACHED_PROCESS if os.name == 'nt' else 0)
        except BaseException:
            self._log.close()
            raise
        self._reader = threading.Thread(target=self._read, name='branch-mcp-reader', daemon=True)
        self._reader.start()

    def _read(self):
        try:
            for line in self.process.stdout:
                self._replies.put(json.loads(line))
        except Exception as exc:
            self._replies.put(exc)
        finally:
            self._replies.put(EOFError('分支 MCP 标准输出已关闭'))

    def call(self, name, arguments, *, timeout=300):
        with self._lock:
            if self._closed or self._invalid:
                raise RuntimeError('分支 MCP 通路已关闭或失效，不重发原请求')
            self._counter += 1
            request = {'jsonrpc': '2.0', 'id': self._counter, 'method': 'tools/call',
                       'params': {'name': name, 'arguments': arguments}}
            try:
                self.process.stdin.write(json.dumps(request, ensure_ascii=False) + '\n')
                self.process.stdin.flush()
                reply = self._replies.get(timeout=timeout)
                if isinstance(reply, Exception):
                    raise reply
                if reply.get('id') != self._counter:
                    raise ValueError('分支 MCP 回执编号不匹配')
            except queue.Empty as exc:
                self._invalid = True
                raise TimeoutError('分支 MCP 请求超时；保留证据，不重发请求') from exc
            except Exception:
                self._invalid = True
                raise
            if 'error' in reply:
                raise RuntimeError(str(reply['error'].get('message', reply['error'])))
            try:
                result = reply['result']
                if result.get('isError'):
                    raise ValueError('分支 MCP 工具返回错误')
                texts = [row['text'] for row in result['content'] if row.get('type') == 'text']
                value = json.loads(texts[0])
                if not isinstance(value, dict):
                    raise ValueError('分支 MCP 结果不是对象')
                return value
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                self._invalid = True
                raise RuntimeError('分支 MCP 回执格式无效') from exc

    def close(self):
        with self._lock:
            if self._closed:
                return self._clean_close
            self._closed = True
            clean = True
            try:
                try:
                    self.process.stdin.close()
                except (OSError, ValueError):
                    pass
                try:
                    self.process.wait(timeout=35)
                except subprocess.TimeoutExpired:
                    # 只操作本 Popen 持有的子进程句柄，不按进程名结束任何目标。
                    clean = False
                    self.process.terminate()
                    self.process.wait(timeout=5)
                self._reader.join(timeout=2)
                clean = clean and self.process.returncode == 0 and not self._reader.is_alive()
            finally:
                self.process.stdout.close()
                self._log.close()
            self._clean_close = clean
            return clean
