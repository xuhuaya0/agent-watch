r"""
collector.py — 纯命令行采集推送端（支持单机多 IP 自动容灾切换：局域网 + WireGuard）
================================================================================
用法：
  # 支持传入多个 Hub 地址（逗号分隔或多次 --hub），自动选择当前可达的 IP 推送
  python collector.py --hub http://192.0.2.104:8090,http://10.0.0.1:8090 --token <token> [--codex]

  # 或者直接读取 config.json 中的 hub_urls 与 hub_token：
  python collector.py
"""

from __future__ import annotations

import argparse
import logging
import os
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from aw.config import load_config, normalize_hub_urls  # noqa: E402
from aw.pusher import MultiIpPusher  # noqa: E402
from aw.scanner import scan_all  # noqa: E402

log = logging.getLogger("collector")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hub", action="append", default=[],
                    help="Hub URL(s), e.g. http://192.0.2.104:8090,http://10.0.0.1:8090")
    ap.add_argument("--token", default=None, help="目标主控的接收令牌；覆盖 config.json 中的 hub_token")
    ap.add_argument("--machine", default=None)
    ap.add_argument("--interval", type=float, default=10)
    ap.add_argument("--codex", action="store_true", help="enable the Codex adapter")
    ap.add_argument("--no-antigravity", action="store_true")
    ap.add_argument("--no-dsh", action="store_true")
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

    cfg = load_config()
    if a.machine:
        cfg["machine"] = a.machine
    urls = normalize_hub_urls(a.hub) if a.hub else cfg.get("hub_urls", [])
    if not urls:
        ap.error("请通过 --hub 或 config.json 的 hub_urls 指定至少一个 Hub 地址")
    token = a.token if a.token is not None else cfg.get("hub_token", "")

    cfg["adapters"]["codex"]["enabled"] = a.codex or cfg["adapters"]["codex"].get("enabled", False)
    if a.no_antigravity:
        cfg["adapters"]["antigravity"]["enabled"] = False
    if a.no_dsh:
        cfg["adapters"]["dsh"]["enabled"] = False

    pusher = MultiIpPusher()
    while True:
        tasks, errors = scan_all(cfg)
        for k, e in errors.items():
            log.warning("adapter %s: %s", k, e)
        ok, info = pusher.push(urls, token, cfg.get("machine") or socket.gethostname(), tasks,
                               machine_id=cfg.get("machine_id", ""))
        if ok:
            log.info("pushed %d tasks -> %s", len(tasks), info)
        else:
            log.warning("push failed (%s)", info)
        if a.once:
            break
        time.sleep(a.interval)


if __name__ == "__main__":
    main()
