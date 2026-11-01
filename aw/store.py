"""Task store: local scans + persistent remote ingests, multi-IP tracking, offline retention."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request
from dataclasses import replace
from typing import Dict, List, Optional, Tuple

from .model import (ATTENTION_STATES, DONE, IDLE, RUNNING, STALLED, Task,
                    fmt_duration, sort_tasks, task_group)

log = logging.getLogger("agent-watch")


class Store:
    def __init__(self, cfg: Dict, data_dir: str):
        self.cfg = cfg
        self.lock = threading.RLock()
        self.local: List[Task] = []
        self.errors: Dict[str, str] = {}    # adapter -> last error
        self.scanned_at: float = 0
        os.makedirs(data_dir, exist_ok=True)
        self.ack_file = os.path.join(data_dir, "ack.json")
        self.remote_file = os.path.join(data_dir, "remote_state.json")
        self.ui_file = os.path.join(data_dir, "workspace.json")
        raw_ui = self._load_json(self.ui_file, {})
        # Only read receipts belong to the panel. Remove obsolete local archive
        # and pin flags, including for conversations no longer in the inventory.
        self.ui_state = {
            key: {"read_at": state["read_at"]}
            for key, state in raw_ui.items()
            if isinstance(state, dict) and "read_at" in state
        } if isinstance(raw_ui, dict) else {}
        if self.ui_state != raw_ui:
            self._save_json(self.ui_file, self.ui_state)
        self.acks: Dict[str, float] = self._load_json(self.ack_file, {})
        # id:<uuid> (legacy: machine name) -> last snapshot + display name
        self.remote: Dict[str, Dict] = self._load_remote()
        self._notified: Optional[set] = None
        self.version = 0

    # --- persistence ----------------------------------------------------------
    @staticmethod
    def _load_json(path: str, default):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return default

    @staticmethod
    def _save_json(path: str, data) -> None:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)

    def _load_remote(self) -> Dict[str, Dict]:
        raw = self._load_json(self.remote_file, {})
        out: Dict[str, Dict] = {}
        for m, rec in raw.items():
            try:
                tasks = [Task.from_dict(d) for d in rec.get("tasks", [])]
                out[m] = {
                    "machine": rec.get("machine", m),
                    "machine_id": rec.get("machine_id", ""),
                    "at": float(rec.get("at", 0)),
                    "ips": list(rec.get("ips") or []),
                    "tasks": tasks,
                }
            except Exception:
                continue
        return out

    def _save_remote(self) -> None:
        serial = {
            m: {
                "machine": r.get("machine", m),
                "machine_id": r.get("machine_id", ""),
                "at": r["at"],
                "ips": r.get("ips", []),
                "tasks": [t.to_dict() for t in r["tasks"]],
            }
            for m, r in self.remote.items()
        }
        try:
            self._save_json(self.remote_file, serial)
        except OSError as e:
            log.warning("save remote_state failed: %s", e)

    # --- acks -----------------------------------------------------------------
    def ack(self, key: str) -> bool:
        with self.lock:
            t = next((t for t in self.all_tasks() if t.key == key), None)
            if not t:
                return False
            self.acks[key] = t.state_since
            live = {x.key for x in self.all_tasks()}
            self.acks = {k: v for k, v in self.acks.items() if k in live}
            self._save_json(self.ack_file, self.acks)
            self.version += 1
            return True

    def is_acked(self, t: Task) -> bool:
        a = self.acks.get(t.key)
        return a is not None and a >= t.state_since - 1 and t.state not in (RUNNING,)

    def update_ui(self, key: str, changes: Dict) -> Optional[Dict]:
        with self.lock:
            if "pinned" in changes:
                raise ValueError("native pin state is read-only")
            if set(changes) - {"read_at"}:
                raise ValueError("unsupported panel preference")
            task = next((t for t in self.view() if t["ui_key"] == key), None)
            if task is None:
                return None
            state = dict(self.ui_state.get(key, {}))
            state.pop("pinned", None)
            state.pop("pinned_at", None)
            if "read_at" in changes:
                state["read_at"] = min(changes["read_at"], task["activity_at"])
            self.ui_state[key] = state
            self._save_json(self.ui_file, self.ui_state)
            return state

    # --- updates --------------------------------------------------------------
    def set_local(self, tasks: List[Task], errors: Dict[str, str]):
        with self.lock:
            for task in tasks:
                task.machine_id = self.cfg.get("machine_id", "")
            self.local = tasks
            self.errors = errors
            self.scanned_at = time.time()
            self.version += 1
        self._check_notify()

    def ingest(self, machine: str, tasks: List[Dict], remote_ip: str = "", extra_ips: Optional[List[str]] = None,
               machine_id: str = ""):
        now = time.time()
        key = f"id:{machine_id}" if machine_id else machine
        # Parse the whole snapshot before touching stored state. Identity comes
        # from the envelope, never from individual task fields.
        parsed = [Task.from_dict({**d, "machine": machine, "machine_id": machine_id}) for d in tasks]
        with self.lock:
            prev = self.remote.get(key) or {}
            if prev and prev.get("machine_id", "") != machine_id:
                raise ValueError("device key conflicts with a legacy machine name")
            ips = list(prev.get("ips") or [])
            for ip in ([remote_ip] if remote_ip else []) + (extra_ips or []):
                if isinstance(ip, str) and ip and ip not in ("127.0.0.1", "::1") and ip not in ips:
                    ips.append(ip)
            self.remote[key] = {
                "machine": machine,
                "machine_id": machine_id,
                "at": now,
                "ips": ips[-8:],
                "tasks": parsed,
            }
            self._save_remote()
            self.version += 1
        self._check_notify()

    def remove_machine(self, machine: str) -> bool:
        with self.lock:
            if machine not in self.remote:
                return False
            del self.remote[machine]
            self._save_remote()
            self.version += 1
            return True

    def _iter_tasks_with_machine_meta(self, now: Optional[float] = None) -> List[Tuple[Task, bool, float, List[str]]]:
        """Yield (task, machine_online, machine_last_seen, machine_ips). Offline tasks are ALWAYS kept."""
        now = now or time.time()
        offline_after = self.cfg.get("offline_after_sec") or self.cfg.get("remote_ttl_sec") or 120
        with self.lock:
            out: List[Tuple[Task, bool, float, List[str]]] = []
            local_at = self.scanned_at or now
            for t in self.local:
                if t.extra.get("native_archived") is True:
                    continue
                out.append((t, True, local_at, ["local"]))
            for m, r in self.remote.items():
                if m == self._local_key() and (self.local or self.scanned_at):
                    # avoid duplicating local tasks if a node pushes to itself
                    continue
                at = r.get("at", 0)
                online = (now - at) <= offline_after
                ips = r.get("ips", [])
                for t in r["tasks"]:
                    if t.extra.get("native_archived") is True:
                        continue
                    out.append((t, online, at, ips))
            return out

    def all_tasks(self) -> List[Task]:
        return [t for t, _, _, _ in self._iter_tasks_with_machine_meta()]

    def _local_key(self) -> str:
        mid = self.cfg.get("machine_id")
        return f"id:{mid}" if mid else self.cfg.get("machine", "local")

    def machines_status(self) -> Dict[str, Dict]:
        now = time.time()
        offline_after = self.cfg.get("offline_after_sec") or self.cfg.get("remote_ttl_sec") or 120
        with self.lock:
            res = {}
            if self.local or self.scanned_at:
                res[self._local_key()] = {
                    "machine": self.cfg.get("machine", "local"),
                    "machine_id": self.cfg.get("machine_id", ""),
                    "local": True,
                    "online": True,
                    "last_seen": self.scanned_at or now,
                    "age": "在线",
                    "ips": ["127.0.0.1"],
                    "tasks": len(self.local),
                }
            for m, r in self.remote.items():
                if m == self._local_key() and (self.local or self.scanned_at):
                    continue
                at = r.get("at", 0)
                online = (now - at) <= offline_after
                res[m] = {
                    "machine": r.get("machine", m),
                    "machine_id": r.get("machine_id", ""),
                    "local": False,
                    "online": online,
                    "last_seen": at,
                    "age": "在线" if online else f"离线 {fmt_duration(int(now - at))}",
                    "ips": r.get("ips", []),
                    "tasks": len(r["tasks"]),
                }
            return res

    # --- views ----------------------------------------------------------------
    def view(self, include_idle: bool = True, native_pending=None) -> List[Dict]:
        now = time.time()
        res = []
        for t, online, seen_at, ips in self._iter_tasks_with_machine_meta(now):
            thread = t.extra.get("thread_id") if t.tool == "codex" else None
            native_state = (native_pending or {}).get((t.machine_id,t.tool,thread or t.session_id))
            if native_state in ("waiting_approval", "waiting_input"):
                # The control snapshot has no reliable request-start timestamp.
                # Do not overwrite collector history or invent a wait duration.
                t = replace(t,state=native_state,state_since=0,detail="原应用正在等待批准" if native_state=="waiting_approval" else "原应用正在等待回答")
            d = t.to_dict(now)
            d["acked"] = False if native_state else self.is_acked(t)
            identity = f"id:{t.machine_id}" if t.machine_id else t.machine
            thread = t.extra.get("thread_id") if t.tool == "codex" else None
            d["ui_key"] = f"{identity}/{t.tool}/{thread or t.session_id}"
            d["ui"] = dict(self.ui_state.get(d["ui_key"], {}))
            # Discard obsolete panel-only pins; the Harness remains the source of truth.
            pin = t.extra.get("native_pin") or {}
            d["ui"]["pinned"] = pin.get("pinned") if pin.get("known") is True and isinstance(pin.get("pinned"), bool) else None
            d["ui"].pop("pinned_at", None)
            d["group"] = task_group(d)
            d["machine_online"] = online
            d["machine_last_seen"] = seen_at
            d["machine_ips"] = ips
            d["machine_offline_age"] = None if online else fmt_duration(max(0, int(now - seen_at)))
            if d["acked"]:
                d["priority"] = 8  # after running, before idle
            d["age"] = fmt_duration(d["age_sec"])
            if not include_idle and (d["group"] in ("recent", "other") or d["acked"]):
                continue
            res.append(d)
        return sort_tasks(res)

    def counts(self, tasks: List[Dict]) -> Dict[str, int]:
        return {
            "attention": sum(1 for t in tasks if t["group"] == "attention"),
            "done": sum(1 for t in tasks if t["group"] == "done"),
            "running": sum(1 for t in tasks if t["group"] == "running"),
            "recent": sum(1 for t in tasks if t["group"] == "recent"),
            "other": sum(1 for t in tasks if t["group"] == "other"),
            "total": len(tasks),
        }

    def task_summary(self, max_items: int = 8, native_pending=None) -> Dict:
        tasks = self.view(include_idle=False,native_pending=native_pending)
        items = []
        for t in tasks[:max_items]:
            items.append({
                "key": t["key"],
                "state": t["state"],
                "group": t["group"],
                "activity_at": t["activity_at"],
                "state_label": t["state_label"],
                "tool": t["tool_short"],
                "project": (t["project"] or "")[:16],
                "title": (t["title"] or "")[:28],
                "detail": (t["detail"] or "")[:40],
                "age": t["age"],
                "age_sec": t["age_sec"],
                "machine": t["machine"],
                "machine_id": t["machine_id"],
                "machine_online": t["machine_online"],
                "machine_offline_age": t["machine_offline_age"],
            })
        return {
            "updated": time.strftime("%H:%M"),
            "counts": self.counts(self.view(include_idle=True,native_pending=native_pending)),
            "machines": self.machines_status(),
            "items": items,
            "more": max(0, len(tasks) - max_items),
        }

    # --- notifications ----------------------------------------------------------
    def _check_notify(self):
        hooks = self.cfg.get("webhooks") or []
        with self.lock:
            cur = {
                (t.key, t.state, int(t.state_since))
                for t in self.all_tasks()
                if (t.state in ATTENTION_STATES or t.state == DONE) and not self.is_acked(t)
            }
            first = self._notified is None
            new = set() if first else cur - self._notified
            self._notified = cur
        if new and hooks:
            payload = self.task_summary(self.cfg.get("summary_max_items", 8))
            payload["new"] = [k for k, _, _ in new]
            for url in hooks:
                threading.Thread(target=_post, args=(url, payload), daemon=True).start()


def _post(url: str, payload: Dict):
    try:
        req = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=5).read()
        log.info("webhook -> %s ok", url)
    except Exception as e:
        log.warning("webhook -> %s failed: %s", url, e)
