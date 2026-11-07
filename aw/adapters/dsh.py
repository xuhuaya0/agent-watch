"""DeepSeek Harness (dsh) adapter.

Data layout (dsh desktop, format v4):
  $DSH_HOME/storages/workspace.json                     workspaces -> path/title/sessionIds, archivedSessionIds
  $DSH_HOME/sessions/<encoded-cwd>/<session-id>/session.v4.jsonl.zstd
      append-only event log written as a sequence of checksummed zstd frames.
      Top-level sessions are named "session-<uuid>"; bare "<uuid>" dirs are subagent children.

Events used (each line: {"type", "seq", "time"(ms), "data"}):
  session                       header (id, cwd, createdAt)
  user/message                  data.content[].text, data.source.kind == "user"
  turn/start / turn/end         data.turn ; turn/end data.reason.kind in completed|error|aborted|interrupted
  tool/call / tool/result       data.callId / data.message.toolCallId
  approval/asked / approval/decided   data.id, data.toolName, data.reason
  session/title                 data.title
  todo/write                    data.todos[{content,status}]
  goal/change                   data.goal.phase
  assistant/message             data.message.content[] (type text)

Files are decoded incrementally: we remember the byte offset of the last complete frame
and only decompress newly appended frames on each scan.
"""

from __future__ import annotations

import glob
import json
import os
import re
import time
from typing import Dict, List, Optional

from .native_sidebar import dsh_pins, unknown_pin

from ..model import (PRIORITY, DONE, ERROR, IDLE, RUNNING, STALLED, WAITING_APPROVAL,
                     WAITING_INPUT, Task)

try:
    import zstandard
except ImportError:  # pragma: no cover
    zstandard = None

ASK_TOOLS = {"ask_user_question", "ask_user", "ask_question"}
# bookkeeping events written by the app itself (e.g. on startup) — not agent activity
QUIET_EVENTS = {"session/end-seed", "session-log-deepseek/delivery-accepted", "session",
                "permission/preset", "sandbox/mode", "approval/policy", "model/selection"}


# ---------------------------------------------------------------------------
# Pure reducer (unit-testable)
# ---------------------------------------------------------------------------

def new_state() -> Dict:
    return {
        "id": None,
        "cwd": "",
        "created_at": None,
        "title": "",
        "last_prompt": "",
        "last_event_at": None,
        "open_turn": None,          # {"turn", "start", "prompt"}
        "last_end": None,           # {"turn", "time", "kind", "message"}
        "pending_calls": {},        # callId -> {"name", "time"}
        "pending_approvals": {},    # id -> {"tool", "reason", "time"}
        "todos": [],
        "goal_active": False,
        "last_assistant": "",
        "last_assistant_phase": "",
        "turns": 0,
        "model": "",
        "subagents": {},            # childId -> label
        "delegation_depth": 0,
        "origin": None,
        "has_turn": False,
    }


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for c in content or []:
        if isinstance(c, dict) and c.get("type") == "text":
            parts.append(c.get("text", ""))
    return "\n".join(parts).strip()


def assistant_phase(message: Dict) -> str:
    """A native tool-bearing assistant step is progress, not its final answer.

    DeepSeek stores text and tool-call blocks together in message.content,
    then emits separate tool/call and tool/result events. Preserve that phase
    when projecting only the readable text; never infer it from the wording.
    """
    content = message.get("content")
    return "commentary" if isinstance(content, list) and any(
        isinstance(part, dict) and part.get("type") == "tool-call"
        for part in content) else ""


