"""Unified task model shared by all adapters, the hub and read-only API consumers."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

# --- states -----------------------------------------------------------------
RUNNING = "running"                    # agent is actively working
WAITING_APPROVAL = "waiting_approval"  # agent blocked on a permission prompt
WAITING_INPUT = "waiting_input"        # agent asked the user a question
DONE = "done"                          # turn finished, not yet acknowledged
ERROR = "error"                        # turn ended with an error
STALLED = "stalled"                    # "running" but silent for too long
IDLE = "idle"                          # acknowledged / old / aborted

ATTENTION_STATES = {WAITING_APPROVAL, WAITING_INPUT, ERROR, STALLED}

# Lower = more important (used for sorting dashboards and Get responses).
PRIORITY = {
    WAITING_APPROVAL: 0,
    WAITING_INPUT: 1,
    ERROR: 2,
    STALLED: 3,
    RUNNING: 4,
    DONE: 5,
    IDLE: 9,
}

STATE_LABEL_ZH = {
    RUNNING: "进行中",
    WAITING_APPROVAL: "等待批准",
    WAITING_INPUT: "等待回复",
    DONE: "已完成",
    ERROR: "出错",
    STALLED: "疑似卡住",
    IDLE: "空闲",
}

TOOL_SHORT = {"antigravity": "AG", "dsh": "DSH", "codex": "CX"}


@dataclass
class Task:
    tool: str                 # antigravity | dsh | codex
    session_id: str
    title: str                # session title
    state: str
    state_since: float        # epoch seconds when the current state began
    last_event_at: float      # epoch seconds of the newest event
    project: str = ""
    prompt: str = ""          # the user prompt of the current / last turn
    turn_started_at: Optional[float] = None
    detail: str = ""          # e.g. "pwsh: escalate sandbox ..." / current todo
    machine: str = ""
    url: str = ""             # optional deep-link
    extra: Dict = field(default_factory=dict)
    machine_id: str = ""

    @property
    def key(self) -> str:
        identity = f"id:{self.machine_id}" if self.machine_id else self.machine
        return f"{identity}/{self.tool}/{self.session_id}"

    @property
    def needs_attention(self) -> bool:
        return self.state in ATTENTION_STATES or self.state == DONE

    def to_dict(self, now: Optional[float] = None) -> Dict:
        now = now or time.time()
        d = asdict(self)
        d["key"] = self.key
        d["state_label"] = STATE_LABEL_ZH.get(self.state, self.state)
        d["tool_short"] = TOOL_SHORT.get(self.tool, self.tool.upper())
        d["priority"] = PRIORITY.get(self.state, 9)
        d["needs_attention"] = self.needs_attention
        # "running for" when running, otherwise "since state change"
        ref = self.turn_started_at if self.state in (RUNNING, STALLED) and self.turn_started_at else self.state_since
        d["age_sec"] = max(0, int(now - ref)) if ref else None
        d["silent_sec"] = max(0, int(now - self.last_event_at)) if self.last_event_at else None
        # Keep completion time stable when later bookkeeping events are logged.
        d["activity_at"] = ((self.state_since or self.last_event_at) if self.state in (DONE, IDLE)
                            else (self.last_event_at or self.state_since)) or 0
        d["activity_age_sec"] = max(0, now - d["activity_at"]) if d["activity_at"] else None
        return d

    @staticmethod
    def from_dict(d: Dict) -> "Task":
        fields = {k: d.get(k) for k in Task.__dataclass_fields__ if k in d}
        fields.setdefault("extra", {})
        return Task(**fields)


def fmt_duration(sec: Optional[int]) -> str:
    if sec is None:
        return "-"
    if sec < 60:
        return f"{sec}s"
    m = sec // 60
    if m < 60:
        return f"{m}m"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{h}h{m:02d}m"
    d, h = divmod(h, 24)
    return f"{d}d{h}h"


def task_group(task: Dict) -> str:
    if task["state"] == RUNNING:
        return "running"
    if task["state"] in ATTENTION_STATES and not task.get("acked"):
        return "attention"
    age = task.get("activity_age_sec")
    if age is not None and age < 2 * 3600:
        return "done"
    if age is not None and age < 12 * 3600:
        return "recent"
    return "other"


def sort_tasks(tasks: List[Dict]) -> List[Dict]:
    """Group by attention/activity/recency, newest activity first within each group."""
    order = {"attention": 0, "running": 1, "done": 2, "recent": 3, "other": 4}
    return sorted(tasks, key=lambda t: (order[task_group(t)], -(t.get("activity_at") or 0), t["key"]))
