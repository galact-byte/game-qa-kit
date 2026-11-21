"""Bound shared-memory and file channel for the registered Bakin runtime; never sends desktop input."""
import ctypes
from ctypes import wintypes as W
import hashlib
import io
import json
import os
from pathlib import Path
import re
import time
import uuid

from PIL import Image
from gameqakit.admission import no_links
from gameqakit.duration_host import read, publish

_SLOT_SIZE = 65536
_SLOT_MAX_BYTES = _SLOT_SIZE - 8
_SLOTS = {'status': 0, 'lease': 1, 'diagnostic': 2, 'supervisor': 3}
_TOTAL_SIZE = len(_SLOTS) * _SLOT_SIZE


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [('nLength', W.DWORD), ('lpSecurityDescriptor', W.LPVOID), ('bInheritHandle', W.BOOL)]


def _current_user_sddl():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel.GetCurrentProcess.restype = W.HANDLE
    kernel.CloseHandle.argtypes = [W.HANDLE]
    kernel.LocalFree.argtypes = [W.HLOCAL]
    kernel.LocalFree.restype = W.HLOCAL
    advapi.OpenProcessToken.argtypes = [W.HANDLE, W.DWORD, ctypes.POINTER(W.HANDLE)]
    advapi.OpenProcessToken.restype = W.BOOL
    advapi.GetTokenInformation.argtypes = [W.HANDLE, W.DWORD, W.LPVOID, W.DWORD, ctypes.POINTER(W.DWORD)]
    advapi.GetTokenInformation.restype = W.BOOL
    advapi.ConvertSidToStringSidW.argtypes = [W.LPVOID, ctypes.POINTER(W.LPWSTR)]
    advapi.ConvertSidToStringSidW.restype = W.BOOL
    token = W.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        needed = W.DWORD(0)
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
        buf = ctypes.create_string_buffer(needed.value)
        if not advapi.GetTokenInformation(token, 1, buf, needed.value, ctypes.byref(needed)):
            raise ctypes.WinError(ctypes.get_last_error())
        sid_ptr = ctypes.c_void_p.from_buffer(buf).value
        sid_str = W.LPWSTR()
        if not advapi.ConvertSidToStringSidW(sid_ptr, ctypes.byref(sid_str)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return f'D:P(A;;GA;;;SY)(A;;GA;;;{sid_str.value})'
        finally:
            kernel.LocalFree(sid_str)
    finally:
        kernel.CloseHandle(token)


class _LocalMailbox:
    """In-process fallback used only on non-Windows hosts for unit tests."""
    def __init__(self):
        self.slots = {}

    def read_json(self, name):
        raw = self.slots.get(name)
        return json.loads(raw) if raw else None

    def write_json(self, name, value):
        raw = json.dumps(value, ensure_ascii=False).encode('utf-8')
        if len(raw) > _SLOT_MAX_BYTES:
            raise ValueError('ipc_message_too_large')
        self.slots[name] = raw.decode('utf-8')

    def close(self):
        self.slots.clear()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class BakinMailbox:
    """Pagefile-backed named shared memory with single-writer lock-free seqlock slots."""
    def __init__(self, pid, session, *, create):
        self.handle = self.address = None
        if (type(pid) is not int or pid <= 0 or not isinstance(session, str)
                or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', session)):
            raise ValueError('invalid_ipc_binding')
        self.kernel = k = ctypes.WinDLL('kernel32', use_last_error=True)
        k.CreateFileMappingW.argtypes = [W.HANDLE, ctypes.POINTER(_SecurityAttributes), W.DWORD, W.DWORD, W.DWORD, W.LPCWSTR]
        k.CreateFileMappingW.restype = W.HANDLE
        k.OpenFileMappingW.argtypes = [W.DWORD, W.BOOL, W.LPCWSTR]
        k.OpenFileMappingW.restype = W.HANDLE
        k.MapViewOfFile.argtypes = [W.HANDLE, W.DWORD, W.DWORD, W.DWORD, ctypes.c_size_t]
        k.MapViewOfFile.restype = W.LPVOID
        k.UnmapViewOfFile.argtypes = [W.LPCVOID]
        k.CloseHandle.argtypes = [W.HANDLE]
        k.LocalFree.argtypes = [W.HLOCAL]
        k.LocalFree.restype = W.HLOCAL
        self.name = f'Local\\gameqakit-bakin-{pid}-{session}'
        if create:
            advapi = ctypes.WinDLL('advapi32', use_last_error=True)
            advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
                W.LPCWSTR, W.DWORD, ctypes.POINTER(W.LPVOID), ctypes.POINTER(W.ULONG)]
            advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = W.BOOL
            descriptor = W.LPVOID()
            if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                    _current_user_sddl(), 1, ctypes.byref(descriptor), None):
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                sa = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), descriptor, False)
                self.handle = k.CreateFileMappingW(W.HANDLE(-1), ctypes.byref(sa), 0x04, 0, _TOTAL_SIZE, self.name)
                error = ctypes.get_last_error()
            finally:
                k.LocalFree(descriptor)
            if not self.handle or error == 183:
                if self.handle:
                    k.CloseHandle(self.handle)
                    self.handle = None
                raise ctypes.WinError(error or 183)
        else:
            self.handle = k.OpenFileMappingW(0x0006, False, self.name)
            if not self.handle:
                raise ctypes.WinError(ctypes.get_last_error())
        self.address = k.MapViewOfFile(self.handle, 0x0006, 0, 0, _TOTAL_SIZE)
        if not self.address:
            error = ctypes.get_last_error()
            self.close()
            raise ctypes.WinError(error)

    @classmethod
    def create(cls, pid, session):
        return cls(pid, session, create=True)

    @classmethod
    def open_existing(cls, pid, session):
        return cls(pid, session, create=False)

    def close(self):
        if self.address:
            self.kernel.UnmapViewOfFile(self.address)
            self.address = None
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def __del__(self):
        self.close()

    def _slot(self, name):
        if not self.address:
            raise RuntimeError('ipc_closed')
        address = self.address + _SLOTS[name] * _SLOT_SIZE
        return address, ctypes.c_int32.from_address(address), W.DWORD.from_address(address + 4)

    def read_json(self, name):
        address, sequence, length_field = self._slot(name)
        for _ in range(8):
            before = sequence.value
            if before == 0:
                return None
            if before & 1:
                time.sleep(0)
                continue
            length = length_field.value
            if length == 0 or length > _SLOT_MAX_BYTES:
                if sequence.value != before:
                    continue
                raise ValueError('ipc_message_too_large')
            raw = ctypes.string_at(address + 8, length)
            after = sequence.value
            if after == before:
                value = json.loads(raw.decode('utf-8'))
                if not isinstance(value, dict):
                    raise ValueError('ipc_message_invalid')
                return value
        return None

    def write_json(self, name, value):
        raw = json.dumps(value, ensure_ascii=False).encode('utf-8')
        if len(raw) > _SLOT_MAX_BYTES:
            raise ValueError('ipc_message_too_large')
        address, sequence, length_field = self._slot(name)
        before = sequence.value
        if before & 1:
            raise RuntimeError('ipc_writer_busy')
        odd = ctypes.c_int32(before + 1).value
        even = ctypes.c_int32(before + 2).value
        if even == 0:
            even = 2
        sequence.value = odd
        ctypes.memmove(address + 8, raw, len(raw))
        length_field.value = len(raw)
        sequence.value = even


