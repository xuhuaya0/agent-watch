from contextlib import closing
import base64
import json
import os
from pathlib import Path
import sqlite3
import struct
import tempfile
from types import SimpleNamespace
import unittest
import zlib
from unittest.mock import patch

from aw.control.attachments import Attachments, MAX_BYTES, clean_descriptors, image_info, image_references, validate_media
from aw.control.broker import ControlError
from aw.control.history import CodexHistory
from aw.control.history_queue import HistoryReads
from aw.control.worker import Worker
from hub_fixture import isolated_hub
from aw.model import Task


def png(width=1, height=1):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(b'\x00\x26\x88\x80')) + chunk(b'IEND', b''))


class AttachmentTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.source = self.root/'sessions/rollout-example.jsonl'
        self.source.parent.mkdir()
        self.picture = self.root/'example image (1).png'
        self.picture.write_bytes(png())
        self.source.write_text(json.dumps({'type': 'response_item', 'payload': {'type': 'message',
            'role': 'assistant', 'content': f'![Example](<{self.picture}>)'}}) + '\n', encoding='utf-8')
        with closing(sqlite3.connect(self.root/'state_5.sqlite')) as db, db:
            db.execute('CREATE TABLE threads(id,rollout_path,archived)')
            db.execute('INSERT INTO threads VALUES (?,?,0)', ('synthetic-session', str(self.source)))
        self.reader = CodexHistory(self.root, lambda: ['synthetic-session'])

    def descriptor(self):
        return self.reader.read('synthetic-session')['messages'][0]['attachments'][0]

    def test_native_message_registers_a_stable_opaque_id_and_reads_only_its_original_image(self):
        item = self.descriptor()
        self.assertRegex(item['id'], r'^[A-Za-z0-9_-]{32}$')
        self.assertEqual(self.descriptor()['id'], item['id'])
        self.assertEqual((item['mime'], item['width'], item['height']), ('image/png', 1, 1))
        result = self.reader.read_attachment('synthetic-session', item['id'])
        self.assertEqual(validate_media(result['media']), self.picture.read_bytes())
        for identity in (str(self.picture), '../example.png', 'A' * 32):
            with self.assertRaises(ControlError):
                self.reader.read_attachment('synthetic-session', identity)
        with self.assertRaises(ControlError):
            self.reader.attachments.read('different-session', item['id'], self.source)

    def test_archive_scope_expiry_and_replaced_native_log_revoke_registered_images(self):
        item = self.descriptor()
        with closing(sqlite3.connect(self.root/'state_5.sqlite')) as db, db:
            db.execute('UPDATE threads SET archived=1')
        with self.assertRaisesRegex(ControlError, 'archived'):
            self.reader.read_attachment('synthetic-session', item['id'])
        with closing(sqlite3.connect(self.root/'state_5.sqlite')) as db, db:
            db.execute('UPDATE threads SET archived=0')
        entry = self.reader.attachments.entries[item['id']]
        entry['at'] -= 601
        with self.assertRaisesRegex(ControlError, 'attachment_unavailable'):
            self.reader.read_attachment('synthetic-session', item['id'])
        item = self.descriptor()
        replacement = self.source.with_suffix('.replacement')
        replacement.write_text(self.source.read_text(encoding='utf-8'), encoding='utf-8')
        replacement.replace(self.source)
        with self.assertRaisesRegex(ControlError, 'attachment_changed'):
            self.reader.read_attachment('synthetic-session', item['id'])

    def test_file_replacement_and_mutation_cannot_reuse_the_previous_attachment_id(self):
        item = self.descriptor()
        self.picture.write_bytes(png() + b'changed')
        with self.assertRaisesRegex(ControlError, 'attachment_changed'):
            self.reader.read_attachment('synthetic-session', item['id'])
        refreshed = self.descriptor()
        self.assertNotEqual(refreshed['id'], item['id'])

    def test_stat_and_fstat_may_have_different_ctime_for_the_same_unchanged_image(self):
        original = os.fstat
        def different_ctime(fd):
            info = original(fd)
            values = {key: getattr(info, key) for key in
                      ('st_mode', 'st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')}
            values['st_ctime_ns'] += 1000000000
            return SimpleNamespace(**values)
        # Reproduce Windows' path creation-time vs handle change-time distinction.
        with patch('aw.control.attachments.os.fstat', different_ctime):
            item = self.descriptor()
            self.assertEqual(self.descriptor()['id'], item['id'])
            media = self.reader.read_attachment('synthetic-session', item['id'])['media']
            self.assertEqual(validate_media(media), png())

    def test_same_size_mutation_with_restored_mtime_and_unchanged_ctime_is_rejected(self):
        # FAT/older Windows ctime may not change, and changes can be beyond a header probe.
        self.picture.write_bytes(png() + b'A' * 70000)
        before, original = self.picture.stat(), os.fstat
        def unchanged_ctime(fd):
            info = original(fd)
            values = {key: getattr(info, key) for key in
                      ('st_mode', 'st_dev', 'st_ino', 'st_size', 'st_mtime_ns')}
            return SimpleNamespace(**values, st_ctime_ns=before.st_ctime_ns)
        with patch('aw.control.attachments.os.fstat', unchanged_ctime):
            item = self.descriptor()
            with self.picture.open('r+b') as stream:
                stream.seek(-1, os.SEEK_END)
                stream.write(b'B')
            os.utime(self.picture, ns=(before.st_atime_ns, before.st_mtime_ns))
            self.assertEqual(self.picture.stat().st_size, before.st_size)
            self.assertEqual(self.picture.stat().st_mtime_ns, before.st_mtime_ns)
            with self.assertRaisesRegex(ControlError, 'attachment_changed'):
                self.reader.read_attachment('synthetic-session', item['id'])
            refreshed = self.descriptor()
            self.assertNotEqual(refreshed['id'], item['id'])

    def test_file_mutation_during_attachment_read_is_rejected(self):
        item, original, calls = self.descriptor(), os.fstat, 0
        def mutate_after_read(fd):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.picture.write_bytes(png() + b'changed during read')
            return original(fd)
        with patch('aw.control.attachments.os.fstat', mutate_after_read):
            with self.assertRaisesRegex(ControlError, 'attachment_changed'):
                self.reader.attachments.read('synthetic-session', item['id'], self.source)
        self.assertEqual(calls, 2)

    def test_replacement_between_stat_and_header_probe_is_not_registered(self):
        original = os.open
        picture = self.picture.resolve()
        def replace_before_open(path, *args, **kwargs):
            if path == picture:
                with path.open('wb') as stream:
                    stream.write(png() + b'replaced')
            return original(path, *args, **kwargs)
        with patch('aw.control.attachments.os.open', replace_before_open):
            result = self.reader.attachments.describe('synthetic-session', f'![Image](<{picture}>)', self.source)
        self.assertEqual(result[0]['error'], 'attachment_changed')
        self.assertNotIn('id', result[0])

    @unittest.skipUnless(hasattr(os, 'mkfifo') and hasattr(os, 'O_NONBLOCK'), 'Unix FIFO race')
    def test_fifo_replacement_cannot_block_either_header_probe_or_attachment_read(self):
        original, picture = os.open, self.picture.resolve()
        def replace_before_open(path, flags, *args, **kwargs):
            self.assertTrue(flags & os.O_NONBLOCK)
            if Path(path) == picture:
                picture.unlink()
                os.mkfifo(picture)
            return original(path, flags, *args, **kwargs)
        with patch('aw.control.attachments.os.open', replace_before_open):
            result = self.reader.attachments.describe('synthetic-session', f'![Image](<{picture}>)', self.source)
        self.assertEqual(result[0]['error'], 'attachment_changed')
        picture.unlink();picture.write_bytes(png())
        item = self.descriptor()
        with patch('aw.control.attachments.os.open', replace_before_open):
            with self.assertRaisesRegex(ControlError, 'attachment_changed'):
                self.reader.read_attachment('synthetic-session', item['id'])

    def test_only_native_image_references_register_not_code_file_links_or_network_paths(self):
        text = f'[File](<{self.picture}>)\n`![Code](<{self.picture}>)`\n```text\n![Code](<{self.picture}>)\n```'
        self.assertEqual(image_references(text), [])
        self.assertEqual(self.reader.attachments.describe('synthetic-session', '![Network](//host/share/example.png)', self.source), [])
        self.assertEqual(self.reader.attachments.describe('synthetic-session', '![Image](https://example.test/image.png)', self.source), [])
        self.assertEqual(image_references('![Example](/example/image(1).png)'), ['/example/image(1).png'])

    def test_missing_oversized_and_disguised_nonimage_files_are_described_without_bytes(self):
        self.picture.write_bytes(b'<html><script>never execute</script></html>')
        self.assertEqual(self.descriptor()['error'], 'attachment_unsupported')
        self.picture.write_bytes(png(16000, 16000))
        self.assertEqual(self.descriptor()['error'], 'attachment_too_large')
        self.picture.write_bytes(b'x' * (MAX_BYTES + 1))
        self.assertEqual(self.descriptor()['error'], 'attachment_too_large')
        self.picture.unlink()
        self.assertEqual(self.descriptor()['error'], 'attachment_unavailable')

    def test_device_metadata_and_media_are_validated_before_reaching_the_browser(self):
        item = self.descriptor()
        self.assertEqual(clean_descriptors([item]), [item])
        for changed in ({'id': '../path'}, {'width': 17000}, {'mime': 'text/html'}, {'source': 'x' * 2049}):
            with self.assertRaises(ControlError):
                clean_descriptors([{**item, **changed}])
        for media in ({'mime': 'text/html', 'data': base64.b64encode(png()).decode()},
                      {'mime': 'image/png', 'data': base64.b64encode(b'<svg/>').decode()},
                      {'mime': 'image/png', 'data': 'not base64!'}):
            with self.assertRaises(ControlError):
                validate_media(media)

    def test_worker_uses_the_attachment_read_without_dispatching_a_native_action(self):
        identity = self.descriptor()['id']
        reader = self.reader
        class Provider:
            def snapshot(self): return [{'tool': 'codex', 'session_id': 'synthetic-session', 'reply': True, 'history': True, 'requests': []}]
            def read_history(self, *_): raise AssertionError('Attachment must not become a cursor')
            def read_attachment(self, sid, token): return reader.read_attachment(sid, token)
            def execute(self, *_): raise AssertionError('Read must never execute a native action')
        worker = Worker({'device_id': 'synthetic-device', 'hub_url': 'http://127.0.0.1:1', 'token': 'synthetic'},
                        {'codex': Provider()}, self.root/'worker.json')
        worker.exchange = lambda _: {'ok': True, 'receipts': [], 'reads': [{'id': 'read', 'tool': 'codex',
            'session_id': 'synthetic-session', 'attachment_id': identity}]}
        worker.step()
        self.assertEqual(validate_media(worker.read_results[0]['result']['media']), png())
        self.assertEqual(worker.state['receipts'], [])
        self.assertNotIn('media', worker.state_file.read_text(encoding='utf-8'))


