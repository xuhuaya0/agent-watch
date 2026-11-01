"""Run as an explicitly configured native sidecar; never start an Agent engine."""
import argparse
import json
import time
import os
from pathlib import Path
from urllib.parse import urlsplit
from aw.control.antigravity import Antigravity
from aw.control.history import AntigravityHistory, CodexHistory
from aw.control.codex import Codex
from aw.control.codex_ipc import CodexDesktop
from aw.control.codex_queue import native_queue_sender
from aw.control.dsh import DeepSeek
from aw.control.worker import Worker
from aw.control.lock import WorkerLock
from aw.control.sessions import SessionScope, CodexScope
from aw.control.antigravity_rpc import NativeRpc
from aw.control.antigravity_interactions import NativeInteractions


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, help='Private device control config')
    parser.add_argument('--once', action='store_true')
    args=parser.parse_args()
    path=Path(args.config).resolve()
    cfg=json.loads(path.read_text(encoding='utf-8'))
    url=urlsplit(cfg['hub_url'])
    if url.scheme not in ('http','https') or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('Invalid Hub URL')
    providers={}
    if 'antigravity' in cfg:
        ag=cfg['antigravity']
        # Selected sessions remain the default; all-session discovery is explicit.
        # A one-shot check must wait for its snapshot instead of exiting before
        # the background discovery thread has returned its first result.
        interactions=NativeInteractions(NativeRpc(os.environ), approvals=ag.get('native_approvals') is True) if ag.get('native_questions') is True else None
        scope = SessionScope(path)
        history = AntigravityHistory(ag.get('data_root', '~/.gemini/antigravity'), scope)
        providers['antigravity']=Antigravity(ag['executable'], scope, background=not args.once, interactions=interactions, history=history)
    if 'codex' in cfg:
        cx=cfg['codex']
        if cx.get('transport') == 'desktop-ipc':
            scope=CodexScope(path)
            history=CodexHistory(cx.get('data_root', '~/.codex'), scope)
            providers['codex']=CodexDesktop(cx['socket_path'],scope,background=not args.once,history=history,queue_sender=native_queue_sender(history.root, cx.get('executable')))
            if args.once:providers['codex'].refresh()
        else:
            providers['codex']=Codex(cx['executable'],cx['socket_path'],cx['session_ids'])
    if 'dsh' in cfg:
        providers['dsh']=DeepSeek(cfg['dsh'],background=not args.once)
        if args.once:providers['dsh'].refresh()
    lock=WorkerLock(path.with_name('control-worker.lock'))
    try:
        while True:
            # Antigravity launches a sidecar per native window. Do not let two
            # copies overwrite the same receipts or advertise different live
            # sessions under one worker ID. Only a context with an allowlisted
            # live session competes for ownership; others wait without writes.
            available=False
            for provider in providers.values():
                try:available = bool(provider.snapshot()) or available
                except Exception:pass
            if available and lock.acquire():
                try:
                    worker=Worker(cfg,providers,path.with_name('control-worker-state.json'))
                    while True:
                        acted=False
                        try:
                            acted=worker.step()
                            if not worker.has_sessions:break
                        except Exception:print('Control connection unavailable; no action retried',flush=True)
                        if args.once:return
                        if not acted:time.sleep(1)
                finally:lock.release()
            if args.once:return
            time.sleep(5)
    finally:
        lock.close()
        for provider in providers.values():
            if hasattr(provider,'close'):provider.close()


if __name__=='__main__':main()