def clock_ms():
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.GetTickCount64.restype=ctypes.c_ulonglong
    return kernel.GetTickCount64()


class TargetChannel:
    def __init__(self,directory,session,pid,creation,*,clock=clock_ms):
        self.root=Path(directory)
        self.clock=clock
        self.identity=dict(protocol=2,bridge_version=3,session_id=session,pid=pid,creation_id=creation)
        self.sequence=self.sample_sequence=self.draw_sequence=self.capture_sequence=0
        self.pending_capture=None
        self.status={}
        self.action_timing=None
        self.mailbox=BakinMailbox.create(pid,session) if os.name=='nt' else _LocalMailbox()
        try:
            for name in ('commands','screenshots'):
                no_links(self.root/name)
                (self.root/name).mkdir(exist_ok=True)
            publish(self.root/'binding.json',self.identity)
        except Exception:
            self.close()
            raise

    def close(self):
        if getattr(self,'mailbox',None) is not None:
            self.mailbox.close()
            self.mailbox=None

    def __enter__(self):
        return self

    def __exit__(self,*args):
        self.close()

    def __del__(self):
        self.close()

    def bound(self,value):
        if (not isinstance(value,dict) or any(type(value.get(k)) is not type(v) or value[k]!=v
                for k,v in self.identity.items())):
            raise ValueError('Bakin target identity mismatch')

    def observe(self):
        value=self.mailbox.read_json('status')
        if not value:return None
        self.bound(value)
        for key in ('published_at_ms','command_seq','sample_seq','draw_seq','held'):
            if type(value.get(key)) is not int or value[key]<0:raise ValueError('Invalid Bakin target counter')
        age=self.clock()-value['published_at_ms']
        if (not -50<=age<1000
                or value['command_seq']>self.sequence or value['command_seq']<self.status.get('command_seq',0)
                or value['sample_seq']<self.sample_sequence or value['draw_seq']<self.draw_sequence
                or type(value.get('ready')) is not bool or type(value.get('paused')) is not bool or value['held'] & ~63
                or not isinstance(value.get('scene'),str) or len(value['scene'])>64
                or value.get('error') is not None and not isinstance(value['error'],str)):
            raise ValueError('Stale or invalid Bakin target status: '+str(dict(age_ms=age,
                command=(value['command_seq'],self.status.get('command_seq',0),self.sequence),
                sample=(value['sample_seq'],self.sample_sequence),draw=(value['draw_seq'],self.draw_sequence),
                ready=value.get('ready'),paused=value.get('paused'),held=value['held'],scene=value.get('scene'),
                error=value.get('error'))))
        self.validate_loading(value)
        self.sample_sequence,self.draw_sequence=value['sample_seq'],value['draw_seq']
        self.status=value
        return value

    @staticmethod
    def validate_loading(value):
        if 'loading_protocol' not in value:return
        if (type(value['loading_protocol']) is not int or value['loading_protocol']!=1
                or not isinstance(value.get('title_state'),str) or len(value['title_state'])>64
                or type(value.get('map_loading')) is not bool
                or not isinstance(value.get('map_fade'),str) or len(value['map_fade'])>64):
            raise ValueError('Invalid Bakin loading state')

    def lease(self):
        self.mailbox.write_json('lease',dict(self.identity,expires_at_ms=self.clock()+1500))

    def command(self,kind,*,action_id='',key=0,duration=0):
        if self.sequence!=self.status.get('command_seq',0):raise RuntimeError('Previous Bakin command is unacknowledged')
        now=self.clock()
        if kind=='down' and (type(duration) is not int or not 1<=duration<=1000
                or type(key) is not int or key not in (1,2,4,8,16,32)
                or not isinstance(action_id,str) or not re.fullmatch('[A-Za-z0-9_-]{1,64}',action_id)
                or now+1000+duration>2**63-1):
            raise ValueError('Invalid Bakin action timing or key')
        self.sequence+=1
        message=dict(self.identity,sequence=self.sequence,id=action_id or uuid.uuid4().hex,
                     kind=kind,key=key if kind=='down' else 0,expires_at_ms=now+1000,
                     duration_ms=duration if kind=='down' else 0,
                     start_deadline_ms=now+1000 if kind=='down' else 0,
                     hard_stop_at_ms=now+1000+duration if kind=='down' else 0)
        publish(self.root/'commands'/('%020d.json'%self.sequence),message)
        if kind=='down':self.action_timing=dict(message,issued_at_ms=now)
        return self.sequence

    def acknowledged(self,kind):
        return self.status.get('command_seq')==self.sequence and self.status.get('command_kind')==kind

    def evidence(self,value,ident):
        return (isinstance(value,dict) and value.get('action_id')==ident
            and all(type(value.get(k)) is int for k in ('sample_seq','query_type','game_state','at_ms','value','hold_until_ms'))
            and 0<value['sample_seq']<=self.sample_sequence and 0<=value['query_type']<=4
            and 0<=value['game_state']<=2 and 0<=value['at_ms']<=self.clock() and 0<=value['value']<=63)

    def action_pressed(self,ident,key):
        press=self.status.get('press')
        timing=self.action_timing
        return bool(timing and timing['id']==ident and timing['key']==key and self.evidence(press,ident)
            and press['query_type']!=2 and press['value'] & key
            and timing['issued_at_ms']<=press['at_ms']<timing['start_deadline_ms']
            and press['hold_until_ms']==press['at_ms']+timing['duration_ms']
            and press['hold_until_ms']<=timing['hard_stop_at_ms'])

    def action_released(self,ident,key):
        press,release=self.status.get('press'),self.status.get('release')
        return bool(self.action_pressed(ident,key) and self.evidence(release,ident)
            and release['value'] & key==0 and release['sample_seq']>=press['sample_seq']
            and release['at_ms']>=press['at_ms'] and self.status.get('held')==0)

    def request_capture(self,ident=None):
        if self.pending_capture:raise RuntimeError('Bakin capture already pending')
        self.capture_sequence+=1
        self.pending_capture=dict(sequence=self.capture_sequence,id=ident or uuid.uuid4().hex,
                                  expires_at_ms=self.clock()+3000,after_draw_seq=self.draw_sequence)
        message={k:v for k,v in self.pending_capture.items() if k!='after_draw_seq'}
        publish(self.root/'target-capture.json',dict(self.identity,**message))

    def capture_result(self,*,allow_blank=False):
        pending=self.pending_capture
        if not pending:return None
        if self.clock()>=pending['expires_at_ms']:raise TimeoutError('Bakin capture exceeded 3 seconds')
        no_links(self.root/'target-capture-result.json')
        value=read(self.root/'target-capture-result.json')
        if not value:return None
        self.bound(value)
        if value.get('sequence',0)<pending['sequence']:return None
        relative='screenshots/%08d.png'%pending['sequence']
        if (value.get('sequence')!=pending['sequence'] or value.get('id')!=pending['id']
                or value.get('screenshot')!=relative or type(value.get('draw_seq')) is not int
                or value['draw_seq']<=pending['after_draw_seq']
                or type(value.get('captured_at_ms')) is not int
                or not pending['expires_at_ms']-3000<=value['captured_at_ms']<pending['expires_at_ms']
                or not isinstance(value.get('sha256'),str) or not re.fullmatch('[0-9a-f]{64}',value['sha256'])):
            raise ValueError('Invalid Bakin capture receipt')
        self.validate_loading(value)
        snapshot=value.get('input_snapshot')
        if 'input_snapshot' in value:
            if (not isinstance(snapshot,dict)
                    or type(snapshot.get('held')) is not int or not 0<=snapshot['held']<=63
                    or type(snapshot.get('sample_seq')) is not int or snapshot['sample_seq']<0
                    or type(snapshot.get('at_ms')) is not int
                    or not pending['expires_at_ms']-3000<=snapshot['at_ms']<=value['captured_at_ms']
                    or snapshot.get('action_id') is not None and (
                        not isinstance(snapshot['action_id'],str)
                        or not re.fullmatch('[A-Za-z0-9_-]{1,80}',snapshot['action_id']))):
                raise ValueError('Invalid Bakin capture input snapshot')
        path=self.root/relative;no_links(path)
        if path.stat().st_size>16*1024*1024:raise ValueError('Oversized Bakin capture')
        body=path.read_bytes()
        if hashlib.sha256(body).hexdigest()!=value['sha256']:raise ValueError('Bakin capture digest mismatch')
        with Image.open(io.BytesIO(body)) as image:
            width,height=image.size
            if image.format!='PNG' or min(width,height)<=2 or width*height>16777216:
                raise RuntimeError('Bakin capture invalid')
            pixels=image.convert('RGB')
        total=(width-2)*(height-2);black=white=count=0
        for index in range(0,total,max(1,total//20000)):
            color=pixels.getpixel((index%(width-2)+1,index//(width-2)+1))
            black+=color==(0,0,0);white+=color==(255,255,255);count+=1
        blank=max(black,white)/count>=.999
        if blank and not allow_blank:raise RuntimeError('Bakin capture invalid: black or white frame')
        self.pending_capture=None
        return pixels,dict(value,blank_frame=blank)