class AttachmentTransportTests(unittest.TestCase):
    def test_device_worker_binding_and_revoked_capability_apply_to_media_reads(self):
        queue = HistoryReads()
        token = 'A' * 32
        caps = {('codex', 's'): {'history': True}}
        queue.request('device', 'codex', 's', None, 'worker', token)
        reads, _ = queue.exchange('device', 'worker', caps, [])
        self.assertEqual(reads[0]['attachment_id'], token)
        result = {'id': reads[0]['id'], 'result': {'media': {'mime': 'image/png', 'data': base64.b64encode(png()).decode()}}}
        queue.exchange('other', 'worker', caps, [result])
        self.assertFalse(queue.request('device', 'codex', 's', None, 'worker', token)['ready'])
        queue.exchange('device', 'worker', {}, [result])
        self.assertEqual(queue.request('device', 'codex', 's', None, 'worker', token)['error'], 'native_session_unavailable')

    def test_hub_requires_login_task_identity_and_opaque_id_then_serves_raster_with_restricted_headers(self):
        with isolated_hub() as hub:
            task = Task(tool='codex', session_id='log', title='Synthetic', state='done', state_since=1,
                        last_event_at=1, extra={'thread_id': 'synthetic-session'})
            hub.STORE.ingest('example', [task.to_dict()], machine_id='synthetic-device')
            caps = [{'tool': 'codex', 'session_id': 'synthetic-session', 'reply': True, 'history': True, 'requests': []}]
            hub.CONTROL.exchange('synthetic-device', 'worker', caps, [])
            client = hub.app.test_client()
            route = '/api/attachments/tasks/id:synthetic-device/codex/synthetic-session'
            self.assertEqual(client.get(route + '?id=' + 'A' * 32).status_code, 401)
            client.post('/api/auth/setup', json={'password': 'synthetic-password'})
            self.assertEqual(client.get(route + '?path=/example/image.png').status_code, 400)
            self.assertEqual(client.get(route + '?id=../file').status_code, 400)
            self.assertFalse(client.get(route + '?id=' + 'A' * 32).get_json()['ready'])
            exchange = hub.CONTROL.exchange('synthetic-device', 'worker', caps, [], [])
            hub.CONTROL.exchange('synthetic-device', 'worker', caps, [], [{'id': exchange['reads'][0]['id'],
                'result': {'media': {'mime': 'image/png', 'data': base64.b64encode(png()).decode()}}}])
            response = client.get(route + '?id=' + 'A' * 32)
            self.assertEqual(response.data, png())
            self.assertEqual(response.mimetype, 'image/png')
            self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
            self.assertIn('sandbox', response.headers['Content-Security-Policy'])
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            task.extra['native_archived'] = True
            hub.STORE.ingest('example', [task.to_dict()], machine_id='synthetic-device')
            self.assertEqual(client.get(route + '?id=' + 'A' * 32).status_code, 404)
