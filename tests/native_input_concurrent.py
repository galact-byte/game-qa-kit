"""自有 x86 窗口经真实 MCP 的实体并发验收；不对商业游戏或硬件来源作推断。"""
import argparse
import ctypes
from ctypes import wintypes
import hashlib
import os
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def assess(before, after, witness, samples, *, rounds, smoke=False):
    held = [s for s in samples if s['down']]
    overlap = {s['round'] for s in held if s['left'] and s['foreground']}
    checks = {
        'exact_software_presses': after['presses'] - before['presses'] == rounds,
        'exact_releases': after['releases'] - before['releases'] == rounds and not after['down'],
        'actual_position': after['position'] == before['position'] + 20 * rounds,
        'only_requested_right': bool(held) and all(s['last_scan_code'] == 0xcd for s in held),
        'target_never_foreground': bool(samples) and not any(s['target_foreground'] for s in samples),
        'no_software_right_in_witness': witness['right_presses'] == 0,
        'concurrent_left_and_right': len(overlap) >= 5,
        'mouse_participation': witness['motion'] >= 10 and witness['clicks'] >= 2,
        'keyboard_participation': witness['left_presses'] >= 1,
    }
    participation = {'concurrent_left_and_right', 'mouse_participation', 'keyboard_participation'}
    if not all(value for key, value in checks.items() if key not in participation):
        status = 'failed'
    elif smoke:
        status = 'smoke_passed_no_physical_claim'
    elif not all(checks.values()):
        status = 'incomplete_participation'
    else:
        status = 'checks_passed_pending_participant_confirmation'
    return {'status': status, 'physical_verified': False, 'checks': checks,
            'overlap_rounds': len(overlap),
            'limitation': 'Window events do not authenticate hardware provenance; participant confirmation is required.'}


class Witness:
    def __init__(self, root):
        import tkinter as tk
        self.root = root
        self.lock = threading.Lock()
        self.changed = threading.Event()
        self.cancelled = threading.Event()
        self.state = {'left': False, 'left_presses': 0, 'right_presses': 0, 'motion': 0, 'clicks': 0}
        self.progress = {'status': 'starting', 'rounds': 0}
        root.title('GameQA — 实体并发见证窗口')
        root.geometry('620x310+520+80')
        tk.Label(root, text='请在这个窗口操作，不要点击后台测试窗口。', font=('Microsoft YaHei UI', 16)).pack(pady=20)
        tk.Label(root, text='点击“准备好了”后，持续按住键盘左方向键；\n同时在本窗口移动鼠标，并点击至少两次。\n后台将发出 12 次右移动，约需 15 秒。',
                 font=('Microsoft YaHei UI', 12), justify='left').pack(pady=8)
        self.message = tk.StringVar(value='正在准备后台测试，请稍候。')
        tk.Label(root, textvariable=self.message, wraplength=580).pack(pady=12)
        self.button = tk.Button(root, text='准备好了', command=self.ready)
        self.button.pack()
        root.bind('<KeyPress-Left>', lambda e: self.event('left', True))
        root.bind('<KeyRelease-Left>', lambda e: self.event('left', False))
        root.bind('<KeyPress-Right>', lambda e: self.event('right_presses'))
        root.bind('<Motion>', lambda e: self.event('motion'))
        root.bind('<ButtonPress-1>', lambda e: self.event('clicks'))
        root.bind('<FocusOut>', lambda e: self.event('left', False))
        root.protocol('WM_DELETE_WINDOW', self.cancelled.set)

    def ready(self):
        self.changed.set()
        self.button.configure(state='disabled')
        self.refresh()

    def report_progress(self, status, *, rounds=0):
        with self.lock:
            self.progress = {'status': status, 'rounds': rounds}

    def refresh(self):
        with self.lock:
            progress = dict(self.progress)
        if progress['status'] == 'running':
            text = f"测试进行中：已完成 {progress['rounds']}/12 次。请持续按住左方向键，并移动、点击鼠标。"
        elif self.changed.is_set():
            text = '已确认，正在开始本轮测试。请按住左方向键，并移动、点击鼠标。'
        elif progress['status'] == 'waiting_for_participant':
            text = '后台已就绪。请点击“准备好了”开始。'
        else:
            text = '正在准备后台测试，请稍候。'
        self.message.set(text)

    def finish(self, result):
        messages = {
            'checks_passed_pending_participant_confirmation': '12/12 次测试已完成，键鼠参与记录齐全，等待本人确认。',
            'smoke_passed_no_physical_claim': '12/12 次自动冒烟已完成；本批不作实体并发结论。',
            'incomplete_participation': '12/12 次后台动作已完成，但键鼠参与不足，本次未通过实体并发验收。',
            'incomplete_no_participant': '等待操作超时，本次测试未开始。',
            'cancelled': '测试已取消，后台进程已收尾。',
        }
        self.message.set(messages.get(result['status'], '测试失败，后台未能完成本轮。请保留结果目录供排查。'))
        self.button.configure(text='关闭', state='normal', command=self.root.destroy)
        self.root.protocol('WM_DELETE_WINDOW', self.root.destroy)

    def event(self, name, value=None):
        with self.lock:
            if name == 'left':
                if value and not self.state['left']:
                    self.state['left_presses'] += 1
                self.state['left'] = value
            else:
                self.state[name] += 1

    def snapshot(self):
        with self.lock:
            return dict(self.state)


