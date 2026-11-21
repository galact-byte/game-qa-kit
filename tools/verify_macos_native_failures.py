"""在 macOS 原生组件上验证故障拒绝、进程回收与新会话恢复。"""
import argparse
import json
import os
from pathlib import Path
import signal
import struct
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gameqakit.capture_session import CaptureEnded
from gameqakit.macos_capture import _Pipe
from tools.verify_macos_product import wait_value


def verify(root, helper, fixture):
    root.mkdir(parents=True, exist_ok=False)
    result = {'ok': False, 'checks': {}, 'cleanup': []}
    target = None
    pipes = []

    def pipe(timeout=3):
        connection = _Pipe(timeout, command=[str(helper)])
        pipes.append(connection)
        return connection

    def rejected(name, request, reason):
        connection = pipe()
        try:
            connection.request(request)
        except CaptureEnded as exc:
            assert str(exc) == f'原生采集终止：{reason}', str(exc)
        else:
            raise AssertionError(f'{name}: 请求未被拒绝')
        code = connection.process.wait(timeout=2)
        assert code == 0, (name, code)
        connection.close()
        result['checks'][name] = {'error': reason, 'exit_code': code}

    def protocol(name, raw=b'', reason=None):
        connection = pipe()
        try:
            output, _ = connection.process.communicate(raw, timeout=3)
        finally:
            if connection.process.poll() is None:
                connection.close()
        assert connection.process.returncode == 0, (name, connection.process.returncode)
        if reason is None:
            assert output == b'', (name, output)
        else:
            assert len(output) >= 4, (name, output)
            length, = struct.unpack('>I', output[:4])
            assert len(output) == 4 + length, (name, len(output), length)
            row = json.loads(output[4:])
            assert row == {'version': 1, 'payload_bytes': 0, 'ok': False, 'error': reason}, row
        result['checks'][name] = {'error': reason, 'exit_code': connection.process.returncode}

    try:
        target = subprocess.Popen([str(fixture), 'target', str(root)])
        ready = wait_value(lambda: json.loads((root / 'target-ready.json').read_text()),
                           lambda value: 'window' in value)
        connection = pipe()
        row, _ = connection.request({'op': 'list'})
        source, = [item for item in row['windows'] if item['window_id'] == ready['window']]
        connection.close()
        result['source'] = source
        rejected('wrong_pid', {'op': 'capture', 'source': {**source, 'pid': os.getpid()}}, 'window_closed')
        rejected('wrong_start_time', {'op': 'capture', 'source': {**source, 'started_at': '0.0'}}, 'source_changed')
        rejected('wrong_executable', {'op': 'capture', 'source': {**source, 'exe_path': '/invalid/GameQA'}}, 'source_changed')
        rejected('unsupported_operation', {'op': 'invalid'}, 'unsupported_operation')
        protocol('invalid_version', b'{"version":2,"op":"list"}\n', 'invalid_request')
        protocol('invalid_json', b'{\n', 'protocol_failed')
        protocol('truncated_request', b'{"version":1', 'truncated_request')
        protocol('oversized_request', b'x' * 8193 + b'\n', 'request_limit')
        protocol('stdin_closed')
        connection = pipe()
        os.kill(connection.process.pid, signal.SIGSTOP)
        started = time.monotonic()
        try:
            connection.request({'op': 'list'})
        except CaptureEnded as exc:
            assert str(exc) == '原生单窗口采集超时', str(exc)
        else:
            raise AssertionError('挂起组件未触发原始三秒期限')
        elapsed = time.monotonic() - started
        connection.close()
        assert connection.expired and connection.process.returncode == -signal.SIGKILL
        result['checks']['hung_helper'] = {'elapsed_ms': elapsed * 1000,
                                          'exit_code': connection.process.returncode}
        assert target.poll() is None, '组件故障关闭了用户窗口'
        connection = pipe()
        row, png = connection.request({'op': 'capture', 'source': source})
        connection.close()
        assert row['source']['window_id'] == source['window_id'] and png.startswith(b'\x89PNG\r\n\x1a\n')
        (root / 'recovered.png').write_bytes(png)
        result['checks']['new_session_recovered'] = {'source': row['source'], 'bytes': len(png)}
        assert target.poll() is None, '恢复采集关闭了用户窗口'
        result['ok'] = True
    except Exception as exc:
        result['error'] = repr(exc)
        raise
    finally:
        for connection in pipes:
            connection.close()
            result['cleanup'].append({'role': 'helper', 'pid': connection.process.pid,
                                      'exit_code': connection.process.returncode})
        (root / 'stop').touch()
        if target is not None:
            forced = False
            try:
                target.wait(timeout=4)
            except subprocess.TimeoutExpired:
                forced = True
                target.kill()
                target.wait(timeout=3)
            result['cleanup'].append({'role': 'fixture', 'pid': target.pid,
                                      'exit_code': target.returncode, 'forced': forced})
            if forced or target.returncode != 0:
                result['ok'] = False
        (root / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    assert result['ok'], result
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--helper', required=True, type=Path)
    parser.add_argument('--fixture', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.root.resolve(), args.helper.resolve(), args.fixture.resolve()), ensure_ascii=False))
