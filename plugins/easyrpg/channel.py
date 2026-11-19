"""Private inherited mapping for the fixed EasyRPG source runtime."""
import ctypes
from ctypes import wintypes as W
import os
import secrets

K = ctypes.WinDLL('kernel32', use_last_error=True)
K.CreateFileMappingW.argtypes = [W.HANDLE, W.LPVOID, W.DWORD, W.DWORD, W.DWORD, W.LPCWSTR]
K.CreateFileMappingW.restype = W.HANDLE
K.MapViewOfFile.argtypes = [W.HANDLE, W.DWORD, W.DWORD, W.DWORD, ctypes.c_size_t]
K.MapViewOfFile.restype = W.LPVOID
K.UnmapViewOfFile.argtypes = [W.LPCVOID]
K.CreateMutexW.argtypes = [W.LPVOID, W.BOOL, W.LPCWSTR]
K.CreateMutexW.restype = W.HANDLE
K.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]
K.ReleaseMutex.argtypes = [W.HANDLE]
K.CloseHandle.argtypes = [W.HANDLE]
K.GetTickCount64.restype = ctypes.c_uint64
MAGIC = 0x4751414550320001
FIELDS = ('magic', 'version', 'session', 'request_session', 'seq', 'kind', 'lease_until', 'expires',
          'ack_seq', 'ack_kind', 'frame', 'held', 'paused', 'pid', 'ready', 'reserved')


class Channel:
    def __init__(self):
        self.mapping = self.mutex = self.address = None
        self.token = secrets.randbits(63) or 1
        self.seq = 0
        try:
            self.mapping = K.CreateFileMappingW(W.HANDLE(-1), None, 4, 0, 128, None)
            self.mutex = K.CreateMutexW(None, False, None)
            if not self.mapping or not self.mutex:
                raise ctypes.WinError(ctypes.get_last_error())
            self.address = K.MapViewOfFile(self.mapping, 0xF001F, 0, 0, 128)
            if not self.address:
                raise ctypes.WinError(ctypes.get_last_error())
            self.data = (ctypes.c_uint64 * 16).from_address(self.address)
            self.data[:4] = [MAGIC, 1, self.token, self.token]
            self.data[12] = 1
            for handle in self.handles:
                os.set_handle_inheritable(handle, True)
        except BaseException:
            self.close()
            raise

    @property
    def handles(self):
        return self.mapping, self.mutex

    @property
    def env(self):
        return dict(GAMEQAKIT_EASYRPG_MAPPING=str(self.mapping), GAMEQAKIT_EASYRPG_MUTEX=str(self.mutex),
                    GAMEQAKIT_EASYRPG_TOKEN=str(self.token))

    def exchange(self, *, kind=None, lease_ms=None):
        result = K.WaitForSingleObject(self.mutex, 10)
        if result != 0:
            if result == 0x80:
                K.ReleaseMutex(self.mutex)
            raise RuntimeError('EasyRPG shared channel unavailable')
        try:
            if kind is not None:
                self.seq += 1
                self.data[3:6] = [self.token, self.seq, kind]
                self.data[7] = K.GetTickCount64() + 1500
            if lease_ms is not None:
                self.data[6] = K.GetTickCount64() + lease_ms if lease_ms > 0 else 0
            return dict(zip(FIELDS, self.data))
        finally:
            K.ReleaseMutex(self.mutex)

    def close(self):
        if self.address:
            K.UnmapViewOfFile(self.address)
            self.address = None
        for name in ('mapping', 'mutex'):
            if getattr(self, name, None):
                K.CloseHandle(getattr(self, name))
                setattr(self, name, None)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
