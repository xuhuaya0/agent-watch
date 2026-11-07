"""Durable, bounded read-only confirmation of uncertain reply delivery.

Providers own the native evidence. This module never invokes execute, interprets
absence as failure, or queues an action for retry. Pending intents contain no
message body and survive the Hub acknowledging an initial unknown receipt.
"""
from __future__ import annotations

import json
import time


TTL = 24 * 60 * 60
INTERVAL = 5
MAX_PENDING = 128
MAX_INTENT_BYTES = 16 * 1024
DETAILS = {
    'native_record': '已从原生会话记录确认接收；不代表 Agent 已完成执行',
    'late_ack': '原应用的迟到回执已确认接收；不代表 Agent 已完成执行',
}


class ReplyReceipts:
    def __init__(self, state, providers, diagnostics, clock=time.time):
        self.state, self.providers, self.diagnostics, self.clock = state, providers, diagnostics, clock
        self.pending = state.setdefault('reply_receipts', {})

    def restore(self):
        for tool, provider in self.providers.items():
            if not hasattr(provider, 'restore_reply_receipts'):
                continue
            intents = [entry['intent'] for entry in self.pending.values() if entry['tool'] == tool]
            try:
                provider.restore_reply_receipts(intents)
            except Exception as error:
                self.diagnostics.record('exception', 'recovery', {'tool': tool}, error)

    def retire(self, identifier):
        entry = self.pending.pop(identifier, None)
        if entry is None:
            return
        provider = self.providers.get(entry['tool'])
        try:
            if hasattr(provider, 'retire_reply_receipt'):
                provider.retire_reply_receipt(entry['intent'])
        except Exception as error:
            self.diagnostics.record('exception', 'receipt_retire', {'id': identifier, **entry}, error)

    def expire(self):
        now = self.clock()
        changed = False
        for identifier, entry in list(self.pending.items()):
            if now >= entry['expires_at'] or now < entry['created'] - 60:
                self.diagnostics.record('expired', 'receipt_check', {'id': identifier, **entry})
                self.retire(identifier)
                changed = True
        return changed

    def prepare(self, command):
        if command['action']['kind'] != 'reply':
            return
        self.expire()
        provider = self.providers.get(command['tool'])
        if not hasattr(provider, 'prepare_reply_receipt'):
            return
        if len(self.pending) >= MAX_PENDING:
            self.diagnostics.record('capacity', 'receipt_prepare', command)
            return
        try:
            intent = provider.prepare_reply_receipt(command)
            if intent is None:
                return
            wire = json.dumps(intent, ensure_ascii=False, allow_nan=False).encode('utf-8')
            if not isinstance(intent, dict) or len(wire) > MAX_INTENT_BYTES:
                raise ValueError('invalid confirmation intent')
            now = self.clock()
            self.pending[command['id']] = {
                'tool': command['tool'], 'session_id': command['session_id'],
                'intent': json.loads(wire), 'created': now,
                'expires_at': now + TTL, 'next_check': now,
            }
        except Exception as error:
            self.diagnostics.record('exception', 'receipt_prepare', command, error)

    def reconcile(self):
        changed = self.expire()
        now = self.clock()
        next_check = self.state.get('reply_receipt_check_at', 0)
        if now < next_check <= now + INTERVAL:
            return changed
        ready = sorted((entry['next_check'], identifier) for identifier, entry in self.pending.items()
                       if entry['next_check'] <= now)
        if not ready:
            return changed
        _, identifier = ready[0]
        entry = self.pending[identifier]
        self.state['reply_receipt_check_at'] = now + INTERVAL
        entry['next_check'] = now + INTERVAL
        receipt = next((r for r in self.state['receipts'] if r['id'] == identifier), None)
        if receipt and receipt['state'] not in ('executing', 'unknown'):
            self.retire(identifier)
            return True
        provider = self.providers.get(entry['tool'])
        if not hasattr(provider, 'check_reply_receipt'):
            return True
        try:
            result = provider.check_reply_receipt(entry['intent'])
            if (not isinstance(result, dict) or result.get('state') != 'delivered'
                    or result.get('evidence') not in DETAILS):
                return True
            if receipt is None:
                receipt = {'id': identifier}
                self.state['receipts'].append(receipt)
            receipt.update(state='delivered', detail=DETAILS[result['evidence']],
                           confirmation=result['evidence'])
            self.diagnostics.record('confirmed', 'receipt_check', {'id': identifier, **entry})
            self.retire(identifier)
        except Exception as error:
            self.diagnostics.record('exception', 'receipt_check', {'id': identifier, **entry}, error)
        return True
