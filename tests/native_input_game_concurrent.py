"""固定商业游戏的实体键鼠并发见证；仅正常菜单输入，保留待确认与待阅图状态。"""
import argparse
import os
from pathlib import Path
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests.native_input_concurrent import Witness
from tests.native_input_game import run


class GameWitness(Witness):
    def __init__(self, root):
        super().__init__(root, total=16, instructions=(
            '点击“准备好了”后，持续按住键盘左方向键；\n'
            '同时在本窗口移动鼠标，并点击至少两次。\n'
            '后台游戏进入环境设置，观察约 8 秒后返回标题。'))
        root.title('GameQA — 正式游戏实体并发见证')
        self.state['unexpected_keys'] = 0
        self.baseline = None
        root.bind('<KeyPress>', self.key_event)

    def key_event(self, event):
        if event.keysym.lower() != 'left':
            self.event('unexpected_keys')

    def ready(self):
        self.baseline = self.snapshot()
        super().ready()


def main():
    import tkinter as tk
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--binaries', type=Path, required=True)
    parser.add_argument('--smoke', action='store_true', help='隐藏见证窗口，不模拟真人，不作实体并发结论')
    args = parser.parse_args()
    if os.name != 'nt':
        raise RuntimeError('Windows is required')
    root = tk.Tk()
    if args.smoke:
        root.withdraw()
    witness = GameWitness(root)
    root.update_idletasks()
    witness.handle = root.winfo_id()
    outcome = []

    def worker():
        try:
            outcome.append(run(args.source.resolve(), args.output.resolve(), args.binaries.resolve(),
                               witness=witness, smoke=args.smoke))
        except BaseException as exc:
            outcome.append({'status': 'failed', 'error': repr(exc)})

    thread = threading.Thread(target=worker)
    thread.start()

    def poll():
        if thread.is_alive():
            witness.refresh()
            root.after(100, poll)
        else:
            result = outcome[0]
            print(result['status'], str(args.output), result.get('error', ''), flush=True)
            if args.smoke:
                root.destroy()
            else:
                witness.finish(result)
    root.after(100, poll)
    root.mainloop()
    thread.join()
    return 0 if outcome and outcome[0]['status'] in (
        'smoke_passed_no_physical_claim',
        'checks_passed_pending_participant_confirmation_and_visual_review') else 1


if __name__ == '__main__':
    raise SystemExit(main())