def reduce_event(st: Dict, ev: Dict) -> None:
    t = ev.get("type")
    d = ev.get("data") or {}
    tm = ev.get("time")
    ts = tm / 1000.0 if isinstance(tm, (int, float)) else None
    if ts and t not in QUIET_EVENTS:
        st["last_event_at"] = ts

    if t == "session":
        st["id"] = ev.get("id")
        st["cwd"] = ev.get("cwd", "")
        st["delegation_depth"] = ev.get("delegationDepth", 0) or 0
        st["origin"] = ev.get("origin")
        ca = ev.get("createdAt")
        st["created_at"] = ca / 1000.0 if ca else None
        if st["last_event_at"] is None and st["created_at"]:
            st["last_event_at"] = st["created_at"]
    elif t == "model/selection":
        st["model"] = d.get("model") or st["model"]
    elif t == "request/context":
        st["model"] = d.get("model") or st["model"]
    elif t == "subagent/catalog":
        if d.get("childId"):
            st["subagents"][d["childId"]] = d.get("label", "")
    elif t == "user/message":
        if (d.get("source") or {}).get("kind", "user") == "user":
            txt = _text_of(d.get("content"))
            if txt:
                st["last_prompt"] = txt
    elif t == "turn/start":
        # Native sessionListMetadata.blank becomes false on this event, even
        # when the prompt is image-only, empty or has no generated title yet.
        st["has_turn"] = True
        st["last_assistant"] = ""
        st["last_assistant_phase"] = ""
        st["open_turn"] = {"turn": d.get("turn"), "start": ts, "prompt": st["last_prompt"]}
        st["turns"] = max(st["turns"], d.get("turn") or 0)
        st["pending_calls"] = {}
        st["pending_approvals"] = {}
    elif t == "turn/end":
        reason = d.get("reason") or {}
        err = reason.get("error") or {}
        st["last_end"] = {
            "turn": d.get("turn"),
            "time": ts,
            "kind": reason.get("kind", "completed"),
            "message": err.get("message", "") if isinstance(err, dict) else str(err),
            "start": (st["open_turn"] or {}).get("start"),
            "prompt": (st["open_turn"] or {}).get("prompt") or st["last_prompt"],
        }
        st["open_turn"] = None
        st["pending_calls"] = {}
        st["pending_approvals"] = {}
    elif t == "tool/call":
        cid = d.get("callId")
        if cid:
            st["pending_calls"][cid] = {"name": d.get("name", ""), "time": ts}
    elif t == "tool/result":
        msg = d.get("message") or {}
        cid = msg.get("toolCallId") or (msg.get("source") or {}).get("callId") or d.get("callId")
        st["pending_calls"].pop(cid, None)
    elif t == "approval/asked":
        aid = d.get("id")
        if aid:
            st["pending_approvals"][aid] = {
                "tool": d.get("toolName", ""),
                "reason": d.get("reason", ""),
                "time": ts,
            }
    elif t == "approval/decided":
        st["pending_approvals"].pop(d.get("id"), None)
    elif t == "session/title":
        if d.get("title"):
            st["title"] = d["title"]
    elif t == "todo/write":
        st["todos"] = d.get("todos") or []
    elif t == "goal/change":
        g = d.get("goal") or {}
        st["goal_active"] = g.get("phase") == "active" and d.get("operation") != "clear"
    elif t == "assistant/message":
        # Context replacement is bookkeeping, not a new reply. Match the
        # history projection rather than exposing compaction as latest text.
        if ev.get("surfaceOp", "append") != "append":
            return
        message = d.get("message") or {}
        txt = _text_of(message.get("content"))
        if txt:
            st["last_assistant"] = txt
            st["last_assistant_phase"] = assistant_phase(message)


