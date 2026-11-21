"""独立 Xvfb 上的 XComposite 多进程与断连实验；不接入产品或发送输入。"""
from __future__ import annotations

import argparse
import ctypes as C
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import time

from probe_x11_capture import COVER, MARKERS, X11, command, png, reply, require_progress


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def client(window, output, connection_only=False):
    connection, pixmap, redirected, closed = X11(), 0, False, False
    try:
        if not connection_only:
            connection.composite.XCompositeRedirectWindow(connection.display, window, 0)
            connection.sync()
            redirected = True
            pixmap = connection.composite.XCompositeNameWindowPixmap(connection.display, window)
            connection.sync()
        emit({'event': 'ready', 'pid': os.getpid()})
        for line in sys.stdin:
            event = line.strip()
            if event == 'quit':
                break
            if event == 'capture':
                pixels = connection.image(pixmap, 320, 180)
                connection.sync()
                if tuple(pixels[y * 320 + x] for x, y in ((8, 8), (312, 8), (8, 172), (312, 172))) != MARKERS:
                    raise ValueError('目标标记不匹配')
                counter = 0
                for bit in range(16):
                    pixel = pixels[38 * 320 + 26 + bit * 16]
                    if pixel not in (0, 0xFFFFFF):
                        raise ValueError('计数像素不完整')
                    counter |= int(pixel == 0xFFFFFF) << bit
                target = output / f'{os.getpid()}-{time.monotonic_ns()}.png'
                png(target, pixels, 320, 180)
                emit({'event': event, 'counter': counter, 'png': target.name})
            elif event == 'check_closed':
                connection.composite.XCompositeNameWindowPixmap(connection.display, window)
                try:
                    connection.sync()
                except RuntimeError as exc:
                    closed = True
                    emit({'event': event, 'rejected': True, 'error': str(exc)})
                else:
                    raise ValueError('已销毁窗口仍然接受新 pixmap 绑定')
            elif event == 'sync':
                connection.sync()
                emit({'event': event})
            else:
                raise ValueError(f'未知客户端命令：{event}')
    finally:
        if pixmap:
            connection.x.XFreePixmap(connection.display, pixmap)
        if redirected and not closed:
            connection.composite.XCompositeUnredirectWindow(connection.display, window, 0)
        connection.sync()
        connection.close()


