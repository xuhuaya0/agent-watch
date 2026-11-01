"""Print what the adapters see right now (no server). Usage: python scan_once.py [--json]"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from aw.config import load_config  # noqa: E402
from aw.model import fmt_duration, sort_tasks  # noqa: E402
from aw.scanner import scan_all  # noqa: E402

cfg = load_config()
tasks, errors = scan_all(cfg)
rows = sort_tasks([t.to_dict() for t in tasks])
if "--json" in sys.argv:
    print(json.dumps({"tasks": rows, "errors": errors}, ensure_ascii=False, indent=2))
else:
    for t in rows:
        print(f"{t['tool_short']:<4}{t['state']:<17}{fmt_duration(t['age_sec']):>8}  {t['project'][:14]:<15}{t['title'][:30]:<32}{t['detail'][:60]}")
    for k, e in errors.items():
        print("ERROR", k, e)
