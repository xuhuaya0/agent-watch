import io
import json
import unittest
from aw.control.antigravity_rpc import NativeRpc
from aw.control.broker import ControlError


class Opener:
    def __init__(self, body=b'{}', error=None):self.body,self.error,self.calls=body,error,[]
    def open(self,request,timeout):
        self.calls.append(request)
        if self.error:raise self.error
        return io.BytesIO(self.body)


class TransportTests(unittest.TestCase):
    def config(self, address='localhost:1234'):
        return {'ANTIGRAVITY_LS_ADDRESS':address,'ANTIGRAVITY_CSRF_TOKEN':'test-context-only'}

    def test_only_managed_loopback_destinations_and_known_methods(self):
        for address in ['example.com:1234','localhost:1234@evil.test:80','localhost:1234/path','localhost:1234?x=1',
                        '127.0.0.1:1234#x','http://localhost:1234','localhost:abc','localhost:99999',
                        '[::1','local\nhost:1234',None,1234]:
            with self.assertRaises(ControlError):NativeRpc(self.config(address))
        with self.assertRaises(ControlError):NativeRpc({'ANTIGRAVITY_LS_ADDRESS':'localhost:1'})
        for token in [None,1234,'token\r\nInjected: value']:
            config=self.config();config['ANTIGRAVITY_CSRF_TOKEN']=token
            with self.assertRaises(ControlError):NativeRpc(config)
        opener=Opener();rpc=NativeRpc(self.config(),opener)
        rpc.call('GetConversationMetadata',{'conversationId':'exact-id'})
        req=opener.calls[0]
        self.assertEqual(req.full_url,'http://127.0.0.1:1234/exa.language_server_pb.LanguageServerService/GetConversationMetadata')
        self.assertEqual(json.loads(req.data),{'conversationId':'exact-id'})
        self.assertNotIn('test-context-only',req.full_url)
        with self.assertRaises(ControlError):rpc.call('SetPermissions',{})
        self.assertEqual(len(opener.calls),1)

    def test_uncertain_writes_are_not_retried_or_claimed_successful(self):
        for opener in [Opener(error=TimeoutError()),Opener(b'<html>'),Opener(b'{"code":"internal"}')]:
            rpc=NativeRpc(self.config(),opener)
            self.assertIsNone(rpc.call('HandleCascadeUserInteraction',{'conversationId':'test'}))
            self.assertEqual(len(opener.calls),1)
            with self.assertRaises(ControlError):rpc.call('GetConversationMetadata',{'conversationId':'test'})