def run(output, binaries, witness, *, smoke=False):
    from gameqakit.sessions import write_json
    from tests.native_input_mcp import mcp
    from tests.native_input_safety import owned, connect
    from tests.native_input_smoke import read, until
    u = ctypes.WinDLL('user32', use_last_error=True)
    u.GetForegroundWindow.restype = wintypes.HWND
    u.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    u.GetAncestor.restype = wintypes.HWND
    u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    witness_handle = u.GetAncestor(witness.handle, 2)
    output.mkdir(parents=True, exist_ok=False)
    result = {'status': 'starting', 'physical_verified': False, 'samples': []}
    artifacts = [Path(__file__), ROOT / 'tests/native_input_mcp.py',
                 *[binaries / name for name in ('target.exe', 'owned-host.exe', 'native-input.dll')]]
    result['artifacts'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in artifacts}
    try:
        case = output / 'entry'
        with owned(case, binaries) as (host, exe, checks):
            bridge, meta = connect(case, host, exe)
            if not smoke:
                result['status'] = 'waiting_for_participant'
                witness.report_progress('waiting_for_participant')
                write_json(output / 'progress.json', result)
                deadline = time.monotonic() + 120
                while not witness.changed.wait(.1):
                    if witness.cancelled.is_set():
                        raise InterruptedError('participant_cancelled')
                    if host.poll() is not None:
                        raise RuntimeError('host_exited_while_waiting')
                    if time.monotonic() >= deadline:
                        result['status'] = 'incomplete_no_participant'
                        return result
                # Ignore the start-button click in the mouse participation count.
                baseline_witness = witness.snapshot()
            else:
                baseline_witness = witness.snapshot()
            witness.report_progress('running')
            with mcp(case, exe) as rpc:
                before = read(case / 'target-state.json')
                images = []
                for index in range(12):
                    if witness.cancelled.is_set():
                        raise InterruptedError('participant_cancelled')
                    rpc.call('control_resume', confirm=True, command_id=f'resume-{index}')
                    stop = threading.Event()
                    sampling_errors = []

                    def sample():
                        try:
                            while not stop.wait(.04):
                                target = read(case / 'target-state.json')
                                state = witness.snapshot()
                                foreground = u.GetForegroundWindow()
                                pid = wintypes.DWORD()
                                u.GetWindowThreadProcessId(foreground, ctypes.byref(pid))
                                result['samples'].append({'round': index, 'monotonic': time.monotonic(),
                                    'down': target['down'], 'position': target['position'],
                                    'last_scan_code': target['last_scan_code'], 'left': state['left'],
                                    'foreground': foreground == witness_handle, 'target_foreground': pid.value == meta['pid']})
                        except BaseException as exc:
                            sampling_errors.append(repr(exc))

                    sampler = threading.Thread(target=sample)
                    sampler.start()
                    try:
                        action = rpc.call('control_action', confirm=True, action='move', direction='right',
                                          duration_ms=700, command_id=f'right-{index}')
                    finally:
                        stop.set()
                        sampler.join(3)
                    if sampler.is_alive() or sampling_errors:
                        raise RuntimeError(f'sampling_failed: {sampling_errors}')
                    if not (action['press_consumed'] and action['release_consumed']):
                        raise RuntimeError('input_consumption_unconfirmed')
                    images.append(action['screenshot_sha256'])
                    witness.report_progress('running', rounds=index + 1)
                    write_json(output / 'progress.json', {**result, 'status': 'running', 'rounds': index + 1})
                rpc.call('control_pause')
                until(lambda: bridge.status().get('paused') and bridge.status().get('held') == [], 2)
                paused = rpc.call('control_status')
                after = read(case / 'target-state.json')
                state = witness.snapshot()
                for name in ('left_presses', 'right_presses', 'motion', 'clicks'):
                    state[name] -= baseline_witness[name]
                result.update(assess(before, after, state, result['samples'], rounds=12, smoke=smoke))
                checks.update({key: value for key, value in result['checks'].items()
                               if key not in {'concurrent_left_and_right', 'mouse_participation', 'keyboard_participation'}})
                checks['paused_and_released'] = paused['paused'] and not paused['held'] and not after['down']
                checks['distinct_action_images'] = len(set(images)) == 12
                result.update(before=before, after=after, witness=state, images=images)
        result['lifecycle'] = read(case / 'result.json')
    except BaseException as exc:
        result['status'] = 'cancelled' if isinstance(exc, InterruptedError) else 'failed'
        result['error'] = repr(exc)
    finally:
        write_json(output / 'result.json', result)
    return result


def main():
    import tkinter as tk
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--binaries', type=Path, required=True)
    parser.add_argument('--smoke', action='store_true', help='不要求人工参与，也不作实体并发结论')
    args = parser.parse_args()
    if os.name != 'nt':
        raise RuntimeError('Windows is required')
    root = tk.Tk()
    witness = Witness(root)
    root.update_idletasks()
    witness.handle = root.winfo_id()
    outcome = []

    def worker():
        try:
            outcome.append(run(args.output.resolve(), args.binaries.resolve(), witness, smoke=args.smoke))
        except BaseException as exc:
            outcome.append({'status': 'failed', 'error': repr(exc)})

    thread = threading.Thread(target=worker)
    thread.start()

    def poll():
        if not thread.is_alive():
            result = outcome[0]
            print(result['status'], str(args.output), flush=True)
            if args.smoke:
                root.destroy()
            else:
                witness.finish(result)
        else:
            witness.refresh()
            root.after(100, poll)
    root.after(100, poll)
    root.mainloop()
    thread.join()
    return 0 if outcome and outcome[0]['status'] in ('checks_passed_pending_participant_confirmation', 'smoke_passed_no_physical_claim') else 1


if __name__ == '__main__':
    raise SystemExit(main())
