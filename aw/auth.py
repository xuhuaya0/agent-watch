"""Authentication, session management, rate limiting, CSRF protection, and API tokens."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from typing import Dict, Optional, Tuple

COOKIE_NAME = "aw_session"
CSRF_HEADER = "X-CSRF-Token"

DEFAULT_SESSION_TTL = 12 * 3600          # 12 hours when "Remember me" is unchecked
REMEMBER_SESSION_TTL = 30 * 24 * 3600    # 30 days when "Remember me" is checked
MAX_LOGIN_FAILS = 5                      # lock IP after 5 consecutive failures
FAIL_WINDOW_SEC = 600                    # within 10 minutes
LOCKOUT_SEC = 300                        # lock for 5 minutes


def _hash_password(password: str, salt_hex: str) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), 200_000)
    return dk.hex()


class AuthManager:
    def __init__(self, data_dir: str, cfg: Dict):
        self.lock = threading.RLock()
        os.makedirs(data_dir, exist_ok=True)
        self.path = os.path.join(data_dir, "auth.json")
        self.cfg = cfg
        self.state = self._load()
        # ip -> {"fails": int, "first_at": float, "locked_until": float}
        self._attempts: Dict[str, Dict[str, float]] = {}
        # revoked session IDs (on explicit logout)
        self._revoked: Dict[str, float] = {}

        # Bootstrap from environment variable if provided and no password set yet
        env_pw = os.environ.get("AGENT_WATCH_PASSWORD", "").strip()
        if env_pw and not self.has_password():
            self.set_password(env_pw)

        # Ensure independent random tokens exist for Push ingestion and Get reading
        changed = False
        if not self.cfg.get("ingest_token"):
            self.cfg["ingest_token"] = secrets.token_urlsafe(24)
            changed = True
        if not self.cfg.get("summary_token"):
            self.cfg["summary_token"] = secrets.token_urlsafe(24)
            changed = True
        if changed:
            from .config import save_config
            try:
                save_config(self.cfg)
            except OSError:
                pass

    def _load(self) -> Dict:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                d = json.load(f)
                if isinstance(d, dict) and "secret" in d:
                    return d
        except (OSError, ValueError):
            pass
        st = {
            "salt": "",
            "password_hash": "",
            "pwd_ver": 1,
            "secret": secrets.token_hex(32),
            "updated_at": 0,
        }
        self._save(st)
        return st

    def _save(self, st: Optional[Dict] = None) -> None:
        st = st or self.state
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, indent=2)
        os.replace(tmp, self.path)

    # --- password management --------------------------------------------------
    def has_password(self) -> bool:
        with self.lock:
            return bool(self.state.get("password_hash") and self.state.get("salt"))

    def set_password(self, new_password: str) -> None:
        if len(new_password) < 4:
            raise ValueError("密码长度至少 4 位")
        with self.lock:
            salt = secrets.token_hex(16)
            self.state["salt"] = salt
            self.state["password_hash"] = _hash_password(new_password, salt)
            # Increment version and rotate signing secret -> invalidates ALL old sessions immediately
            self.state["pwd_ver"] = int(self.state.get("pwd_ver", 1)) + 1
            self.state["secret"] = secrets.token_hex(32)
            self.state["updated_at"] = time.time()
            self._revoked.clear()
            self._save()

    def verify_password(self, password: str) -> bool:
        with self.lock:
            if not self.has_password():
                return False
            expected = self.state["password_hash"]
            actual = _hash_password(password, self.state["salt"])
            return hmac.compare_digest(expected, actual)

    # --- rate limiting --------------------------------------------------------
    def check_rate_limit(self, ip: str, now: Optional[float] = None) -> Tuple[bool, int]:
        """Return (allowed, retry_after_sec)."""
        now = now or time.time()
        with self.lock:
            rec = self._attempts.get(ip)
            if not rec:
                return True, 0
            if rec.get("locked_until", 0) > now:
                return False, max(1, int(rec["locked_until"] - now))
            if now - rec.get("first_at", 0) > FAIL_WINDOW_SEC:
                self._attempts.pop(ip, None)
            return True, 0

    def record_login_result(self, ip: str, success: bool, now: Optional[float] = None) -> Optional[int]:
        """Record login attempt; return lockout seconds if just locked, else None."""
        now = now or time.time()
        with self.lock:
            if success:
                self._attempts.pop(ip, None)
                return None
            rec = self._attempts.get(ip)
            if not rec or (now - rec.get("first_at", 0) > FAIL_WINDOW_SEC):
                rec = {"fails": 0, "first_at": now, "locked_until": 0.0}
                self._attempts[ip] = rec
            rec["fails"] += 1
            if rec["fails"] >= MAX_LOGIN_FAILS:
                rec["locked_until"] = now + LOCKOUT_SEC
                return LOCKOUT_SEC
            return None

    # --- session tokens & CSRF ------------------------------------------------
    def create_session(self, remember: bool = False, now: Optional[float] = None) -> Tuple[str, str, int]:
        """Return (cookie_value, csrf_token, max_age_sec)."""
        now = now or time.time()
        max_age = REMEMBER_SESSION_TTL if remember else DEFAULT_SESSION_TTL
        exp = int(now + max_age)
        sid = secrets.token_hex(12)
        with self.lock:
            ver = self.state["pwd_ver"]
            secret = self.state["secret"].encode("utf-8")
        payload = f"{sid}.{ver}.{exp}"
        sig = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
        csrf = hmac.new(secret, f"csrf.{payload}".encode("utf-8"), hashlib.sha256).hexdigest()[:32]
        return f"{payload}.{sig}", csrf, max_age

    def verify_session(self, token: str, now: Optional[float] = None) -> Optional[Dict]:
        """Verify session cookie. Return {'sid', 'exp', 'csrf'} if valid, else None."""
        if not token:
            return None
        parts = token.split(".")
        if len(parts) != 4:
            return None
        sid, ver_s, exp_s, sig = parts
        try:
            ver = int(ver_s)
            exp = int(exp_s)
        except ValueError:
            return None
        now = now or time.time()
        if now > exp:
            return None
        with self.lock:
            if not self.has_password():
                return None
            if ver != self.state.get("pwd_ver"):
                return None
            if sid in self._revoked:
                return None
            secret = self.state["secret"].encode("utf-8")
        payload = f"{sid}.{ver}.{exp}"
        expected_sig = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return None
        csrf = hmac.new(secret, f"csrf.{payload}".encode("utf-8"), hashlib.sha256).hexdigest()[:32]
        return {"sid": sid, "exp": exp, "csrf": csrf}

    def revoke_session(self, token: str) -> None:
        info = self.verify_session(token)
        if not info:
            return
        with self.lock:
            now = time.time()
            self._revoked = {k: v for k, v in self._revoked.items() if v > now}
            self._revoked[info["sid"]] = info["exp"]

    @staticmethod
    def verify_csrf(session_info: Optional[Dict], header_val: str) -> bool:
        if not session_info or not header_val:
            return False
        return hmac.compare_digest(session_info["csrf"], header_val.strip())

    # --- independent API tokens -----------------------------------------------
    def verify_ingest_token(self, header_auth: str) -> bool:
        token = (self.cfg.get("ingest_token") or "").strip()
        if not token:
            return False
        prefix = "Bearer "
        if not header_auth.startswith(prefix):
            return False
        return hmac.compare_digest(token, header_auth[len(prefix):].strip())

    def verify_summary_token(self, header_auth: str, query_token: str) -> bool:
        token = (self.cfg.get("summary_token") or "").strip()
        if not token:
            return False
        candidate = ""
        if header_auth.startswith("Bearer "):
            candidate = header_auth[7:].strip()
        elif query_token:
            candidate = query_token.strip()
        if not candidate:
            return False
        return hmac.compare_digest(token, candidate)