def probe(output):
    output.mkdir(parents=True, exist_ok=False)
    report = {'ok': False, 'scope': 'Xvfb x64 CPU fixture; not a product acceptance',
              'commit': os.environ.get('GITHUB_SHA'), 'checks': {}, 'cleanup': []}
    children, logs, observer = [], [], None
    expected_codes = {}

    def start(args, label, *, env=None, pass_fds=()):
        log = (output / f'{label}.log').open('wb')
        logs.append(log)
        process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=log, text=True, env=env, pass_fds=pass_fds)
        children.append((label, process))
        return process

    def fixture(kind, label):
        process = start([sys.executable, '-B', str(Path(__file__).with_name('probe_x11_capture.py')),
                         '--fixture', kind], label)
        return process, reply(process)

    def sampler(window, label):
        process = start([sys.executable, '-B', str(Path(__file__).resolve()), '--client',
                         str(window), '--output', str(output)], label)
        if reply(process).get('event') != 'ready':
            raise ValueError('采集器未就绪')
        return process

    def capture_sequence(process, count):
        samples = []
        for _ in range(count):
            time.sleep(.15)
            samples.append(command(process, 'capture'))
        require_progress(samples)
        return samples

    def stop_fixture(process):
        process.communicate('quit\n', timeout=3)
        if process.returncode != 0:
            raise ValueError('测试窗口未正常退出')

    try:
        target, identity = fixture('target', 'target')
        report['target'] = identity
        first = sampler(identity['window'], 'sampler-a')
        second = sampler(identity['window'], 'sampler-b')
        cover, report['cover'] = fixture('cover', 'cover')
        observer = X11()
        time.sleep(.15)
        root_pixels = observer.image(observer.root, 320, 180, x=60, y=60)
        fraction = sum(pixel == COVER for pixel in root_pixels) / len(root_pixels)
        if fraction != 1:
            raise ValueError('目标未完全遮挡')
        png(output / 'covered-root.png', root_pixels, 320, 180)
        report['cover_fraction'] = fraction
        report['checks']['two_redirectors'] = [capture_sequence(first, 3), capture_sequence(second, 3)]
        first.communicate('quit\n', timeout=3)
        if first.returncode:
            raise ValueError('第一个采集器未正常释放资源')
        report['checks']['survives_other_unredirect'] = capture_sequence(second, 3)
        third = sampler(identity['window'], 'sampler-c')
        report['checks']['new_client_after_release'] = capture_sequence(third, 3)
        third.kill()
        third.communicate(timeout=3)
        expected_codes[third.pid] = third.returncode
        report['checks']['survives_other_client_kill'] = capture_sequence(second, 3)
        stop_fixture(target)
        report['checks']['closed_window'] = command(second, 'check_closed')
        replacement, replacement_identity = fixture('target', 'replacement')
        report['replacement'] = replacement_identity
        if replacement_identity['window'] == identity['window']:
            report['checks']['xid_reuse'] = {'same_xid': True,
                'original_pid': identity['pid'], 'replacement_pid': replacement_identity['pid'],
                'requires_process_identity_check': True}
        else:
            report['checks']['old_window_stays_rejected'] = command(second, 'check_closed')
        replacement_sampler = sampler(replacement_identity['window'], 'sampler-replacement')
        report['checks']['new_window_requires_new_binding'] = capture_sequence(replacement_sampler, 3)
        replacement_sampler.communicate('quit\n', timeout=3)
        second.communicate('quit\n', timeout=3)
        stop_fixture(replacement)
        stop_fixture(cover)
        observer.close()
        observer = None

        # 此服务端仅属于本实验，故意终止它以验证 Xlib I/O 错误不拖死控制进程。
        read_fd, write_fd = os.pipe()
        try:
            server = start(['Xvfb', '-displayfd', str(write_fd), '-screen', '0', '640x480x24',
                            '-nolisten', 'tcp', '+extension', 'Composite'],
                           'disconnect-server', pass_fds=(write_fd,))
            os.close(write_fd)
            write_fd = -1
            if not select.select([read_fd], [], [], 5)[0]:
                raise TimeoutError('独立断连测试屏幕未就绪')
            number = os.read(read_fd, 100).decode().strip()
            if not number.isdigit():
                raise ValueError('测试服务端返回了无效屏幕号')
        finally:
            os.close(read_fd)
            if write_fd >= 0:
                os.close(write_fd)
        disconnected = start([sys.executable, '-B', str(Path(__file__).resolve()), '--connection-only',
                              '--output', str(output)], 'disconnected-client',
                             env={**os.environ, 'DISPLAY': ':' + number})
        reply(disconnected)
        server.terminate()
        server.communicate(timeout=3)
        expected_codes[server.pid] = server.returncode
        before = time.monotonic()
        disconnected.stdin.write('sync\n')
        disconnected.stdin.flush()
        disconnected.communicate(timeout=3)
        elapsed = time.monotonic() - before
        if disconnected.returncode == 0:
            raise ValueError('X 服务断开后采集器仍返回成功')
        expected_codes[disconnected.pid] = disconnected.returncode
        report['checks']['server_disconnect'] = {'client_exit_code': disconnected.returncode,
                                                'elapsed_seconds': elapsed,
                                                'supervisor_alive': True}
        report['ok'] = True
    except Exception as exc:
        report['error'] = repr(exc)
    finally:
        if observer:
            observer.close()
        for label, process in reversed(children):
            forced = False
            if process.poll() is None:
                try:
                    process.communicate('quit\n', timeout=3)
                except (BrokenPipeError, subprocess.TimeoutExpired):
                    forced = True
                    process.kill()
                    process.communicate(timeout=3)
            expected = expected_codes.get(process.pid, 0)
            report['cleanup'].append({'label': label, 'pid': process.pid, 'code': process.returncode,
                                      'expected_code': expected, 'forced_cleanup': forced})
            if forced or process.returncode != expected:
                report['ok'] = False
        for log in logs:
            log.close()
        (output / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    emit(report)
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--client', type=int)
    parser.add_argument('--connection-only', action='store_true')
    options = parser.parse_args()
    if options.client or options.connection_only:
        client(options.client, options.output, options.connection_only)
    else:
        raise SystemExit(probe(options.output))
