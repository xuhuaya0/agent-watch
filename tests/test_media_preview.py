"""Native-shaped media fixtures and static HTML isolation; no real session writes."""
import base64
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from aw.control.attachments import Attachments, clean_descriptors, validate_media, image_references
from aw.control.broker import ControlError
from aw.control.html_preview import preview_html, CSP
from aw.control.history import AntigravityHistory
from aw.control.dsh import DeepSeek
from test_attachments import png
from test_native_history import HistoryGateway, DSH_SID, SID
from hub_fixture import isolated_hub
from aw.model import Task


class MediaPreviewTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.log=self.root/'source.jsonl';self.log.write_text('synthetic')
        self.html=self.root/'report.html';self.html.write_text('<style>h1 {color: teal}</style><h1>Report</h1>',encoding='utf-8')

    def test_html_is_registered_only_from_explicit_native_link_and_revoked_on_change(self):
        reader=Attachments();body=f'[Report](<{self.html}>)'
        self.assertEqual(image_references(body),[])
        self.assertEqual(image_references('`'+body+'`',include_html=True),[])
        item=reader.describe('session',body,self.log)[0]
        self.assertEqual(item['mime'],'text/html');self.assertNotIn('width',item)
        self.assertEqual(clean_descriptors([item]),[item])
        self.assertEqual(validate_media(reader.read('session',item['id'],self.log)['media']),self.html.read_bytes())
        with self.assertRaises(ControlError):reader.read('other',item['id'],self.log)
        self.html.write_text('changed')
        with self.assertRaisesRegex(ControlError,'attachment_changed'):reader.read('session',item['id'],self.log)

    def test_local_file_urls_are_supported_without_allowing_remote_file_shares(self):
        reader=Attachments()
        item=reader.describe('session',f'[Report]({self.html.as_uri()})',self.log)[0]
        self.assertEqual(validate_media(reader.read('session',item['id'],self.log)['media']),self.html.read_bytes())
        for url in ('file://remote.example/report.html','file:////remote.example/report.html','file://[/report.html'):
            self.assertEqual(reader.describe('session',f'[Report]({url})',self.log),[])

    def test_html_sanitizer_preserves_layout_and_removes_navigation_execution_and_external_resources(self):
        payload='''<html><head><meta http-equiv="refresh" content="0;url=https://example.test"><base href="https://example.test"><style>div > p {color: red} @import "https://example.test/style";</style></head><body onload="alert(1)"><script>parent.document.body.remove()</script><iframe src="https://example.test"></iframe><svg><script>alert(1)</script></svg><form action="https://example.test"><input name="secret"></form><a href="https://example.test" ping="https://example.test">Link</a><img src="https://example.test/a.png" onerror="alert(1)"><table><tr><td colspan="2">Result</td></tr></table></body></html>'''
        result=preview_html(payload.encode()).decode()
        for forbidden in ('<script','<iframe','<svg','<form','<input','<base','onload=','onerror=',' href=',' ping=','http-equiv="refresh"','src="https:'):
            self.assertNotIn(forbidden,result)
        self.assertIn('<td colspan="2">Result</td>',result)
        self.assertIn('div > p {color: red}',result)
        self.assertIn('Content-Security-Policy',result)
        self.assertIn("default-src 'none'",CSP)
        self.assertIn('sandbox',CSP)
        self.assertNotIn('allow-scripts',CSP)

    def test_html_accepts_only_inline_valid_raster_and_utf8(self):
        data=base64.b64encode(png()).decode()
        result=preview_html(('<img src="data:image/png;base64,'+data+'"><img src="data:image/svg+xml;base64,PHN2Zy8+">').encode()).decode()
        self.assertIn('data:image/png;base64,',result);self.assertNotIn('svg+xml',result)
        for raw in (b'\xff',b'<html>\0'):
            with self.assertRaises(ControlError):validate_media({'mime':'text/html','data':base64.b64encode(raw).decode()})

    def test_hub_html_read_sanitizes_response_with_sandbox_headers(self):
        with isolated_hub() as hub:
            task=Task(tool='dsh',session_id='synthetic-session',title='Synthetic',state='done',state_since=1,last_event_at=1)
            hub.STORE.ingest('example',[task.to_dict()],machine_id='synthetic-device')
            caps=[{'tool':'dsh','session_id':'synthetic-session','reply':True,'history':True,'requests':[]}]
            hub.CONTROL.exchange('synthetic-device','worker',caps,[])
            client=hub.app.test_client();client.post('/api/auth/setup',json={'password':'synthetic-password'})
            route='/api/attachments/tasks/id:synthetic-device/dsh/synthetic-session?id='+'A'*32
            self.assertFalse(client.get(route).get_json()['ready'])
            exchange=hub.CONTROL.exchange('synthetic-device','worker',caps,[],[])
            raw=b'<script>alert(1)</script><h1>Report</h1>'
            hub.CONTROL.exchange('synthetic-device','worker',caps,[],[{'id':exchange['reads'][0]['id'],
                'result':{'media':{'mime':'text/html','data':base64.b64encode(raw).decode()}}}])
            response=client.get(route)
            self.assertEqual(response.mimetype,'text/html');self.assertNotIn(b'<script',response.data)
            self.assertIn(b'<h1>Report</h1>',response.data);self.assertEqual(response.headers['Content-Security-Policy'],CSP)
            self.assertEqual(response.headers['Cache-Control'],'no-store')

    def test_antigravity_html_and_image_share_session_scope(self):
        log=self.root/'brain'/SID/'.system_generated/logs/transcript.jsonl';log.parent.mkdir(parents=True)
        picture=self.root/'sample.png';picture.write_bytes(png())
        log.write_text(json.dumps({'type':'PLANNER_RESPONSE','content':f'![Preview](<{picture}>)\n[Report](<{self.html}>)'})+'\n')
        reader=AntigravityHistory(self.root,lambda:[SID]);items=reader.read(SID)['messages'][0]['attachments']
        self.assertEqual([i['mime'] for i in items],['image/png','text/html'])
        self.assertEqual(validate_media(reader.read_attachment(SID,items[0]['id'])['media']),png())
        reader.scope=lambda:[]
        with self.assertRaises(ControlError):reader.read_attachment(SID,items[1]['id'])

    def deepseek(self):
        log=self.root/'sessions/example'/DSH_SID/'session.v4.jsonl';log.parent.mkdir(parents=True);log.write_text('synthetic')
        provider=DeepSeek({'connection_file':'unused','scope':'all','data_root':str(self.root)},HistoryGateway,background=False)
        provider.refresh();self.addCleanup(provider.close)
        raw=png();digest=hashlib.sha256(raw).hexdigest();target=self.root/'attachments/v1/objects'/digest[:2]/digest
        target.parent.mkdir(parents=True);target.write_bytes(raw)
        ref={'attachmentId':'sha256:'+digest,'mediaType':'image/png','width':1,'height':1,'bytes':len(raw),'name':'Example.png'}
        provider.gateway.events=[{'seq':0,'type':'user/message','data':{'content':[{'type':'image','attachment':ref}]}},
            {'seq':1,'type':'assistant/message','data':{'message':{'content':[{'type':'text','text':f'[Report](<{self.html}>)'}]}}}]
        return provider,target,ref

    def test_deepseek_reads_native_immutable_image_and_html_without_native_writes(self):
        provider,target,ref=self.deepseek();items=provider.read_history(DSH_SID)['messages']
        image=items[0]['attachments'][0];document=items[1]['attachments'][0]
        self.assertIn(image['source'],items[0]['text'])
        self.assertEqual(validate_media(provider.read_attachment(DSH_SID,image['id'])['media']),target.read_bytes())
        self.assertEqual(validate_media(provider.read_attachment(DSH_SID,document['id'])['media']),self.html.read_bytes())
        self.assertEqual({m for m,_ in provider.gateway.calls},{'session/list','session/page','session/projections'})
        provider.gateway.catalog[0]['archived']=True
        with self.assertRaises(ControlError):provider.read_attachment(DSH_SID,image['id'])

    def test_deepseek_bad_digest_metadata_and_replaced_log_revoke_access(self):
        provider,target,ref=self.deepseek();ref['width']=2
        self.assertEqual(provider.read_history(DSH_SID)['messages'][0]['attachments'][0]['error'],'attachment_changed')
        ref['width']=1
        image=provider.read_history(DSH_SID)['messages'][0]['attachments'][0]
        log=target.parents[4]/'sessions/example'/DSH_SID/'session.v4.jsonl'
        replacement=log.with_suffix('.new');replacement.write_text('replacement');replacement.replace(log)
        with self.assertRaises(ControlError):provider.read_attachment(DSH_SID,image['id'])

if __name__=='__main__':unittest.main()
