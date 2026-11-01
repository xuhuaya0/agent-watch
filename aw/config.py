"""Configuration: defaults overridable by agent-watch/config.json (and editable via UI / Tray)."""

from __future__ import annotations

import json
import os
import socket
import tempfile
import uuid
from typing import List

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.environ.get("AGENT_WATCH_CONFIG", os.path.join(HERE, "config.json"))

DEFAULTS = {
    "host": "0.0.0.0",
    "port": 8090,
    "machine": socket.gethostname(),
    "machine_id": "",  # stable installation identity; machine is only a display name
    "scan_interval_sec": 5,
    # Workspace lists all available conversations; lookback is an optional legacy mode.
    "include_all_sessions": True,
    # how far back to look for sessions when include_all_sessions is false
    "lookback_days": 7,
    # running but no new events for this long -> stalled
    "stall_after_sec": 600,
    # a single tool call (build, test...) may legitimately run longer
    "stall_after_tool_sec": 1800,
    # Antigravity: a pending sandbox-bypass call older than this -> likely waiting approval
    "approval_guess_after_sec": 20,
    # done/error tasks become idle after this long even if not acknowledged
    "auto_idle_after_sec": 12 * 3600,
    # remote collectors must send "Authorization: Bearer <ingest_token>"
    "ingest_token": "",
    # Get / read-only clients can use "Authorization: Bearer <summary_token>" or "?token=<summary_token>"
    "summary_token": "",
    # remote machine not refreshed for this long is marked offline (tasks are KEPT, never deleted)
    "offline_after_sec": 120,
    # Optional upstream Hub URLs (supports multiple IPs for the same Hub, e.g. LAN IP + WireGuard IP).
    # When non-empty, this node also pushes its local tasks to the first reachable Hub URL.
    "hub_urls": [],
    # Outbound authentication belongs to the destination Hub, not this receiver.
    "hub_token": "",
    # POSTed (JSON summary) when a NEW attention/done item appears, e.g. kindle /api/push
    "webhooks": [],
    "summary_max_items": 8,
    "adapters": {
        "antigravity": {"enabled": True, "root": "~/.gemini/antigravity"},
        "dsh": {"enabled": True, "root": os.environ.get("DSH_HOME", "~/.dsh")},
        "codex": {"enabled": False, "root": os.environ.get("CODEX_HOME", "~/.codex")},
    },
}


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def normalize_hub_urls(val) -> List[str]:
    """Accept a list of URLs or a comma/newline-separated string; return clean base URLs."""
    if not val:
        return []
    if isinstance(val, str):
        raw = [x.strip() for x in val.replace("\n", ",").split(",")]
    elif isinstance(val, list):
        raw = [str(x).strip() for x in val]
    else:
        return []
    out = []
    for u in raw:
        if not u:
            continue
        if "://" not in u:
            u = "http://" + u
        u = u.rstrip("/")
        if u not in out:
            out.append(u)
    return out


def _installation_id(config_path: str) -> str:
    """Persist independently of editable settings, including read-only config mounts."""
    path = os.path.abspath(config_path) + ".machine-id"
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    if not os.path.exists(path):
        # Publish a fully written file, without replacing another process's ID.
        with tempfile.NamedTemporaryFile(mode="w", dir=directory, delete=False) as f:
            tmp = f.name
            f.write(str(uuid.uuid4()))
        try:
            try:
                os.link(tmp, path)
            except FileExistsError:
                pass
        finally:
            os.unlink(tmp)
    with open(path, encoding="utf-8") as f:
        return str(uuid.UUID(f.read().strip()))


def load_config(path: str = CONFIG_FILE) -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))
    stored = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                stored = json.load(f)
                cfg = _merge(cfg, stored)
        except (OSError, ValueError):
            pass
    cfg["hub_urls"] = normalize_hub_urls(cfg.get("hub_urls"))
    # Preserve existing working connections once when upgrading the shared-token
    # schema. An explicit empty hub_token must never fall back to ingest_token.
    if "hub_token" not in stored and cfg["hub_urls"]:
        cfg["hub_token"] = cfg.get("ingest_token", "")
    cfg["machine_id"] = (str(uuid.UUID(cfg["machine_id"])) if cfg.get("machine_id")
                         else _installation_id(path))
    return cfg


def save_config(cfg: dict, path: str = CONFIG_FILE) -> None:
    cfg["hub_urls"] = normalize_hub_urls(cfg.get("hub_urls"))
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
