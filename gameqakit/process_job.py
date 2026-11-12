"""自有进程树的 Job 所有权；句柄不继承，分配完成前不恢复目标。"""
from .desktop import DesktopProcess, k


class OwnedProcess(DesktopProcess):
    def __init__(self, argv, *, cwd, env=None, isolated_desktop=False):
        super().__init__(argv, cwd=cwd, env=env, isolated_desktop=isolated_desktop)