def derive(st: Dict, now: float, cfg: Dict):
    """Return (state, state_since, turn_started_at, prompt, detail)."""
    stall = cfg.get("stall_after_sec", 600)
    stall_tool = cfg.get("stall_after_tool_sec", 1800)
    auto_idle = cfg.get("auto_idle_after_sec", 12 * 3600)
    last = st.get("last_event_at") or now
    todo_now = next((x.get("content", "") for x in st.get("todos", []) if x.get("status") == "in_progress"), "")

    if st["pending_approvals"]:
        a = min(st["pending_approvals"].values(), key=lambda x: x["time"] or 0)
        detail = f"{a['tool']}: {a['reason']}".strip(": ")
        ot = st["open_turn"] or {}
        return WAITING_APPROVAL, a["time"] or last, ot.get("start"), ot.get("prompt", ""), detail

    if st["open_turn"]:
        ot = st["open_turn"]
        asks = [c for c in st["pending_calls"].values() if c["name"] in ASK_TOOLS]
        if asks:
            return WAITING_INPUT, asks[0]["time"] or last, ot["start"], ot["prompt"], "Agent 在等你回答问题"
        calls = list(st["pending_calls"].values())
        limit = stall_tool if calls else stall
        if now - last > limit:
            what = f"无新事件 {int((now - last) // 60)} 分钟"
            if calls:
                what += f"（卡在 {calls[0]['name']}）"
            return STALLED, last, ot["start"], ot["prompt"], what
        detail = todo_now or (f"执行 {calls[0]['name']}" if calls else "思考中")
        return RUNNING, ot["start"] or last, ot["start"], ot["prompt"], detail

    le = st["last_end"]
    if not le:
        return IDLE, last, None, st.get("last_prompt", ""), ""
    kind = le["kind"]
    end_t = le["time"] or last
    if kind == "error":
        state = ERROR
        detail = le.get("message") or "turn 出错"
    elif kind in ("aborted", "interrupted"):
        state = IDLE
        detail = "已中止" if kind == "aborted" else "已中断"
    else:
        # goal mode: the harness starts the next round automatically
        if st.get("goal_active") and now - end_t < 120:
            return RUNNING, end_t, le.get("start"), le.get("prompt", ""), "目标模式自动续跑"
        state = DONE
        detail = (st.get("last_assistant") or "").split("\n")[0][:120]
    if state in (DONE, ERROR) and now - end_t > auto_idle:
        state = IDLE
    return state, end_t, le.get("start"), le.get("prompt", ""), detail


# ---------------------------------------------------------------------------
# Incremental file reader
# ---------------------------------------------------------------------------

class SessionLog:
    def __init__(self, path: str):
        self.path = path
        self.offset = 0
        self.size = -1
        self.mtime = -1.0
        self.text_tail = ""
        self.state = new_state()

    def _reset(self):
        self.offset = 0
        self.text_tail = ""
        self.state = new_state()

    def update(self) -> bool:
        """Consume newly appended frames. Returns True if anything changed."""
        try:
            stt = os.stat(self.path)
        except OSError:
            return False
        if stt.st_size == self.size and stt.st_mtime == self.mtime:
            return False
        if stt.st_size < self.offset:
            self._reset()
        with open(self.path, "rb") as f:
            f.seek(self.offset)
            buf = f.read()
        self.size, self.mtime = stt.st_size, stt.st_mtime
        if self.path.endswith(".zstd"):
            out, consumed = self._decode_frames(buf)
        else:  # plain jsonl (persistence format "none")
            cut = buf.rfind(b"\n") + 1
            out, consumed = buf[:cut], cut
        self.offset += consumed
        if not out:
            return consumed > 0
        text = self.text_tail + out.decode("utf-8", "replace")
        lines = text.split("\n")
        self.text_tail = lines.pop()  # possibly incomplete last line
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                reduce_event(self.state, json.loads(line))
            except (ValueError, TypeError, AttributeError):
                continue
        # a complete trailing line without newline
        if self.text_tail.strip().endswith("}"):
            try:
                reduce_event(self.state, json.loads(self.text_tail))
                self.text_tail = ""
            except ValueError:
                pass
        return True

    @staticmethod
    def _decode_frames(buf: bytes):
        if zstandard is None:
            raise RuntimeError("需要安装 zstandard: pip install zstandard")
        dctx = zstandard.ZstdDecompressor()
        out = []
        consumed = 0
        rest = buf
        while rest:
            obj = dctx.decompressobj()
            try:
                chunk = obj.decompress(rest)
            except zstandard.ZstdError:
                break  # torn / partially written frame: retry next scan
            if not obj.eof:
                break
            out.append(chunk)
            used = len(rest) - len(obj.unused_data)
            consumed += used
            rest = obj.unused_data
        return b"".join(out), consumed


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

