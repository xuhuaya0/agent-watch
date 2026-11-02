"""Local Windows desktop IPC, with peer ownership and no impersonation grants."""
import ctypes as C
from ctypes import wintypes as W
import os
import re
import threading
from .broker import ControlError


def validate_path(path):
    if not isinstance(path, str) or not re.fullmatch(r'\\\\\.\\pipe\\codex-ipc(?:-[A-Za-z0-9-]+)?', path):
        raise ControlError('codex_unsafe_pipe')
    return path


class Overlapped(C.Structure):
    _fields_ = [('Internal', C.c_size_t), ('InternalHigh', C.c_size_t),
                ('Offset', W.DWORD), ('OffsetHigh', W.DWORD), ('hEvent', W.HANDLE)]


class NamedPipe:
    def __init__(self, path):
        validate_path(path)
        if os.name != 'nt':
            raise ControlError('codex_desktop_ipc_unavailable')
        self.k = C.WinDLL('kernel32', use_last_error=True)
        self.a = C.WinDLL('advapi32', use_last_error=True)
        self.handle = None
        self.close_lock = threading.Lock()
        signatures = {
            'CreateFileW': ([W.LPCWSTR,W.DWORD,W.DWORD,W.LPVOID,W.DWORD,W.DWORD,W.HANDLE],W.HANDLE),
            'WaitNamedPipeW': ([W.LPCWSTR,W.DWORD],W.BOOL),
            'GetNamedPipeServerProcessId': ([W.HANDLE,C.POINTER(W.ULONG)],W.BOOL),
            'OpenProcess': ([W.DWORD,W.BOOL,W.DWORD],W.HANDLE),
            'GetCurrentProcess': ([],W.HANDLE),
            'CloseHandle': ([W.HANDLE],W.BOOL),
            'CreateEventW': ([W.LPVOID,W.BOOL,W.BOOL,W.LPCWSTR],W.HANDLE),
            'ReadFile': ([W.HANDLE,W.LPVOID,W.DWORD,C.POINTER(W.DWORD),C.POINTER(Overlapped)],W.BOOL),
            'WriteFile': ([W.HANDLE,W.LPVOID,W.DWORD,C.POINTER(W.DWORD),C.POINTER(Overlapped)],W.BOOL),
            'GetOverlappedResult': ([W.HANDLE,C.POINTER(Overlapped),C.POINTER(W.DWORD),W.BOOL],W.BOOL),
            'WaitForSingleObject': ([W.HANDLE,W.DWORD],W.DWORD),
            'CancelIoEx': ([W.HANDLE,C.POINTER(Overlapped)],W.BOOL),
        }
        for name,(args,result) in signatures.items():
            fn=getattr(self.k,name);fn.argtypes=args;fn.restype=result
        self.a.OpenProcessToken.argtypes=[W.HANDLE,W.DWORD,C.POINTER(W.HANDLE)]
        self.a.OpenProcessToken.restype=W.BOOL
        self.a.GetTokenInformation.argtypes=[W.HANDLE,C.c_int,W.LPVOID,W.DWORD,C.POINTER(W.DWORD)]
        self.a.GetTokenInformation.restype=W.BOOL
        self.a.EqualSid.argtypes=[W.LPVOID,W.LPVOID];self.a.EqualSid.restype=W.BOOL
        if not self.k.WaitNamedPipeW(path,5000):
            raise C.WinError(C.get_last_error())
        # OVERLAPPED | SECURITY_SQOS_PRESENT | SECURITY_IDENTIFICATION. The
        # server may identify this client, but cannot impersonate its token.
        self.handle=self.k.CreateFileW(path,0xC0000000,0,None,3,0x40110000,None)
        if self.handle == W.HANDLE(-1).value:
            self.handle=None
            raise C.WinError(C.get_last_error())
        try:
            self._verify_peer()
        except Exception:
            self.close()
            raise

    def _token_user(self, process):
        token=W.HANDLE()
        if not self.a.OpenProcessToken(process,8,C.byref(token)):
            raise C.WinError(C.get_last_error())
        try:
            size=W.DWORD()
            self.a.GetTokenInformation(token,1,None,0,C.byref(size))
            if not 0 < size.value <= 65536:
                raise ControlError('codex_unsafe_pipe')
            buf=C.create_string_buffer(size.value)
            if not self.a.GetTokenInformation(token,1,buf,size,C.byref(size)):
                raise C.WinError(C.get_last_error())
            return buf
        finally:
            self.k.CloseHandle(token)

    def _verify_peer(self):
        pid=W.ULONG()
        if not self.k.GetNamedPipeServerProcessId(self.handle,C.byref(pid)):
            raise ControlError('codex_unsafe_pipe')
        peer=self.k.OpenProcess(0x1000,False,pid)
        if not peer:
            raise ControlError('codex_unsafe_pipe')
        try:
            left=self._token_user(peer);right=self._token_user(self.k.GetCurrentProcess())
            if not self.a.EqualSid(C.cast(left,C.POINTER(W.LPVOID))[0],C.cast(right,C.POINTER(W.LPVOID))[0]):
                raise ControlError('codex_unsafe_pipe')
        finally:
            self.k.CloseHandle(peer)

    def _io(self, data=None, count=0):
        handle=self.handle
        if handle is None:
            raise OSError('pipe closed')
        buf=C.create_string_buffer(data) if data is not None else C.create_string_buffer(count)
        ov=Overlapped();ov.hEvent=self.k.CreateEventW(None,True,False,None)
        if not ov.hEvent:
            raise C.WinError(C.get_last_error())
        try:
            done=W.DWORD()
            fn=self.k.WriteFile if data is not None else self.k.ReadFile
            if not fn(handle,buf,len(data) if data is not None else count,None,C.byref(ov)):
                error=C.get_last_error()
                if error != 997:
                    raise C.WinError(error)
                wait=self.k.WaitForSingleObject(ov.hEvent,5000 if data is not None else 0xffffffff)
                if wait != 0:
                    self.k.CancelIoEx(handle,C.byref(ov))
                    self.k.GetOverlappedResult(handle,C.byref(ov),C.byref(done),True)
                    raise TimeoutError('pipe I/O did not complete')
            if not self.k.GetOverlappedResult(handle,C.byref(ov),C.byref(done),False):
                raise C.WinError(C.get_last_error())
            return done.value if data is not None else buf.raw[:done.value]
        finally:
            self.k.CloseHandle(ov.hEvent)

    def recv(self, count):
        return self._io(count=min(count,65536))

    def sendall(self, data):
        offset=0
        while offset<len(data):
            count=self._io(data=data[offset:offset+65536])
            if not count:
                raise OSError('pipe closed')
            offset+=count

    def shutdown(self, _how=None):
        if self.handle is not None:
            self.k.CancelIoEx(self.handle,None)

    def close(self):
        with self.close_lock:
            handle,self.handle=self.handle,None
            if handle is not None:
                self.k.CancelIoEx(handle,None)
                self.k.CloseHandle(handle)
