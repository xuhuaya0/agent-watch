"""Antigravity adapter.

Data layout:
  <root>/brain/<conversation-id>/.system_generated/logs/transcript.jsonl
      one JSON object per step: step_index, source, type, status, created_at (UTC ISO), content,
      tool_calls[{name,args}], error
      types: USER_INPUT, PLANNER_RESPONSE, GENERIC (tool result), SYSTEM_MESSAGE,
             ERROR_MESSAGE, CHECKPOINT
  <root>/annotations/<conversation-id>.pbtxt   title:"..."

Turn semantics:
  * a turn starts at the last USER_INPUT
  * PLANNER_RESPONSE with no tool_calls  -> the agent ended its turn (done)
  * PLANNER_RESPONSE with N tool_calls followed by < N GENERIC results -> calls pending
      - pending ask_question                       -> waiting_input
      - pending call with BypassSandbox=true, old  -> waiting_approval (heuristic)
  * background tasks ("task id: X") not yet reported finished keep the turn "running"
"""

from __future__ import annotations

import datetime
import glob
import json
import os
import re
import time
from collections import Counter
from typing import Dict, List, Optional, Tuple

from .native_sidebar import antigravity_archives, antigravity_pins, antigravity_storage_path, unknown_pin

from ..model import (DONE, ERROR, IDLE, RUNNING, STALLED, WAITING_APPROVAL,
                     WAITING_INPUT, Task)