_LOGS: Dict[str, SessionLog] = {}


def _load_workspaces(root: str):
    p = os.path.join(root, "storages", "workspace.json")
    by_path, archived, listed = {}, set(), set()
    try:
        with open(p, "r", encoding="utf-8") as f:
            ws = json.load(f)
        for w in (ws.get("tables", {}).get("workspaces") or {}).values():
            if w.get("path"):
                by_path[os.path.normcase(os.path.normpath(w["path"]))] = w.get("title") or os.path.basename(w["path"])
            listed.update(w.get("sessionIds") or [])
        archived = set((ws.get("global") or {}).get("archivedSessionIds") or [])
    except (OSError, ValueError):
        pass
    return by_path, archived, listed


def _session_logs(root: str):
    """Select committed native generations, never backups or temporary files.

    Native persistence selects the highest numeric generation per directory and
    rejects mixed physical encodings or duplicate IDs across project directories.
    Preserve those ambiguities as errors instead of choosing by glob order/mtime.
    """
    candidates = {}
    for path in glob.glob(os.path.join(root, "sessions", "*", "*", "session.v*.jsonl*")):
        match = re.fullmatch(r"session\.v([1-9][0-9]*)\.jsonl(\.zstd)?", os.path.basename(path))
        if not match or not os.path.isfile(path):
            continue
        version = int(match[1])
        if version > 2**53 - 1:
            continue  # The native filename parser requires a safe integer.
        sid = os.path.basename(os.path.dirname(path))
        candidates.setdefault(sid, []).append((version, bool(match[2]), path))
    paths, errors = {}, {}
    for sid, rows in candidates.items():
        directories = {os.path.normcase(os.path.abspath(os.path.dirname(row[2]))) for row in rows}
        if len(directories) != 1:
            errors[sid] = "会话 ID 出现在多个目录，无法确定原生日志"
        elif len({row[1] for row in rows}) != 1:
            errors[sid] = "会话日志混用压缩与非压缩格式，无法确定原生日志"
        else:
            latest = max(rows, key=lambda row: row[0])
            if latest[0] > 4:
                errors[sid] = "尚不支持此 DeepSeek 会话日志版本"
            else:
                paths[sid] = latest[2]
    return paths, errors


