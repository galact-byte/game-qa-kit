"""独立 MCP 创建者的有限并发排队；不替测试者执行游戏动作或审图。"""
from __future__ import annotations

import copy
from pathlib import Path
import re
import threading
import time

from . import admission
from .sessions import read_json, write_json

TERMINAL = {'passed', 'failed', 'unverified', 'cancelled', 'cleanup_failed', 'interrupted_unverified'}
WORKER_TERMINAL = {'stopped', 'cancelled', 'failed', 'cleanup_failed'}


def _path(value):
    if not isinstance(value, (str, Path)) or not str(value):
        raise ValueError('目录不能为空')
    path = Path(value).absolute()
    admission.no_links(path)
    return path.resolve()


def _overlap(a, b):
    return a == b or a in b.parents or b in a.parents


def _validate(plan):
    if plan.get('schema_version') != 1:
        raise ValueError('不支持的批次版本')
    payload = {k: v for k, v in plan.items() if k != 'fingerprint'}
    if plan.get('fingerprint') != admission.fingerprint(payload):
        raise ValueError('批次摘要变化，请重新检查')
    rows = plan.get('branches')
    limit = plan.get('max_active')
    if not isinstance(rows, list) or not rows or type(limit) is not int or not 1 <= limit <= len(rows):
        raise ValueError('max_active 必须为 1 到分支数之间的整数')
    ids, outputs, inputs = set(), [_path(plan['report_directory'])], []
    for row in rows:
        name = row['id']
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', name) or name in ids:
            raise ValueError('分支 ID 必须唯一，限 1～64 位字母、数字、下划线或连字符')
        ids.add(name)
        child = row['plan']
        if child.get('fingerprint') != admission.fingerprint({k: v for k, v in child.items() if k != 'fingerprint'}):
            raise ValueError('分支摘要变化')
        outputs.append(_path(child['destination']))
        inputs.extend(_path(child[key]) for key in ('source', 'artifacts'))
        if child.get('checkpoint'):
            inputs.append(_path(child['checkpoint']['directory']))
    for i, output in enumerate(outputs):
        if output.exists():
            raise ValueError('报告及分支目录必须全新：' + str(output))
        if any(_overlap(output, other) for other in outputs[i + 1:] + inputs):
            raise ValueError('报告、分支与原件目录不能重叠')


def make_plan(branches, *, max_active, report_directory):
    if not isinstance(branches, list) or not branches:
        raise ValueError('请提供至少一个分支')
    rows = []
    for spec in branches:
        options = {'artifacts': spec['artifacts']}
        if spec.get('checkpoint') is not None:
            options['checkpoint'] = spec['checkpoint']
        rows.append({'id': spec['id'], 'plan': admission.make_plan(spec['path'], spec['destination'], **options)})
    payload = dict(schema_version=1, branches=rows, max_active=max_active,
                   report_directory=str(_path(report_directory)))
    plan = {**payload, 'fingerprint': admission.fingerprint(payload)}
    _validate(plan)
    return plan


def read_report(directory):
    """磁盘记录只用于查证，不恢复调度或进程停止权。"""
    report = read_json(_path(directory) / 'summary.json')
    report.update(live=False, can_control=False)
    for row in report['branches']:
        if row['state'] not in TERMINAL:
            row['recorded_state'], row['state'] = row['state'], 'interrupted_unverified'
    if any(r['state'] == 'interrupted_unverified' for r in report['branches']):
        report['recorded_state'], report['state'] = report['state'], 'interrupted_unverified'
    report['counts'] = _counts(report['branches'])
    return report


def _counts(rows):
    return {name: sum(row['state'] == name for row in rows)
            for name in sorted(TERMINAL | {'queued', 'preparing', 'starting', 'ready', 'stopping'})}


