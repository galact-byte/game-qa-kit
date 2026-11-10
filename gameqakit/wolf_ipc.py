"""Wolf 专用有界共享内存传输；只打开桥创建的会话对象，不创建或回退。"""
import ctypes
from ctypes import wintypes as W
import re

_SLOT_SIZE = 65536
_SLOTS = {'request': 0, 'lease': 1, 'pause': 2, 'status': 3, 'result': 4}


class WolfMailbox:
    def __init__(self, pid, session):
        self.address = self.handle = None
        self.locks = []
        if (type(pid) is not int or pid <= 0 or not isinstance(session, str)
                or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', session)):
            raise ValueError('invalid_ipc_binding')
        self.kernel = k = ctypes.WinDLL('kernel32', use_last_error=True)
        k.OpenFileMappingW.argtypes = [W.DWORD, W.BOOL, W.LPCWSTR]
        k.OpenFileMappingW.restype = W.HANDLE
        k.MapViewOfFile.argtypes = [W.HANDLE, W.DWORD, W.DWORD, W.DWORD, ctypes.c_size_t]
        k.MapViewOfFile.restype = W.LPVOID
        k.UnmapViewOfFile.argtypes = [W.LPCVOID]
        k.CloseHandle.argtypes = [W.HANDLE]
        k.OpenMutexW.argtypes = [W.DWORD, W.BOOL, W.LPCWSTR]
        k.OpenMutexW.restype = W.HANDLE
        k.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]
        k.WaitForSingleObject.restype = W.DWORD
        k.ReleaseMutex.argtypes = [W.HANDLE]
        name = f'Local\\gameqakit-wolf-{pid}-{session}'
        self.handle = k.OpenFileMappingW(6, False, name)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self.address = k.MapViewOfFile(self.handle, 6, 0, 0, 5 * _SLOT_SIZE)
        if not self.address:
            error = ctypes.get_last_error()
            self.close()
            raise ctypes.WinError(error)
        for index in range(5):
            lock = k.OpenMutexW(0x00100001, False, name + '-' + str(index))
            if not lock:
                error = ctypes.get_last_error()
                self.close()
                raise ctypes.WinError(error)
            self.locks.append(lock)

    def close(self):
        for lock in self.locks:
            self.kernel.CloseHandle(lock)
        self.locks.clear()
        if self.address:
            self.kernel.UnmapViewOfFile(self.address)
            self.address = None
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None

    def __del__(self):
        self.close()

    def _slot(self, name):
        if not self.address:
            raise RuntimeError('ipc_closed')
        address = self.address + _SLOTS[name] * _SLOT_SIZE
        return address, ctypes.cast(address, ctypes.POINTER(W.LONG))

    def _acquire(self, name):
        result = self.kernel.WaitForSingleObject(self.locks[_SLOTS[name]], 0)
        if result == 0x80:
            self.kernel.ReleaseMutex(self.locks[_SLOTS[name]])
            raise RuntimeError('ipc_writer_abandoned')
        if result not in (0, 0x102):
            raise RuntimeError('ipc_mutex_failed')
        return result == 0

    def read(self, name):
        address, sequence = self._slot(name)
        if not self._acquire(name):
            return ''
        try:
            if sequence.contents.value & 1:
                return ''
            length = W.DWORD.from_address(address + 4).value
            if length > _SLOT_SIZE - 8:
                raise RuntimeError('ipc_message_too_large')
            return ctypes.string_at(address + 8, length).decode('utf-8')
        finally:
            self.kernel.ReleaseMutex(self.locks[_SLOTS[name]])

    def write(self, name, text):
        value = text.encode('utf-8')
        if len(value) > _SLOT_SIZE - 8:
            raise ValueError('ipc_message_too_large')
        address, sequence = self._slot(name)
        if not self._acquire(name):
            raise RuntimeError('ipc_writer_busy')
        try:
            before = sequence.contents.value
            if before & 1:
                raise RuntimeError('ipc_writer_busy')
            sequence.contents.value = W.LONG(before + 1).value
            ctypes.memmove(address + 8, value, len(value))
            W.DWORD.from_address(address + 4).value = len(value)
            sequence.contents.value = W.LONG(before + 2).value
        finally:
            self.kernel.ReleaseMutex(self.locks[_SLOTS[name]])