ASK_TOOLS = {"ask_question"}
_BG_START = re.compile(r"background task with task id:\s*(\S+)")
_BG_DONE = re.compile(r'Task id \\?"([^"\\]+)\\?" finished')
_BG_CANCELLED = re.compile(r'^Task(?: id)?\s+\\?"([^"\\]+)\\?"\s+cancelled\.?\s*$', re.M)
_REQ = re.compile(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", re.S)
_NOTIFICATION = re.compile(
    r'\n<SYSTEM_MESSAGE>\n\[Message\] timestamp=\S+ sender=system '
    # agentapi send-message uses HIGH. LOW also carries native lifecycle
    # notices (for example server restarts), which are not a new reply prompt.
    r'priority=MESSAGE_PRIORITY_HIGH content=(.*)\n</SYSTEM_MESSAGE>\s*$', re.S)
_PATH = re.compile(
    r"(?:[A-Za-z]:[\\/]+Users[\\/]+[^\\/\"'\s]+[\\/]+"
    r"|/(?:Users|home)/[^/\"'\s]+/|/root/)([^\"'\n\r]+)"
)
_SKIP_DIRS = {".gemini", "appdata", "application data", "temp", "tmp"}


def _ts(s: Optional[str]) -> Optional[float]:
    if not s:
        return None
    try:
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _is_true(v) -> bool:
    return v is True or str(v).strip('"').lower() == "true"


def _requests_feedback(call: Dict) -> bool:
    """write_file/edit_file of an artifact with RequestFeedback=true (a plan awaiting review)."""
    if call.get("name") not in ("write_file", "edit_file"):
        return False
    meta = (call.get("args") or {}).get("ArtifactMetadata")
    if isinstance(meta, dict):
        return _is_true(meta.get("RequestFeedback"))
    if isinstance(meta, str):
        try:
            return _is_true(json.loads(meta).get("RequestFeedback"))
        except (ValueError, AttributeError):
            return re.search(r"RequestFeedback['\"]?\s*:\s*['\"]?true", meta, re.I) is not None
    return False


def _user_text(content: str) -> str:
    m = _REQ.search(content or "")
    return (m.group(1) if m else (content or "")).strip()


def read_title(root: str, cid: str) -> str:
    p = os.path.join(root, "annotations", f"{cid}.pbtxt")
    try:
        raw = open(p, "rb").read().decode("utf-8", "replace")
    except OSError:
        return ""
    m = re.search(r'title:\s*"((?:[^"\\]|\\.)*)"', raw)
    if not m:
        return ""
    s = m.group(1)
    if "\\" in s:  # protobuf text escapes (octal bytes)
        try:
            s = s.encode("latin-1", "backslashreplace").decode("unicode_escape").encode("latin-1").decode("utf-8")
        except (UnicodeError, ValueError):
            pass
    return s


def infer_project(steps: List[Dict]) -> str:
    """Most frequently referenced folder under the user's home (e.g. Documents/antigravity/kindle -> kindle)."""
    c1, c2 = Counter(), Counter()
    for s in steps:
        if s.get("type") != "PLANNER_RESPONSE":
            continue
        blob = json.dumps(s.get("tool_calls") or [], ensure_ascii=False).replace("\\\\", "\\")
        for m in _PATH.finditer(blob):
            parts = [p for p in re.split(r"[\\/]+", m.group(1)) if p]
            if not parts or parts[0].lower() in _SKIP_DIRS:
                continue
            # skip the conventional container folder (Documents, Desktop, ...)
            if parts[0].lower() in ("documents", "desktop", "source", "repos", "projects", "code", "dev", "onedrive"):
                parts = parts[1:]
            if not parts:
                continue
            c1[parts[0]] += 1
            if len(parts) >= 3:  # parts[1] is a directory (something follows it)
                c2[(parts[0], parts[1])] += 1
    if not c1:
        return ""
    top1, n1 = c1.most_common(1)[0]
    sub = [(k, n) for k, n in c2.items() if k[0] == top1]
    if sub:
        (k, n) = max(sub, key=lambda x: x[1])
        if n >= 0.5 * n1:
            return k[1]
    return top1


def analyze(steps: List[Dict], now: float, file_mtime: float, cfg: Dict):
    """Return dict(state, since, turn_start, prompt, detail, last_event)."""
    stall = cfg.get("stall_after_sec", 600)
    stall_tool = cfg.get("stall_after_tool_sec", 1800)
    approval_after = cfg.get("approval_guess_after_sec", 20)
    auto_idle = cfg.get("auto_idle_after_sec", 12 * 3600)

    last_event = max([_ts(s.get("created_at")) or 0 for s in steps[-3:]] + [file_mtime or 0]) or now
    u = max((i for i, s in enumerate(steps) if s.get("type") == "USER_INPUT"), default=None)
    if u is None:
        return dict(state=IDLE, since=last_event, turn_start=None, prompt="", detail="", last_event=last_event)

    turn = steps[u:]
    turn_start = _ts(steps[u].get("created_at")) or last_event
    prompt = _user_text(steps[u].get("content", ""))
    notifications = [(i, _NOTIFICATION.search(s.get("content") or ""))
                     for i, s in enumerate(turn) if s.get("type") == "SYSTEM_MESSAGE"]
    notifications = [(i, m.group(1)) for i, m in notifications if m]
    input_index, notification = notifications[-1] if notifications else (0, "")
    if notification:
        turn_start = _ts(turn[input_index].get("created_at")) or last_event

    # background tasks started in this turn and not yet reported finished
    pending_bg = set()
    for s in turn:
        c = s.get("content") or ""
        if s.get("type") == "GENERIC":
            for m in _BG_START.finditer(c):
                pending_bg.add(m.group(1))
            # manage_task reports cancellation in its tool result, not in a
            # later SYSTEM_MESSAGE. Only read the result header, not logs that
            # might quote another task's cancellation message.
            header = re.split(r'(?m)^(?:Log output|Command output):', c, maxsplit=1)[0]
            if not s.get("error") and s.get("status", "DONE") == "DONE":
                for m in _BG_CANCELLED.finditer(header):
                    pending_bg.discard(m.group(1))
        if s.get("type") == "SYSTEM_MESSAGE":
            for m in _BG_DONE.finditer(c):
                pending_bg.discard(m.group(1))

    last = turn[-1]
    lt = last.get("type")
    silent = now - last_event

    # locate the last planner response and how many of its tool calls have results
    p = max((i for i, s in enumerate(turn) if s.get("type") == "PLANNER_RESPONSE"), default=None)
    pending_calls: List[Dict] = []
    if p is not None:
        calls = turn[p].get("tool_calls") or []
        results = sum(1 for s in turn[p + 1:] if s.get("type") == "GENERIC")
        later_input = any(s.get("type") in ("SYSTEM_MESSAGE", "USER_INPUT") for s in turn[p + 1:])
        if not later_input and results < len(calls):
            pending_calls = calls[results:]

    def out(state, since, detail=""):
        return dict(state=state, since=since, turn_start=turn_start, prompt=prompt,
                    detail=detail, last_event=last_event, notification=notification,
                    reply=next((s.get("content") or "" for s in reversed(turn[input_index:])
                                if s.get("type") == "PLANNER_RESPONSE" and not s.get("tool_calls")), ""))

    if notification and input_index == len(turn) - 1:
        return out(STALLED if silent >= stall else RUNNING, turn_start, "等待外部消息的回复")

    # --- agent ended its turn -------------------------------------------------
    if lt == "PLANNER_RESPONSE" and not (last.get("tool_calls") or []) and last.get("status", "DONE") == "DONE":
        # the final response is the last line, so the file mtime ~= when the turn ended
        end_t = file_mtime or _ts(last.get("created_at")) or last_event
        if pending_bg:
            if silent >= stall_tool:
                return out(STALLED, last_event, f"后台任务未确认完成 ({len(pending_bg)})")
            return out(RUNNING, turn_start, f"等待后台任务 ({len(pending_bg)})")
        asked_review = any(
            _requests_feedback(c)
            for s in turn if s.get("type") == "PLANNER_RESPONSE" for c in (s.get("tool_calls") or [])
        )
        text = (last.get("content") or "").strip().split("\n")[0][:120]
        if now - end_t > auto_idle:
            return out(IDLE, end_t, text)
        if asked_review:
            return out(WAITING_INPUT, end_t, "有方案/文档等你审阅")
        return out(DONE, end_t, text)

    # --- tool calls pending -----------------------------------------------------
    if pending_calls:
        names = [c.get("name", "") for c in pending_calls]
        call_t = _ts(turn[p].get("created_at")) or last_event
        if any(n in ASK_TOOLS for n in names):
            return out(WAITING_INPUT, call_t, "Agent 在等你回答问题")
        bypass = [c for c in pending_calls if _is_true((c.get("args") or {}).get("BypassSandbox"))]
        if bypass and silent > approval_after:
            a = bypass[0].get("args") or {}
            what = str(a.get("toolSummary") or a.get("CommandLine") or "").strip('"')[:100]
            return out(WAITING_APPROVAL, call_t, f"可能等待批准: {what}")
        if silent > stall_tool:
            return out(STALLED, last_event, f"卡在 {names[0]} {int(silent // 60)} 分钟")
        return out(RUNNING, turn_start, f"执行 {names[0]}")

    # --- error ------------------------------------------------------------------
    if lt == "ERROR_MESSAGE" and silent > 60:
        return out(ERROR, last_event, (last.get("error") or "错误")[:200])

    # --- model is generating / processing --------------------------------------
    if silent > stall:
        # A timer/background notification arrived after the agent had already finished its turn
        # and nothing followed: the work itself is done, it is not a stuck task.
        if lt == "SYSTEM_MESSAGE":
            fin = [s for s in turn if s.get("type") == "PLANNER_RESPONSE" and not (s.get("tool_calls") or [])]
            if fin:
                end_t = _ts(fin[-1].get("created_at")) or last_event
                text = "后台/定时通知未处理 · " + (fin[-1].get("content") or "").strip().split("\n")[0][:80]
                return out(IDLE if now - end_t > auto_idle else DONE, end_t, text)
        return out(STALLED, last_event, f"无新事件 {int(silent // 60)} 分钟")
    return out(RUNNING, turn_start, "思考中")


def _read_jsonl(path: str) -> List[Dict]:
    steps = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                steps.append(json.loads(line))
            except ValueError:
                continue
    return steps


_CACHE: Dict[str, Tuple[float, int, List[Dict], str]] = {}


def scan(cfg: Dict, now: Optional[float] = None) -> List[Task]:
    now = now or time.time()
    acfg = cfg["adapters"]["antigravity"]
    root = os.path.expanduser(acfg.get("root", "~/.gemini/antigravity"))
    pins = antigravity_pins(acfg.get("sidebar_path") or antigravity_storage_path())
    archives = antigravity_archives(root)
    lookback = cfg.get("lookback_days", 7) * 86400
    tasks: List[Task] = []
    for path in glob.glob(os.path.join(root, "brain", "*", ".system_generated", "logs", "transcript.jsonl")):
        cid = path.split(os.sep)[-4]
        if archives.get(cid) is True:
            continue
        try:
            st = os.stat(path)
        except OSError:
            continue
        if not cfg.get("include_all_sessions", True) and now - st.st_mtime > lookback:
            continue
        cached = _CACHE.get(path)
        if cached and cached[0] == st.st_mtime and cached[1] == st.st_size:
            steps, project = cached[2], cached[3]
        else:
            try:
                steps = _read_jsonl(path)
            except OSError:
                continue
            project = infer_project(steps)
            _CACHE[path] = (st.st_mtime, st.st_size, steps, project)
        a = analyze(steps, now, st.st_mtime, cfg)
        title = read_title(root, cid) or (a["prompt"][:40] if a["prompt"] else cid[:8])
        tasks.append(Task(
            tool="antigravity",
            session_id=cid,
            title=title,
            state=a["state"],
            state_since=a["since"] or now,
            last_event_at=a["last_event"] or now,
            project=project,
            prompt=a["prompt"][:300],
            turn_started_at=a["turn_start"],
            detail=(a["detail"] or "")[:300],
            machine=cfg["machine"],
            extra={"native_archived": archives.get(cid), "native_pin": unknown_pin() if pins is None else {
                "known": True, "pinned": cid in pins, "writable": False,
                "source": "antigravity_app_storage", "order": pins.get(cid),
            }, "latest_turn": {
                "user": a["prompt"][:32768],
                "assistant": a.get("reply", "")[:65536],
                "notification": a.get("notification", "")[:32768],
                "truncated": len(a["prompt"]) > 32768 or len(a.get("reply", "")) > 65536 or len(a.get("notification", "")) > 32768,
            }},
        ))
    return tasks