def scan(cfg: Dict, now: Optional[float] = None) -> List[Task]:
    now = now or time.time()
    acfg = cfg["adapters"]["dsh"]
    root = os.path.expanduser(acfg.get("root", "~/.dsh"))
    if not os.path.isdir(root):
        return []
    lookback = cfg.get("lookback_days", 7) * 86400
    ws_titles, archived, listed = _load_workspaces(root)
    pins = dsh_pins(root)
    all_sessions = cfg.get("include_all_sessions", True)

    # every session dir: top-level "session-<uuid>" and subagent children "<uuid>"
    paths, path_errors = _session_logs(root)

    def load(sid: str) -> Optional[SessionLog]:
        path = paths.get(sid)
        if not path:
            return None
        log = _LOGS.get(path)
        if log is None:
            log = _LOGS[path] = SessionLog(path)
        log.update()
        return log

    def derive_tree(sid: str, ancestors: frozenset = frozenset()):
        """Surface explicit descendant requests; retain child failures as details."""
        if sid in ancestors or len(ancestors) >= 32:
            return ("unknown", now, None, "", "子代理关系循环或过深"), []
        try:
            log = load(sid)
        except Exception:
            log = None
        if log is None:
            return ("unknown", now, None, "", "子代理日志不可用"), []
        result = derive(log.state, now, cfg)
        state, since, start, prompt, detail = result
        subs = []
        blockers = []
        for cid, label in log.state.get("subagents", {}).items():
            child, descendants = derive_tree(cid, ancestors | {sid})
            subs.append({"id": cid, "label": label, "state": child[0],
                         "detail": child[4], "subagents": descendants})
            if child[0] in (WAITING_APPROVAL, WAITING_INPUT):
                blockers.append((child, label))
        # A child failure or silence does not establish a parent blocker: the
        # parent can retry, replace the child or continue without it. Catalogs
        # retain old children across turns. Preserve those diagnostics in subs;
        # only explicit human requests propagate through an active parent.
        # Finished/cancelled parents retain their native state.
        if state in (RUNNING, STALLED) and blockers:
            child, label = min(blockers, key=lambda x: (PRIORITY[x[0][0]], x[0][1]))
            if PRIORITY[child[0]] <= PRIORITY[state]:
                state, since = child[0], child[1]
                detail = f"子代理「{label}」: {child[4] or child[0]}"
        return (state, since, start, prompt, detail), subs

    tasks: List[Task] = []
    for sid, detail in path_errors.items():
        if sid.startswith("session-") and sid not in archived:
            tasks.append(Task(tool="dsh", session_id=sid, title=sid, state=ERROR,
                              state_since=now, last_event_at=now, detail=detail,
                              machine=cfg["machine"]))
    for sid, path in paths.items():
        if not sid.startswith("session-") or sid in archived:
            continue
        # sessions shown in the dsh sidebar (workspace.json) are always included;
        # anything else only if recently active
        if not all_sessions and sid not in listed:
            try:
                if now - os.path.getmtime(path) > lookback:
                    continue
            except OSError:
                continue
        try:
            log = load(sid)
        except Exception as e:  # keep scanning other sessions
            tasks.append(Task(tool="dsh", session_id=sid, title=sid, state=ERROR,
                              state_since=now, last_event_at=now, detail=f"解析失败: {e}",
                              machine=cfg["machine"]))
            continue
        st = log.state
        # workspace membership and a session-* directory do not establish a
        # conversation: the native sidebar also omits blank setup sessions.
        if not st["has_turn"] or st.get("origin") == "subagent":
            continue
        (state, since, turn_start, prompt, detail), subs = derive_tree(sid)
        cwd = st.get("cwd") or ""
        project = ws_titles.get(os.path.normcase(os.path.normpath(cwd)), os.path.basename(cwd.rstrip("\\/"))) if cwd else ""
        todos = st.get("todos") or []
        open_todos = [x.get("content", "") for x in todos if x.get("status") in ("pending", "in_progress")]

        le = st.get("last_end") or {}
        last_turn_sec = int(le["time"] - le["start"]) if le.get("time") and le.get("start") else None
        if state in (DONE, IDLE) and open_todos and le.get("kind") == "completed":
            detail = f"{detail}（待办还剩 {len(open_todos)} 项）" if detail else f"待办还剩 {len(open_todos)} 项"

        tasks.append(Task(
            tool="dsh",
            session_id=sid,
            title=st.get("title") or (prompt[:40] if prompt else sid),
            state=state,
            state_since=since or now,
            last_event_at=st.get("last_event_at") or now,
            project=project,
            prompt=(prompt or "")[:300],
            turn_started_at=turn_start,
            detail=(detail or "")[:300],
            machine=cfg["machine"],
            extra={
                "native_pin": ({"known": True, "pinned": sid in pins and sid not in archived,
                                "order": pins.get(sid, 0), "source": "dsh_workspace", "writable": False} if pins is not None else unknown_pin()),
                "native_archived": sid in archived,
                "latest_turn": {"user": (prompt or "")[:32768], "assistant": st.get("last_assistant", "")[:65536],
                                "assistant_phase": st.get("last_assistant_phase", ""),
                                "truncated": len(prompt or "") > 32768 or len(st.get("last_assistant", "")) > 65536},
                "cwd": cwd,
                "model": st.get("model", ""),
                "turns": st.get("turns", 0),
                "last_turn_sec": last_turn_sec,
                "todos_done": sum(1 for x in todos if x.get("status") == "completed"),
                "todos_total": len(todos),
                "open_todos": open_todos[:5],
                "subagents": subs,
            },
        ))
    return tasks
