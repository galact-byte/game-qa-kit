"""创建者 Job 内的受管会话 worker；记录不携带可恢复的停止能力。"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import time

from .sessions import read_json, write_json


def session_for(plan):
    if plan.get('backend') == 'native_input':
        from plugins.native_input.session import NativeSession
        return NativeSession(plan)
    if plan.get('backend') == 'unity_mono_legacy':
        from plugins.unity.session import UnitySession
        return UnitySession(plan)
    raise ValueError('unsupported_backend')


def _run(record):
    from .admission import no_links
    request = read_json(record)
    plan = request['payload']
    root = Path(plan['destination'])
    cancel_file = Path(record).parent / (root.name + '.cancel.json')
    native = session_for(plan)
    phase = 'preparing'
    state = {'schema_version': 1, 'state': phase, 'backend': plan['backend'],
             'fingerprint': plan['fingerprint'], 'request_id': request['request_id'],
             'worker_pid': os.getpid(), 'directory': str(root)}

    def cancelled():
        return cancel_file.exists() or (root / 'stop.request').exists()

    status_file = Path(record).parent / (root.name + '.status.json')

    def publish(**values):
        state.update(values)
        write_json(status_file, state)
        if root.exists():
            write_json(root / 'session.json', state)

    try:
        publish()
        native.prepare()
        publish(state='prepared')
        phase = 'prepared'
        while not cancelled():
            start = root / 'start.request.json'
            if start.exists():
                no_links(start)
                command = read_json(start)
                if (command.get('operation') != 'start' or command.get('directory') != str(root.resolve())
                        or command.get('payload', {}).get('fingerprint') != plan['fingerprint']):
                    raise ValueError('invalid_start_request')
                publish(state='starting')
                phase = 'starting'
                result = native.start(cancelled)
                publish(**result)
                phase = 'ready_paused'
                break
            time.sleep(.02)
        while phase == 'ready_paused' and not cancelled():
            if not native.host.alive():
                raise RuntimeError('host_exited')
            bridge = native.bridge.status()
            if bridge.get('observation_error'):
                raise RuntimeError('observation_failed：' + str(bridge['observation_error']))
            if not bridge.get('connected'):
                raise RuntimeError('bridge_disconnected：' + str(bridge.get('error') or 'unknown')
                                   + '；停止输入并回收自有进程')
            if (root / 'bridge/fatal.json').exists():
                raise RuntimeError('bridge_failed：' + str(read_json(root / 'bridge/fatal.json')))
            # resumed 状态由控制协议维护；生命周期记录不据此冒称仿真暂停。
            time.sleep(.1)
    except Exception as exc:
        state.update(error=str(exc), failed_phase=phase)
    finally:
        if root.exists():
            publish(state='stopping')
            try:
                cleanup = native.stop()
                final = cleanup['state']
                if state.get('error') and final == 'stopped':
                    final = 'failed'
                publish(**{**cleanup, 'state': final})
            except Exception as exc:
                publish(state='cleanup_failed', cleanup_error=str(exc), runtime_retained=True)
        else:
            publish(state='failed')
    return 1 if state.get('error') or state.get('state') == 'cleanup_failed' else 0


def run(record):
    from .sessions import SessionLease
    from .admission import no_links
    record = Path(record)
    root = Path(read_json(record)['payload']['destination'])
    lease_path = record.parent / (root.name + '.lifecycle.lock')
    no_links(lease_path)
    with SessionLease(lease_path):
        return _run(record)


if __name__ == '__main__':
    raise SystemExit(run(sys.argv[1]))
