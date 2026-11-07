import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from aw.control.broker import ControlError
from aw.control import codex_receipts as receipts

SESSION = 'fixture-session'
IDENTITY = 'fixture-command'
INTENT = {'version': 1, 'command_id': IDENTITY, 'session_id': SESSION, 'prepared_at': 1.0}


def completed(identity=IDENTITY, **changes):
    payload = {'type': 'item_completed', 'thread_id': SESSION, 'turn_id': 'native-turn',
               'completed_at_ms': 1700000000000,
               'item': {'type': 'UserMessage', 'id': 'engine-item', 'client_id': identity,
                        'content': [{'type': 'text', 'text': 'Identical text'}]}}
    payload.update(changes)
    return {'type': 'event_msg', 'payload': payload}


class ReceiptHistoryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = self.root / 'history.jsonl'
        self.path.write_bytes(b'')
        outer = self
        class History:
            allowed = True
            calls = 0
            def path(self, sid):
                self.calls += 1
                if sid != SESSION or not self.allowed:
                    raise ControlError('native_session_archived')
                return outer.path
        self.history = History()
        self.reader = receipts.ReceiptHistory()

    def append(self, *rows):
        with self.path.open('ab') as stream:
            for row in rows:
                stream.write(json.dumps(row).encode() + b'\n')

    def check(self, intent=INTENT):
        return self.reader.check(self.history, intent)

    def test_exact_completed_native_identity_only_and_original_file_is_untouched(self):
        self.append(completed())
        before = self.path.read_bytes()
        self.assertEqual(self.check(), {'state': 'delivered', 'evidence': 'native_record'})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertNotIn('Identical text', repr(vars(self.reader)))

    def test_same_text_wrong_id_and_non_server_or_wrong_session_records_cannot_confirm(self):
        self.append(completed('manual-message'), completed(type='item_started'),
                    completed(thread_id='another-session'), completed(completed_at_ms=None),
                    completed(item={'type': 'AgentMessage', 'id': 'x', 'client_id': IDENTITY}),
                    {'type': 'response_item', 'payload': completed()['payload']},
                    {'type': 'event_msg', 'payload': {'type': 'user_message', 'message': json.dumps(completed())}})
        self.assertIsNone(self.check())
        self.append(completed())
        self.assertIsNotNone(self.check())

    def test_incremental_reverse_cursor_crosses_block_boundary_without_repeated_full_scan(self):
        self.append(completed())
        self.append(*[{'type': 'response_item', 'payload': {'output': 'x' * 500}} for _ in range(10)])
        with patch.object(receipts, 'READ_BYTES', 1024):
            ends = []; result = None
            for _ in range(10):
                before = self.reader.total; result = self.check()
                self.assertLessEqual(self.reader.total - before, 1024)
                ends.append(self.reader.back_end)
                if result: break
        self.assertIsNotNone(result)
        self.assertGreater(len(ends), 2)
        self.assertTrue(all(a > b for a, b in zip(ends, ends[1:])))

    def test_oversized_tool_record_is_skipped_across_chunks_and_incomplete_eof_waits(self):
        self.append(completed(), {'type': 'response_item', 'payload': {'output': 'x' * 5000}})
        with patch.object(receipts, 'READ_BYTES', 1024), patch.object(receipts, 'RECORD_BYTES', 600):
            results = [self.check() for _ in range(8)]
        self.assertTrue(any(result is not None for result in results))
        self.path.write_bytes(json.dumps(completed()).encode()[:-1])
        self.reader = receipts.ReceiptHistory()
        self.assertIsNone(self.check()); read = self.reader.total
        self.assertIsNone(self.check()); self.assertEqual(self.reader.total, read)
        with self.path.open('ab') as stream: stream.write(b'}\n')
        self.assertIsNotNone(self.check())

    def test_eof_only_reads_growth_and_continuous_output_does_not_starve_older_target(self):
        self.append({'type': 'response_item', 'payload': {'output': 'small'}})
        self.assertIsNone(self.check()); read = self.reader.total
        for _ in range(3): self.assertIsNone(self.check())
        self.assertEqual(self.reader.total, read)
        self.append(completed()); self.assertIsNotNone(self.check())
        self.path.write_bytes(b''); self.reader = receipts.ReceiptHistory()
        self.append(completed(), *[{'type': 'response_item', 'payload': {'output': 'x' * 500}} for _ in range(5)])
        with patch.object(receipts, 'READ_BYTES', 1024):
            found = False
            for _ in range(12):
                self.append({'type': 'response_item', 'payload': {'output': 'more' * 200}})
                if self.check(): found = True; break
        self.assertTrue(found)

    def test_empty_file_first_growth_and_partial_append_preserve_record_boundaries(self):
        self.assertIsNone(self.check()); self.assertEqual(self.reader.total, 0)
        raw = json.dumps(completed()).encode() + b'\n'
        with self.path.open('ab') as stream: stream.write(raw[:80])
        self.assertIsNone(self.check()); read = self.reader.total
        self.assertIsNone(self.check()); self.assertEqual(self.reader.total, read)
        with self.path.open('ab') as stream: stream.write(raw[80:])
        self.assertIsNotNone(self.check())

    def test_rotation_truncation_and_compression_stop_without_reading_unrelated_data(self):
        self.append({'type': 'response_item', 'payload': {'output': 'not a receipt'}})
        self.assertIsNone(self.check())
        replacement = self.root / 'new'; replacement.write_bytes(json.dumps(completed()).encode() + b'\n')
        replacement.replace(self.path)
        self.assertIsNone(self.check()); self.assertTrue(self.reader.stopped)
        self.reader = receipts.ReceiptHistory(); self.path.write_bytes(b'x' * 100)
        self.assertIsNone(self.check()); self.path.write_bytes(b'')
        self.assertIsNone(self.check()); self.assertTrue(self.reader.stopped)
        self.reader = receipts.ReceiptHistory(); self.path = self.root / 'history.jsonl.zst'; self.path.write_bytes(b'not-zstd')
        self.assertIsNone(self.check()); self.assertTrue(self.reader.stopped); self.assertEqual(self.reader.total, 0)

    def test_archive_before_or_during_read_does_not_confirm(self):
        self.append(completed()); self.history.allowed = False
        with self.assertRaises(ControlError): self.check()
        self.history.allowed = True
        original = receipts.completed_identity
        def archive(raw, sid, identity):
            self.history.allowed = False
            return original(raw, sid, identity)
        with patch.object(receipts, 'completed_identity', archive):
            with self.assertRaises(ControlError): self.check()

    def test_cumulative_budget_and_deadline_keep_unknown_without_restarting_scan(self):
        self.append(*[{'type': 'response_item', 'payload': {'output': 'x' * 500}} for _ in range(10)])
        with patch.object(receipts, 'READ_BYTES', 1024), patch.object(receipts, 'TOTAL_BYTES', 2048):
            for _ in range(8): self.assertIsNone(self.check())
            self.assertEqual(self.reader.total, 2048); self.assertTrue(self.reader.stopped)
        self.reader = receipts.ReceiptHistory()
        with patch.object(receipts.time, 'monotonic', side_effect=[0, 3]):
            self.assertIsNone(self.check())
        self.assertGreater(self.reader.total, 0)

    def test_provider_retirement_releases_cursor_without_native_transport_or_replay(self):
        from aw.control.codex_ipc import CodexDesktop
        self.append({'type': 'response_item', 'payload': {'output': 'synthetic'}})
        provider = CodexDesktop('/unused', lambda: [SESSION], background=False, history=self.history)
        self.addCleanup(provider.close)
        provider.restore_reply_receipts([INTENT])
        self.assertIsNone(provider.check_reply_receipt(INTENT))
        key = (SESSION, IDENTITY)
        self.assertGreater(provider.reply_history_reads[key].total, 0)
        self.assertIsNone(provider.rpc)
        provider.retire_reply_receipt(INTENT)
        self.assertEqual(provider.reply_history_reads, {})
        self.assertEqual(provider.reply_watched, {})
        self.assertEqual(set(INTENT), {'version', 'command_id', 'session_id', 'prepared_at'})


if __name__ == '__main__':
    unittest.main()
