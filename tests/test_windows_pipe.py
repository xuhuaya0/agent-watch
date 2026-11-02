import ctypes as C
from ctypes import wintypes as W
import json
import os
import struct
import threading
import unittest
import uuid
from aw.control.broker import ControlError
from aw.control.windows_pipe import validate_path


class PipePathTests(unittest.TestCase):
    def test_only_local_codex_pipe_names_are_allowed(self):
        self.assertEqual(validate_path(r'\\.\pipe\codex-ipc'),r'\\.\pipe\codex-ipc')
        for path in (r'\\remote\pipe\codex-ipc', r'C:\private.txt',r'\\.\pipe\other',r'\\.\pipe\codex-ipc\child'):
            with self.assertRaises(ControlError):validate_path(path)

    def test_queue_wakes_via_registered_uri_without_shell_or_requeue(self):
        from aw.control.codex_queue import NativeQueue
        from types import SimpleNamespace
        sid='11111111-1111-4111-8111-111111111111'
        calls=[];uris=[]
        def run(args,**kwargs):
            calls.append(args)
            return SimpleNamespace(returncode=0,stdout=f'Queued message {sid} for thread {sid}.')
        queue=NativeQueue('codex.exe','profile',run,uris.append)
        self.assertEqual(queue(sid,'--literal & %(text)'), 'queued_native')
        self.assertEqual(uris,['codex://threads/wake/'+sid])
        self.assertEqual(calls,[['codex.exe','queue','--thread',sid,'--message=--literal & %(text)']])
        def failed(_):raise OSError('URI handler unavailable')
        self.assertEqual(NativeQueue('codex.exe','profile',run,failed)(sid,'hello'),'queued_unwoken')
        self.assertEqual(len(calls),2)


@unittest.skipUnless(os.name=='nt','Windows named pipe integration')
class NativePipeTests(unittest.TestCase):
    def test_real_pipe_framing_handshake_owner_and_disconnect(self):
        from aw.control.codex_ipc import DesktopRpc
        k=C.WinDLL('kernel32',use_last_error=True)
        k.CreateNamedPipeW.argtypes=[W.LPCWSTR,W.DWORD,W.DWORD,W.DWORD,W.DWORD,W.DWORD,W.DWORD,W.LPVOID]
        k.CreateNamedPipeW.restype=W.HANDLE
        k.ConnectNamedPipe.argtypes=[W.HANDLE,W.LPVOID];k.ConnectNamedPipe.restype=W.BOOL
        k.ReadFile.argtypes=[W.HANDLE,W.LPVOID,W.DWORD,C.POINTER(W.DWORD),W.LPVOID];k.ReadFile.restype=W.BOOL
        k.WriteFile.argtypes=[W.HANDLE,W.LPVOID,W.DWORD,C.POINTER(W.DWORD),W.LPVOID];k.WriteFile.restype=W.BOOL
        k.CloseHandle.argtypes=[W.HANDLE];k.CloseHandle.restype=W.BOOL
        name=r'\\.\pipe\codex-ipc-test-'+str(uuid.uuid4())
        handle=k.CreateNamedPipeW(name,3,0,1,65536,65536,5000,None)
        self.assertNotEqual(handle,W.HANDLE(-1).value)
        errors=[];received=[];finished=threading.Event()
        def read(count):
            data=b''
            while len(data)<count:
                buf=C.create_string_buffer(count-len(data));done=W.DWORD()
                if not k.ReadFile(handle,buf,len(buf),C.byref(done),None) or not done.value:raise EOFError()
                data+=buf.raw[:done.value]
            return data
        def serve():
            try:
                if not k.ConnectNamedPipe(handle,None) and C.get_last_error()!=535:raise C.WinError(C.get_last_error())
                for _ in range(2):
                    request=json.loads(read(struct.unpack('<I',read(4))[0]));received.append(request)
                    result={'clientId':'test-client'} if request['method']=='initialize' else {'owner':True}
                    payload=json.dumps({'type':'response','requestId':request['requestId'],
                        'resultType':'success','handledByClientId':'test-owner','result':result}).encode()
                    raw=struct.pack('<I',len(payload))+payload
                    # Fragment both header and body to exercise stream framing.
                    for part in (raw[:2],raw[2:7],raw[7:]):
                        done=W.DWORD();buf=C.create_string_buffer(part)
                        if not k.WriteFile(handle,buf,len(part),C.byref(done),None):raise C.WinError(C.get_last_error())
                # Keep server open while the client finishes handling the reply.
                try:read(1)
                except EOFError:pass
            except Exception as exc:errors.append(type(exc).__name__)
            finally:k.CloseHandle(handle);finished.set()
        thread=threading.Thread(target=serve,daemon=True);thread.start()
        events=[]
        rpc=DesktopRpc(name,events.append)
        try:
            self.assertEqual(rpc.client_id,'test-client')
            result=rpc.call('thread-owner-discovery',{'hostId':'local','conversationId':'fixture'},owner='test-owner')
            self.assertTrue(result['result']['owner'])
            self.assertEqual([r['method'] for r in received],['initialize','thread-owner-discovery'])
        finally:rpc.close()
        self.assertTrue(finished.wait(5));self.assertEqual(errors,[])
