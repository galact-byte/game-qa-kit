"""Wolf 显式启动出图；仅用于本次自有独立桌面 Job。"""
import ctypes
from ctypes import wintypes as W
import threading
import time


def initialize(process, client, *, command_id='startup'):
    """连接须在注入前选择 wolf_initialize=1；失败立即回收自有 Job。"""
    from gameqakit.desktop import k
    if not process.job or not process.alive() or process.pid != client.pid:
        raise ValueError('owned_startup_process_required')
    k.TerminateJobObject.argtypes = [W.HANDLE, W.UINT]
    k.TerminateJobObject.restype = W.BOOL
    job = process.job
    expired = threading.Event()
    stop = threading.Event()
    failures = []

    def terminate():
        if not k.TerminateJobObject(job, 0xE003):
            failures.append(ctypes.WinError(ctypes.get_last_error()))

    # Start before querying status: a blocked status/PNG read cannot extend the budget.
    started = time.monotonic()
    def watch():
        if not stop.wait(max(0, 3.0 - (time.monotonic() - started))):
            expired.set()
            terminate()
    watchdog = threading.Thread(target=watch, name='wolf-startup-deadline', daemon=True)
    watchdog.start()
    try:
        state = client.status()
        if (not state.get('connected') or not state.get('startup_pending') or state.get('frame') != 0
                or not state.get('at_frame_boundary') or not state.get('paused') or state.get('held') != []
                or state.get('busy') or state.get('error')):
            raise RuntimeError('wolf_startup_not_ready')
        result = client.resume(command_id=command_id, timeout=3.0)
        final = client.status()
        if expired.is_set() or time.monotonic() - started >= 3.0:
            raise TimeoutError('wolf_startup_deadline')
        if (not final.get('connected') or not final.get('paused') or final.get('held') != []
                or not final.get('at_frame_boundary') or final.get('busy') or final.get('capture_pending')
                or final.get('startup_active') or final.get('startup_pending') or final.get('error')
                or final.get('frame') != result.get('frame') or not 1 <= final['frame'] <= 120
                or final.get('held_polls') != 0):
            raise RuntimeError('wolf_startup_not_paused')
        return result
    except BaseException:
        terminate()
        raise
    finally:
        stop.set()
        watchdog.join()
        if failures:
            raise RuntimeError('wolf_startup_reap_failed') from failures[0]
