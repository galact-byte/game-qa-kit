"""test_process_job 的自有目标；不操作游戏与用户数据。"""
from pathlib import Path
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gameqakit.process_job import OwnedProcess

root, mode = Path(sys.argv[1]), sys.argv[2]
if mode == 'creator':
    with OwnedProcess([sys.executable, __file__, str(root), 'worker'], cwd=str(root)) as child:
        child.run()
        while True:
            time.sleep(.1)
elif mode == 'worker':
    with OwnedProcess([sys.executable, __file__, str(root), 'host'], cwd=str(root)) as child:
        child.run()
        while True:
            time.sleep(.1)
else:
    with subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']) as target:
        ids = {'worker': os.getppid(), 'host': os.getpid(), 'target': target.pid}
        (root / 'pids.json').write_text(json.dumps(ids), encoding='utf-8')
        (root / 'ready').touch()
        time.sleep(30)