class BatchQueue:
    """轮询推进调度，每一名额由独立 MCP 进程持有到回收确认。"""
    def __init__(self, *, peer_factory=None):
        if peer_factory is None:
            from .mcp_client import McpProcess
            peer_factory = McpProcess
        self._factory, self._peers = peer_factory, {}
        self.plan = None
        self._rows = []
        self._cancelled = self._blocked = self._closed = False
        self._lock = threading.RLock()

    def start(self, plan, *, confirm=False):
        with self._lock:
            if confirm is not True:
                raise ValueError('启动批次需要 confirm=true')
            if self.plan is not None:
                if plan != self.plan:
                    raise ValueError('本创建者已持有另一批次，请使用独立 MCP')
                return self.status()
            _validate(plan)
            # 所有摘要先复核，避免后支原件已变化时先启动前支。
            for row in plan['branches']:
                admission.revalidate_plan(row['plan'])
            directory = _path(plan['report_directory'])
            directory.mkdir(parents=True, exist_ok=False)
            self.plan = copy.deepcopy(plan)
            self._rows = [dict(id=r['id'], plan=r['plan'], state='queued', evidence=[],
                               cleanup_verified=False) for r in self.plan['branches']]
            write_json(directory / 'plan.json', self.plan)
            return self.status()

    def _call(self, row, name, **arguments):
        return self._peers[row['id']].call(name, arguments)

    def _stop(self, row):
        if row.get('stop_requested'):
            return
        self._call(row, 'game_stop', directory=row['plan']['destination'],
                   request_id='batch-stop-' + row['id'], confirm=True)
        row.update(state='stopping', stop_requested=True)

    def _settle(self, row, state, *, peer_closed):
        row['session'] = state
        if state.get('state') not in WORKER_TERMINAL or state.get('worker_alive') is not False:
            row.update(state='interrupted_unverified', cleanup_verified=False)
            self._blocked = True
            return
        clean = peer_closed and state['state'] != 'cleanup_failed' and not any(
            state.get(key) is False for key in ('target_exited', 'release_confirmed', 'source_unchanged',
                'original_unchanged', 'runtime_source_unchanged', 'checkpoint_unchanged'))
        row['cleanup_verified'] = clean
        if not clean:
            row['state'], self._blocked = 'cleanup_failed', True
            return
        row['state'] = ('failed' if state['state'] == 'failed' else
                        row.get('judgment', {}).get('outcome', 'cancelled' if self._cancelled else 'unverified'))
        if row.get('evidence'):
            try:
                row['evidence_intact'] = all(admission.file_digest(_path(ref['path'])) == ref['sha256']
                                             for ref in row['evidence'])
            except (OSError, ValueError):
                row['evidence_intact'] = False
            if row['state'] == 'passed' and not row['evidence_intact']:
                row['state'] = 'unverified'

    def _poll(self, row):
        state = self._call(row, 'game_status', directory=row['plan']['destination'])
        row['session'] = state
        stage = state['state']
        if stage in WORKER_TERMINAL:
            if state.get('worker_alive') is not False:
                row['state'] = 'stopping'
                return
            closed = self._peers[row['id']].close() is True
            del self._peers[row['id']]
            self._settle(row, state, peer_closed=closed)
            return
        if stage == 'interrupted_unverified':
            raise RuntimeError('分支创建者或工作进程失联，回收未核验')
        if self._cancelled:
            self._stop(row)
        elif row.get('stop_requested'):
            row['state'] = 'stopping'
        elif stage == 'prepared':
            self._call(row, 'game_start', directory=row['plan']['destination'],
                       request_id='batch-start-' + row['id'], fingerprint=row['plan']['fingerprint'], confirm=True)
            row['state'] = 'starting'
        elif stage in ('ready_paused', 'running'):
            row['state'] = 'ready'
        else:
            row['state'] = 'starting' if stage == 'starting' else 'preparing'

    def status(self):
        with self._lock:
            if self.plan is None:
                return {'state': 'idle', 'live': True, 'branches': [], 'counts': {}}
            if not self._closed:
                for row in self._rows:
                    if row['id'] in self._peers and row['state'] not in TERMINAL:
                        try:
                            self._poll(row)
                        except Exception as exc:
                            row.update(state='interrupted_unverified', error=str(exc))
                            self._blocked = True
                if not self._blocked and not self._cancelled:
                    for row in self._rows:
                        if len(self._peers) >= self.plan['max_active']:
                            break
                        if row['state'] != 'queued':
                            continue
                        try:
                            folder = Path(self.plan['report_directory']) / row['id']
                            folder.mkdir()
                            self._peers[row['id']] = self._factory(folder)
                            self._call(row, 'game_prepare', plan=row['plan'],
                                       request_id='batch-prepare-' + row['id'], confirm=True)
                            row['state'] = 'preparing'
                        except Exception as exc:
                            row.update(state='interrupted_unverified', error=str(exc))
                            self._blocked = True
                            break
            done = all(row['state'] in TERMINAL for row in self._rows)
            state = ('blocked' if self._blocked else 'cancelled' if done and self._cancelled else
                     'completed' if done else 'running')
            report = dict(schema_version=1, fingerprint=self.plan['fingerprint'], state=state,
                          live=not self._closed, can_control=False,
                          report_directory=self.plan['report_directory'], max_active=self.plan['max_active'],
                          updated_at=time.time(), branches=copy.deepcopy(self._rows), counts=_counts(self._rows))
            write_json(Path(self.plan['report_directory']) / 'summary.json', report)
            return report

    def finish(self, branch_id, *, outcome, evidence, note=''):
        with self._lock:
            if outcome not in ('passed', 'failed', 'unverified') or not isinstance(note, str):
                raise ValueError('outcome 必须为 passed、failed 或 unverified，note 必须为文字')
            if not isinstance(evidence, list) or (outcome == 'passed' and not evidence):
                raise ValueError('通过声明必须带已复核的证据文件')
            refs = []
            for item in evidence:
                path = _path(item)
                if not path.is_file():
                    raise ValueError('证据文件不存在：' + str(path))
                refs.append(dict(path=str(path), sha256=admission.file_digest(path)))
            judgment = dict(outcome=outcome, evidence=refs, note=note)
            row = next((r for r in self._rows if r['id'] == branch_id), None)
            if row is None:
                raise ValueError('未知分支 ID')
            if row.get('judgment'):
                if judgment != row['judgment']:
                    raise ValueError('分支结论已记录，不能覆盖；请另建批次')
                return self.status()
            if row['state'] != 'ready' or self._closed or self._cancelled:
                raise ValueError('只有活动就绪分支可以提交结论')
            row.update(judgment=judgment, evidence=refs)
            try:
                self._stop(row)
            except Exception as exc:
                row.update(state='interrupted_unverified', error=str(exc))
                self._blocked = True
            return self.status()

    def cancel(self):
        with self._lock:
            self._cancelled = self._cancelled or any(row['state'] not in TERMINAL for row in self._rows)
            for row in self._rows:
                if row['state'] == 'queued':
                    row['state'] = 'cancelled'
                elif row['id'] in self._peers:
                    try:
                        self._stop(row)
                    except Exception as exc:
                        row.update(state='interrupted_unverified', error=str(exc))
                        self._blocked = True
            return self.status()

    def close(self):
        with self._lock:
            if self._closed:
                return
            self.cancel()
            for row in self._rows:
                peer = self._peers.pop(row['id'], None)
                if peer is not None:
                    try:
                        closed = peer.close() is True
                        from .game_session import Manager
                        self._settle(row, Manager().status(row['plan']['destination']), peer_closed=closed)
                    except Exception as exc:
                        row.update(state='interrupted_unverified', cleanup_verified=False, error=str(exc))
                        self._blocked = True
            self._closed = True
            if self.plan is not None:
                self.status()
