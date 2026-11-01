"""Codex rollout adapter (opt-in; log formats vary by client version).

Expected layout: $CODEX_HOME/sessions/YYYY/MM/DD/rollout-*.jsonl
Each line: {"timestamp": ISO, "type": "session_meta"|"event_msg"|"response_item"|..., "payload": {...}}
event_msg payload.type values used: user_message, task_started, task_complete, turn_aborted,
error, agent_message, exec_approval_request / apply_patch_approval_request.

Also handles response_item user messages and tool calls/results. Current Mac
rollouts were checked for message/start/complete events; approval variants still
need real-client validation. Explicit subagent metadata is folded under the parent;
internal guardian sessions are excluded from user task views.
"""

from __future__ import annotations

import datetime
import copy
from itertools import chain
import glob
import json
import logging
import ntpath
import os
import time
from typing import Dict, Iterator, List, Optional

from ..model import ATTENTION_STATES, PRIORITY, DONE, ERROR, IDLE, RUNNING, STALLED, WAITING_APPROVAL, WAITING_INPUT, Task

from .codex_titles import read_titles, title_text, user_text
from .native_sidebar import codex_sidebar, codex_pin

log = logging.getLogger(__name__)
ASK_TOOLS = {"request_user_input", "ask_user_question", "ask_user", "ask_question"}


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(c.get("text", "") for c in content
                     if isinstance(c, dict) and c.get("type") in ("input_text", "text")
                     and isinstance(c.get("text"), str))


def _ts(s) -> Optional[float]:
    try:
        return datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def analyze(lines: List[Dict], now: float, cfg: Dict) -> Dict:
    st = dict(cwd="", prompt="", first_prompt="", reply="", turn_start=None, state=IDLE, since=None, detail="", last=None, approval=None, has_activity=False)
    calls = {}
    approvals = {}
    for o in lines:
        if not isinstance(o, dict):
            continue
        t = _ts(o.get("timestamp")) or st["last"]
        p = o.get("payload") or {}
        if not isinstance(p, dict):
            continue
        typ = o.get("type")
        # Settings/compaction/token bookkeeping must not refresh task activity.
        meaningful = (typ == "response_item" and p.get("type") in
                      ("message", "function_call", "custom_tool_call", "function_call_output", "custom_tool_call_output")) or (
                      typ == "event_msg" and (p.get("type") in
                      ("user_message", "agent_message", "task_started", "task_complete", "turn_aborted", "error") or
                      str(p.get("type", "")).endswith(("approval_request", "approval_decided"))))
        if typ == "response_item" and p.get("type") == "message" and p.get("role") not in ("user", "assistant"):
            meaningful = False
        if meaningful:
            st["last"] = t or st["last"]
            st["has_activity"] = True
        if typ == "session_meta":
            st["cwd"] = p.get("cwd") if isinstance(p.get("cwd"), str) else ""
        elif typ == "response_item":
            kind = p.get("type")
            if kind == "message" and p.get("role") == "user":
                text = user_text(_text(p.get("content")))
                if text:
                    st["prompt"] = text
                    st["first_prompt"] = st["first_prompt"] or text
            elif kind == "message" and p.get("role") == "assistant" and p.get("phase") == "final_answer":
                st["reply"] = _text(p.get("content"))
            elif kind in ("function_call", "custom_tool_call"):
                cid = p.get("call_id")
                if isinstance(cid, str) and cid:
                    name = p.get("name") if isinstance(p.get("name"), str) else ""
                    calls[cid] = {"name": name.rsplit(".", 1)[-1], "time": t}
            elif kind in ("function_call_output", "custom_tool_call_output"):
                cid = p.get("call_id")
                if isinstance(cid, str) and cid:
                    calls.pop(cid, None)
                    approvals.pop(cid, None)
                    # Older approval events did not carry a call id. A tool
                    # result is the only progress signal available for those.
                    approvals.pop(None, None)
        elif typ == "event_msg":
            et = p.get("type")
            if et == "user_message":
                text = user_text(_text(p.get("message")))
                if text:
                    st["prompt"] = text
                    st["first_prompt"] = st["first_prompt"] or text
            elif et == "task_started":
                st.update(state=RUNNING, turn_start=t, since=t, approval=None, detail="", reply="")
                calls.clear()
                approvals.clear()
            elif et == "task_complete":
                st["reply"] = _text(p.get("last_agent_message")) or st["reply"]
                st.update(state=DONE, since=t, detail=_text(p.get("last_agent_message"))[:120], approval=None)
                calls.clear()
                approvals.clear()
            elif et == "turn_aborted":
                st.update(state=IDLE, since=t, detail="已中止", approval=None)
                calls.clear()
                approvals.clear()
            elif et == "error":
                st.update(state=ERROR, since=t, detail=str(p.get("message", ""))[:200])
            elif isinstance(et, str) and et.endswith("approval_request"):
                approvals[p.get("call_id")] = (t, et)
            elif isinstance(et, str) and et.endswith("approval_decided"):
                approvals.pop(p.get("call_id"), None)
    now = now or time.time()
    if st["state"] == RUNNING:
        asks = [c for c in calls.values() if c["name"] in ASK_TOOLS]
        if approvals:
            approval = min(approvals.values(), key=lambda a: a[0] or 0)
            st.update(state=WAITING_APPROVAL, since=approval[0], detail=approval[1], approval=approval)
        elif asks:
            st.update(state=WAITING_INPUT, since=asks[0]["time"], detail="Agent 在等你回答问题")
        elif st["last"] and now - st["last"] > cfg.get("stall_after_tool_sec" if calls else "stall_after_sec", 1800 if calls else 600):
            st.update(state=STALLED, since=st["last"], detail="无新事件")
    if st["state"] in (DONE, ERROR) and st["since"] and now - st["since"] > cfg.get("auto_idle_after_sec", 43200):
        st["state"] = IDLE
    return st


