"""Run all enabled adapters on this machine."""

from __future__ import annotations

import logging
import time
import traceback
from typing import Dict, List, Tuple

from .adapters import antigravity, codex, dsh
from .model import Task

log = logging.getLogger("agent-watch")

ADAPTERS = {"antigravity": antigravity.scan, "dsh": dsh.scan, "codex": codex.scan}


def scan_all(cfg: Dict) -> Tuple[List[Task], Dict[str, str]]:
    now = time.time()
    tasks: List[Task] = []
    errors: Dict[str, str] = {}
    for name, fn in ADAPTERS.items():
        if not cfg["adapters"].get(name, {}).get("enabled"):
            continue
        try:
            tasks.extend(fn(cfg, now))
        except Exception as e:  # one broken adapter must not kill the others
            errors[name] = f"{type(e).__name__}: {e}"
            log.debug(traceback.format_exc())
    for task in tasks:
        task.machine_id = cfg.get("machine_id", "")
    return tasks, errors
