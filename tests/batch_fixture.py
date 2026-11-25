"""批次协议集成替身：使用真实 MCP、Manager、worker 和自有进程回收。"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    from gameqakit import admission, game_worker, mcp_server as server
    from gameqakit.branch_queue import BatchQueue
    from gameqakit.mcp_client import McpProcess
    from gameqakit.process_job import OwnedProcess
    from gameqakit.sessions import write_json

    # 只替换游戏/SDK 边界；本入口只位于 tests，生产入口没有测试开关。
    def revalidate(plan):
        if plan.get('fingerprint') != admission.fingerprint({k: v for k, v in plan.items() if k != 'fingerprint'}):
            raise ValueError('fixture plan changed')
        if admission.file_digest(Path(plan['source']) / 'original.txt') != plan['source_sha256']:
            raise ValueError('fixture original changed')
        if Path(plan['destination']).exists():
            raise ValueError('fixture destination exists')
        return plan
    admission.revalidate_plan = revalidate

    class Bridge:
        @staticmethod
        def status():
            return {'connected': True, 'paused': True, 'held': []}

    class Session:
        def __init__(self, plan):
            self.plan, self.root = plan, Path(plan['destination'])
            self.host = None
            self.bridge = Bridge()

        def prepare(self):
            revalidate(self.plan)
            self.root.mkdir()

        def start(self, cancelled):
            if cancelled():
                raise InterruptedError('fixture cancelled')
            if self.plan.get('fixture_fail'):
                raise RuntimeError('fixture launch failed')
            self.host = OwnedProcess([sys.executable, '-u', '-B', '-c',
                                      'import time; time.sleep(45)'], cwd=self.root)
            self.host.__enter__()
            self.host.run()
            # 不伪造桥目录，Manager 无需向不存在的桥查询。
            return {'state': 'ready_paused', 'pid': self.host.pid,
                    'exe_path': sys.executable, 'session_id': self.root.name}

        def stop(self):
            if self.host is not None:
                self.host.close()
            return {'state': 'stopped', 'target_exited': True,
                    'release_confirmed': True,
                    'source_unchanged': admission.file_digest(Path(self.plan['source']) / 'original.txt') == self.plan['source_sha256']}

    if sys.argv[1] == 'worker':
        game_worker.session_for = Session
        raise SystemExit(game_worker.run(sys.argv[2]))

    # 保持真实 Manager 的 OwnedProcess/Job 边界，只把 worker 指向本测试入口。
    import gameqakit.process_job as process_job
    class FixtureWorker(OwnedProcess):
        def __init__(self, command, **kwargs):
            if command[1:3] == ['-m', 'gameqakit.game_worker']:
                command = [sys.executable, '-B', str(Path(__file__).resolve()), 'worker', *command[3:]]
            super().__init__(command, **kwargs)
    process_job.OwnedProcess = FixtureWorker

    if sys.argv[1] == 'queue':
        def peer(directory):
            child = McpProcess(directory, command=[sys.executable, '-u', '-B',
                               str(Path(__file__).resolve()), 'peer'])
            write_json(directory / 'mcp-process.json', {'pid': child.process.pid})
            return child
        server._BATCH = BatchQueue(peer_factory=peer)
    server.main()


if __name__ == '__main__':
    main()