def session_info(lines: List[Dict]) -> Dict:
    meta = next((r["payload"] for r in lines if r.get("type") == "session_meta"
                 and isinstance(r.get("payload"), dict)), {})
    source = meta.get("source")
    sub = source.get("subagent") if isinstance(source, dict) else None
    spawn = sub.get("thread_spawn") if isinstance(sub, dict) else None
    spawn = spawn if isinstance(spawn, dict) else {}
    guardian = (meta.get("thread_source") == "guardian_review" or sub == "guardian" or
                (isinstance(sub, dict) and sub.get("other") == "guardian"))
    parent = meta.get("parent_thread_id") or spawn.get("parent_thread_id")
    return {
        "id": meta.get("id") or meta.get("session_id"),
        "title": title_text(meta.get("name")) or title_text(meta.get("title")),
        "child": sub is not None or meta.get("thread_source") == "subagent",
        "parent": parent if isinstance(parent, str) else "",
        "guardian": guardian,
    }


def read_events(path: str) -> Iterator[Dict]:
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                # An incomplete tail is retried on the next scan.
                if line.endswith("\n"):
                    log.warning("Skipping malformed Codex event in %s", path)
                continue
            if isinstance(row, dict):
                yield row


_ANALYSIS_CACHE = {}


def scan(cfg: Dict, now: Optional[float] = None) -> List[Task]:
    now = now or time.time()
    root = os.path.expanduser(cfg["adapters"]["codex"].get("root", "~/.codex"))
    lookback = cfg.get("lookback_days", 7) * 86400
    metadata, legacy_pins = codex_sidebar(root)
    all_sessions = cfg.get("include_all_sessions", True)
    records = {}
    old_paths = {}

    def load(path):
        try:
            stat = os.stat(path)
            mtime = stat.st_mtime
            events = read_events(path)
            first = next(events, {})
            info = session_info([first])
            if info["guardian"]:
                events.close()
                return None
            fingerprint = (stat.st_mtime_ns, stat.st_size, tuple(cfg.get(k) for k in
                           ("stall_after_sec", "stall_after_tool_sec", "auto_idle_after_sec")))
            cached = _ANALYSIS_CACHE.get(path)
            if cached and cached[0] == fingerprint and cached[1]['state'] in (DONE, IDLE, ERROR):
                st = copy.deepcopy(cached[1])
                if st['state'] in (DONE, ERROR) and st['since'] and now-st['since'] > cfg.get('auto_idle_after_sec',43200):
                    st['state'] = IDLE
                events.close()
            else:
                st = analyze(chain([first], events), now, cfg)
                _ANALYSIS_CACHE[path] = (fingerprint, copy.deepcopy(st))
        except OSError:
            return None
        # Initialization-only logs are not tasks, including helper placeholders.
        if not st["has_activity"]:
            return None
        sid = os.path.basename(path)[:-6]  # Preserve existing acknowledgement keys.
        thread_id = info["id"] if isinstance(info["id"], str) and info["id"] else sid
        last = st["last"] or mtime
        task = Task(
            tool="codex", session_id=sid,
            title=info["title"] or title_text(st["first_prompt"])[:80] or "Codex 会话", state=st["state"],
            state_since=st["since"] or last, last_event_at=last,
            project=ntpath.basename(st["cwd"].rstrip("\\/")), prompt=st["prompt"][:300],
            turn_started_at=st["turn_start"], detail=st["detail"], machine=cfg["machine"],
            extra={"cwd": st["cwd"], "latest_turn": {
                       "user": st["prompt"][:32768], "assistant": st["reply"][:65536],
                       "truncated": len(st["prompt"]) > 32768 or len(st["reply"]) > 65536},
                   "thread_id": thread_id, "title_source": "session_metadata" if info["title"] else
                   "first_user_message" if st["first_prompt"] else "fallback"},
        )
        records[thread_id] = (task, info)
        return thread_id

    for path in sorted(p for folder in ("sessions",)
                       for p in glob.glob(os.path.join(root, folder, "**", "rollout-*.jsonl"), recursive=True)):
        try:
            if not all_sessions and now - os.path.getmtime(path) > lookback:
                # Read only the metadata of old logs so recent children can
                # still resolve their parent without loading all old history.
                with open(path, encoding="utf-8", errors="replace") as f:
                    row = json.loads(next(f))
                info = session_info([row]) if isinstance(row, dict) else {}
                if isinstance(info.get("id"), str):
                    old_paths[info["id"]] = path
                continue
        except (OSError, ValueError, StopIteration):
            continue
        load(path)

    pending = list(records)
    while pending:
        _, info = records[pending.pop()]
        parent = info["parent"] if info["child"] else ""
        if parent and parent not in records and parent in old_paths:
            loaded = load(old_paths.pop(parent))
            if loaded:
                pending.append(loaded)

    if all_sessions:
        # Native history remains visible even if an older rollout has moved or is unavailable.
        for tid, row in metadata.items():
            if tid in records or not row['visible'] or row.get('native_archived'):
                continue
            stamp = row.get('updated_at_ms') or row.get('updated_at') or 0
            stamp = stamp / 1000 if stamp > 100000000000 else stamp
            cwd = row.get('cwd') or ''
            task = Task(tool='codex', session_id=tid, title=title_text(row.get('name')) or title_text(user_text(row.get('title') or '')) or 'Codex 会话',
                        state=IDLE, state_since=stamp, last_event_at=stamp, machine=cfg['machine'],
                        project=ntpath.basename(cwd.rstrip('\\/')), prompt=user_text(row.get('first_user_message') or '')[:300],
                        extra={'thread_id':tid, 'cwd':cwd, 'metadata_only':True, 'title_source':'native_sidebar'})
            records[tid] = (task, {'child':False, 'parent':''})
    for tid, (task, _) in records.items():
        task.extra['native_pin'] = codex_pin(tid, metadata, legacy_pins)
        task.extra['native_archived'] = metadata.get(tid, {}).get('native_archived', False)

    titles = read_titles(root, records)
    for tid, (task, _) in records.items():
        if tid in titles:
            task.title = titles[tid]["title"]
            task.extra["title_source"] = titles[tid]["source"]

    children = {}
    for tid, (_, info) in records.items():
        if info["child"]:
            children.setdefault(info["parent"], []).append(tid)

    def fold(tid, ancestors=frozenset()):
        task, _ = records[tid]
        subs = []
        active = task.state in (RUNNING, STALLED)
        if tid in ancestors or len(ancestors) >= 32:
            return task, subs
        for cid in children.get(tid, []):
            if cid in ancestors or cid == tid:
                continue
            child, descendants = fold(cid, ancestors | {tid})
            subs.append({"id": cid, "label": child.title, "state": child.state,
                         "detail": child.detail, "activity_at": child.to_dict(now)["activity_at"],
                         "subagents": descendants})
            # Completed/aborted parent turns stay completed/aborted. Only an
            # active parent inherits a child's request for intervention.
            if active and child.state in ATTENTION_STATES:
                if PRIORITY[child.state] <= PRIORITY[task.state]:
                    task.state, task.state_since = child.state, child.state_since
                    task.detail = f"子 Agent「{child.title}」: {child.detail or child.state}"
        subs.sort(key=lambda x: (-(x["activity_at"] or 0), x["id"]))
        task.extra["subagents"] = subs
        return task, subs

    def task_archived(tid):
        return metadata.get(tid, {}).get("native_archived") is True

    # Unknown-parent children never become standalone user conversations.
    # They reattach automatically when their parent log becomes available.
    return [fold(tid)[0] for tid, (_, info) in records.items() if not info["child"] and not task_archived(tid)]
