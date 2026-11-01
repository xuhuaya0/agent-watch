"""Multi-IP Hub pusher: supports LAN + WireGuard dual-IP failover and local IP reporting."""

from __future__ import annotations

import json
import logging
import socket
import urllib.request
import urllib.error
from typing import Dict, List, Optional, Tuple

from .config import normalize_hub_urls
from .model import Task

log = logging.getLogger("agent-watch")


def local_ips() -> List[str]:
    """Discover non-loopback IPv4 addresses on this machine (e.g. LAN + WireGuard interfaces)."""
    ips: List[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except OSError:
        pass
    for probe in ("192.168.1.1", "10.0.0.1", "8.8.8.8"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect((probe, 80))
                ip = s.getsockname()[0]
            if ip and not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
        except OSError:
            pass
    return ips


class MultiIpPusher:
    """Pushes tasks to the first reachable Hub URL (remembers last good URL, auto-failovers)."""

    def __init__(self):
        self.preferred_url: Optional[str] = None
        self.last_ok_url: Optional[str] = None
        self.last_error: str = ""
        self.last_push_at: float = 0.0

    def push(self, urls: List[str], token: str, machine: str, tasks: List[Task], timeout: float = 4.0,
             machine_id: str = "") -> Tuple[bool, str]:
        candidates = normalize_hub_urls(urls)
        if not candidates:
            return False, "no hub_urls configured"
        if not token.strip():
            self.last_error = "未填写上报令牌：请将目标主控的“本机接收令牌”复制到本机“上报令牌”。"
            return False, self.last_error
        if self.preferred_url in candidates:
            candidates = [self.preferred_url] + [u for u in candidates if u != self.preferred_url]

        body = json.dumps({
            "machine": machine,
            "machine_id": machine_id,
            "ips": local_ips(),
            "tasks": [t.to_dict() for t in tasks],
        }, ensure_ascii=False).encode("utf-8")

        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        errors = []
        for base in candidates:
            # Older Hubs may not yet expose the generic Push endpoint.
            for endpoint in ("/api/push", "/api/ingest"):
                url = base.rstrip("/") + endpoint
                req = urllib.request.Request(url, data=body, method="POST", headers=headers)
                try:
                    with urllib.request.urlopen(req, timeout=timeout) as r:
                        if 200 <= r.status < 300:
                            self.preferred_url = base
                            self.last_ok_url = base
                            self.last_error = ""
                            import time
                            self.last_push_at = time.time()
                            return True, base
                        errors.append(f"{base}: HTTP {r.status}")
                except urllib.error.HTTPError as e:
                    code = e.code
                    e.close()
                    if endpoint == "/api/push" and code in (404, 405):
                        continue
                    if code == 401:
                        errors.append(f"{base}: 上报令牌不匹配（401），请复制目标主控的“本机接收令牌”到本机“上报令牌”并保存")
                    else:
                        errors.append(f"{base}: HTTP {code}")
                except Exception as e:
                    errors.append(f"{base}: {e}")
                break

        self.last_error = "; ".join(errors)
        return False, self.last_error
