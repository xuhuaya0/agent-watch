r"""
hub.py — Agent Watch 主服务（内置管理员登录 + CSRF/限速防护 + Push/Get 双 Token 认证）
================================================================================
启动：
  - 带系统托盘/菜单栏图标（推荐 Win/Mac）：python tray.py
  - 纯命令行/Docker 容器运行：              python hub.py
访问：  http://localhost:8090/
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import secrets
import sys
import threading
import time
import uuid
from functools import wraps
from urllib.parse import unquote

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from flask import Flask, Response, g, jsonify, request, send_from_directory  # noqa: E402

from aw import autostart  # noqa: E402
from aw.auth import COOKIE_NAME, CSRF_HEADER, AuthManager  # noqa: E402
from aw.config import CONFIG_FILE, load_config, normalize_hub_urls, save_config  # noqa: E402
from aw.pusher import MultiIpPusher, local_ips  # noqa: E402
from aw.scanner import scan_all  # noqa: E402
from aw.control.broker import Broker, ControlError
from aw.store import Store  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("agent-watch")

DATA_DIR = os.environ.get("AGENT_WATCH_DATA_DIR", os.path.join(HERE, "data"))
CFG = load_config(os.environ.get("AGENT_WATCH_CONFIG", CONFIG_FILE))
STORE = Store(CFG, DATA_DIR)
AUTH = AuthManager(DATA_DIR, CFG)
CONTROL = Broker(DATA_DIR)
PUSHER = MultiIpPusher()
_SCAN_WAKE = threading.Event()

app = Flask(__name__, static_folder=os.path.join(HERE, "static"))
app.json.ensure_ascii = False
app.json.mimetype = "application/json; charset=utf-8"


# ---------------------------------------------------------------------------
# Auth helpers & decorators
# ---------------------------------------------------------------------------

def _current_session():
    token = request.cookies.get(COOKIE_NAME, "")
    return AUTH.verify_session(token)


def _set_session_cookie(resp: Response, cookie_val: str, max_age: int, remember: bool):
    resp.set_cookie(
        COOKIE_NAME,
        cookie_val,
        max_age=max_age if remember else None,
        httponly=True,
        samesite="Lax",
        path="/",
    )


def require_admin(check_csrf: bool = False):
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not AUTH.has_password():
                return jsonify({"ok": False, "error": "needs_setup", "needs_setup": True}), 401
            sess = _current_session()
            if not sess:
                return jsonify({"ok": False, "error": "unauthorized"}), 401
            if check_csrf and request.method in ("POST", "PUT", "DELETE", "PATCH"):
                if not AUTH.verify_csrf(sess, request.headers.get(CSRF_HEADER, "")):
                    return jsonify({"ok": False, "error": "csrf_invalid"}), 403
            g.session = sess
            return fn(*args, **kwargs)
        return wrapper
    return deco


# ---------------------------------------------------------------------------
# Background scanner & upstream pusher
# ---------------------------------------------------------------------------

def scan_once_and_push():
    tasks, errors = scan_all(CFG)
    STORE.set_local(tasks, errors)
    for k, e in errors.items():
        log.warning("adapter %s: %s", k, e)
    urls = CFG.get("hub_urls") or []
    if urls:
        PUSHER.push(urls, CFG.get("hub_token", ""), CFG.get("machine", ""), tasks,
                    machine_id=CFG.get("machine_id", ""))


def scanner_loop():
    while True:
        t0 = time.time()
        try:
            scan_once_and_push()
        except Exception:
            log.exception("scan failed")
        interval = max(1, int(CFG.get("scan_interval_sec", 5)))
        _SCAN_WAKE.wait(max(0.5, interval - (time.time() - t0)))
        _SCAN_WAKE.clear()


def trigger_scan():
    _SCAN_WAKE.set()


def _etag(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Auth API (Login / Setup / Logout / Change Password / Rotate Tokens)
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/auth/status")
def api_auth_status():
    if not AUTH.has_password():
        return jsonify({"ok": True, "authenticated": False, "needs_setup": True})
    sess = _current_session()
    if not sess:
        return jsonify({"ok": True, "authenticated": False, "needs_setup": False})
    return jsonify({
        "ok": True,
        "authenticated": True,
        "needs_setup": False,
        "csrf_token": sess["csrf"],
    })


@app.route("/api/auth/setup", methods=["POST"])
def api_auth_setup():
    if AUTH.has_password():
        return jsonify({"ok": False, "error": "管理员密码已设置，请直接登录"}), 400
    data = request.get_json(silent=True) or {}
    pw = str(data.get("password") or "")
    remember = bool(data.get("remember", True))
    try:
        AUTH.set_password(pw)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    cookie_val, csrf, max_age = AUTH.create_session(remember=remember)
    resp = jsonify({"ok": True, "csrf_token": csrf})
    _set_session_cookie(resp, cookie_val, max_age, remember)
    return resp


@app.route("/api/auth/login", methods=["POST"])
def api_auth_login():
    if not AUTH.has_password():
        return jsonify({"ok": False, "error": "needs_setup", "needs_setup": True}), 400
    ip = request.remote_addr or "unknown"
    allowed, retry_after = AUTH.check_rate_limit(ip)
    if not allowed:
        return jsonify({
            "ok": False,
            "error": f"登录失败次数过多，请 {retry_after} 秒后再试",
            "retry_after": retry_after,
        }), 429

    data = request.get_json(silent=True) or {}
    pw = str(data.get("password") or "")
    remember = bool(data.get("remember", False))
    if not AUTH.verify_password(pw):
        locked = AUTH.record_login_result(ip, False)
        if locked:
            return jsonify({
                "ok": False,
                "error": f"连续失败次数过多，已锁定 {locked} 秒",
                "retry_after": locked,
            }), 429
        return jsonify({"ok": False, "error": "密码错误"}), 401

    AUTH.record_login_result(ip, True)
    cookie_val, csrf, max_age = AUTH.create_session(remember=remember)
    resp = jsonify({"ok": True, "csrf_token": csrf})
    _set_session_cookie(resp, cookie_val, max_age, remember)
    return resp


@app.route("/api/auth/logout", methods=["POST"])
@require_admin(check_csrf=True)
def api_auth_logout():
    AUTH.revoke_session(request.cookies.get(COOKIE_NAME, ""))
    resp = jsonify({"ok": True})
    resp.delete_cookie(COOKIE_NAME, path="/")
    return resp


@app.route("/api/auth/password", methods=["POST"])
@require_admin(check_csrf=True)
def api_auth_password():
    data = request.get_json(silent=True) or {}
    old_pw = str(data.get("old_password") or "")
    new_pw = str(data.get("new_password") or "")
    remember = bool(data.get("remember", True))
    if not AUTH.verify_password(old_pw):
        return jsonify({"ok": False, "error": "当前密码不正确"}), 400
    try:
        # Increments pwd_ver and rotates secret -> invalidates ALL old sessions
        AUTH.set_password(new_pw)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    cookie_val, csrf, max_age = AUTH.create_session(remember=remember)
    resp = jsonify({"ok": True, "csrf_token": csrf})
    _set_session_cookie(resp, cookie_val, max_age, remember)
    return resp


@app.route("/api/auth/rotate-token", methods=["POST"])
@require_admin(check_csrf=True)
def api_rotate_token():
    data = request.get_json(silent=True) or {}
    kind = data.get("kind")
    if kind not in ("ingest_token", "summary_token"):
        return jsonify({"ok": False, "error": "invalid token kind"}), 400
    CFG[kind] = secrets.token_urlsafe(24)
    save_config(CFG)
    return jsonify({"ok": True, "kind": kind, "token": CFG[kind]})


# ---------------------------------------------------------------------------
# Protected Business APIs
# ---------------------------------------------------------------------------

@app.route("/api/tasks")
@require_admin(check_csrf=False)
def api_tasks():
    include_idle = request.args.get("all") == "1"
    tasks = STORE.view(include_idle=True,native_pending=CONTROL.pending_states())
    # The list polls frequently; full captured messages are fetched on selection.
    tasks = [{**t, "extra": {k: v for k, v in t["extra"].items() if k != "latest_turn"}} for t in tasks]
    return jsonify({
        "ok": True,
        "machine": CFG["machine"],
        "scanned_at": STORE.scanned_at,
        "counts": STORE.counts(tasks),
        "machines": STORE.machines_status(),
        "tasks": tasks if include_idle else [t for t in tasks if t["group"] not in ("recent", "other") and not t["acked"]],
        "errors": STORE.errors,
    })


@app.route("/api/tasks/<path:key>")
@require_admin(check_csrf=False)
def api_task_detail(key):
    task = next((t for t in STORE.view(native_pending=CONTROL.pending_states()) if t["key"] == key), None)
    return (jsonify({"ok": True, "task": task}) if task else
            (jsonify({"ok": False, "error": "task_not_found"}), 404))


@app.route("/api/workspace/tasks/<path:key>", methods=["PATCH"])
@require_admin(check_csrf=True)
def api_workspace_task(key):
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not data or set(data) - {"pinned", "read_at"}:
        return jsonify({"ok": False, "error": "invalid_preferences"}), 400
    for name, value in data.items():
        if name == "read_at":
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                return jsonify({"ok": False, "error": "invalid_read_time"}), 400
        elif not isinstance(value, bool):
            return jsonify({"ok": False, "error": "expected_boolean"}), 400
    if "pinned" in data:
        return jsonify({"ok": False, "error": "native_pin_read_only", "message": "请在原 Harness 中修改置顶，采集后会自动同步显示"}), 409
    state = STORE.update_ui(key, data)
    return (jsonify({"ok": True, "ui": state}) if state is not None else
            (jsonify({"ok": False, "error": "task_not_found"}), 404))


def _summary():
    max_items = int(request.args.get("max", CFG.get("summary_max_items", 8)))
    s = STORE.task_summary(max_items,native_pending=CONTROL.pending_states())
    return s, _etag({k: v for k, v in s.items() if k != "updated"})


@app.route("/api/get")
@app.route("/api/summary")
@app.route("/api/eink/summary")
def api_get():
    # Allowed via either: (1) independent read-only summary_token, or (2) logged-in admin session
    has_token = AUTH.verify_summary_token(
        request.headers.get("Authorization", ""),
        request.args.get("token", ""),
    )
    if not has_token and not _current_session():
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    s, tag = _summary()
    since = request.args.get("since", "").strip() or request.headers.get("If-None-Match", "").strip('"')
    headers = {"ETag": f'"{tag}"', "X-Summary-Hash": tag, "Cache-Control": "no-store"}
    if since == tag:
        return Response(status=304, headers=headers)
    resp = jsonify(s)
    for k, v in headers.items():
        resp.headers[k] = v
    return resp


@app.route("/api/tasks/<path:key>/ack", methods=["POST"])
@require_admin(check_csrf=True)
def api_ack(key):
    ok = STORE.ack(unquote(key))
    return jsonify({"ok": ok}), (200 if ok else 404)


@app.route("/api/machines/<path:machine>", methods=["DELETE"])
@require_admin(check_csrf=True)
def api_delete_machine(machine):
    ok = STORE.remove_machine(unquote(machine))
    return jsonify({"ok": ok}), (200 if ok else 404)


@app.route("/api/push", methods=["POST"])
@app.route("/api/ingest", methods=["POST"])
def api_push():
    # Collector independent authentication: Bearer <ingest_token>
    if not AUTH.verify_ingest_token(request.headers.get("Authorization", "")):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "expected an object"}), 400
    machine = str(data.get("machine") or request.remote_addr or "remote").strip()
    tasks = data.get("tasks", [])
    try:
        machine_id = str(uuid.UUID(data["machine_id"])) if data.get("machine_id") else ""
        if not isinstance(tasks, list) or any(not isinstance(t, dict) for t in tasks):
            raise ValueError("tasks must be a list of objects")
        STORE.ingest(
            machine=machine, machine_id=machine_id, tasks=tasks,
            remote_ip=request.remote_addr or "",
            extra_ips=data.get("ips") if isinstance(data.get("ips"), list) else [],
        )
    except (ValueError, TypeError, AttributeError):
        return jsonify({"ok": False, "error": "invalid device identity or task payload"}), 400
    return jsonify({"ok": True, "received": len(tasks)})


@app.route("/api/settings", methods=["GET", "POST"])
@require_admin(check_csrf=True)
def api_settings():
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        if "machine" in data and str(data["machine"]).strip():
            CFG["machine"] = str(data["machine"]).strip()
        if "hub_urls" in data:
            CFG["hub_urls"] = normalize_hub_urls(data["hub_urls"])
        if "hub_token" in data:
            CFG["hub_token"] = str(data["hub_token"] or "").strip()
        if "ingest_token" in data and str(data["ingest_token"]).strip():
            CFG["ingest_token"] = str(data["ingest_token"]).strip()
        if "summary_token" in data and str(data["summary_token"]).strip():
            CFG["summary_token"] = str(data["summary_token"]).strip()
        if "offline_after_sec" in data:
            CFG["offline_after_sec"] = max(15, int(data["offline_after_sec"]))
        if "webhooks" in data:
            wh = data["webhooks"]
            if isinstance(wh, str):
                wh = [x.strip() for x in wh.replace("\n", ",").split(",") if x.strip()]
            CFG["webhooks"] = [str(x).strip() for x in (wh or []) if str(x).strip()]
        if "adapters" in data and isinstance(data["adapters"], dict):
            for k in ("antigravity", "dsh", "codex"):
                if k in data["adapters"]:
                    v = data["adapters"][k]
                    if isinstance(v, bool):
                        CFG["adapters"][k]["enabled"] = v
                    elif isinstance(v, dict):
                        CFG["adapters"][k].update(v)
        if "autostart" in data:
            autostart.set_enabled(bool(data["autostart"]))
        save_config(CFG)
        trigger_scan()

    return jsonify({
        "ok": True,
        "machine": CFG["machine"],
        "port": CFG["port"],
        "machine_id": CFG.get("machine_id", ""),
        "local_ips": local_ips(),
        "autostart": autostart.is_enabled(),
        "platform": sys.platform,
        "hub_urls": CFG.get("hub_urls", []),
        "hub_token": CFG.get("hub_token", ""),
        "ingest_token": CFG.get("ingest_token", ""),
        "summary_token": CFG.get("summary_token", ""),
        "offline_after_sec": CFG.get("offline_after_sec", 120),
        "webhooks": CFG.get("webhooks", []),
        "adapters": {k: {"enabled": bool(v.get("enabled")), "root": v.get("root", "")}
                     for k, v in CFG["adapters"].items()},
        "pusher": {
            "last_ok_url": PUSHER.last_ok_url,
            "last_error": PUSHER.last_error,
            "last_push_at": PUSHER.last_push_at,
        },
    })


@app.route("/api/health")
def api_health():
    # Minimal public probe (does not leak tasks, machine IPs, or tokens)
    return jsonify({"ok": True, "scanned_at": STORE.scanned_at})


_SCANNER_STARTED = False


def start_background_scanner():
    global _SCANNER_STARTED
    if not _SCANNER_STARTED:
        _SCANNER_STARTED = True
        threading.Thread(target=scanner_loop, daemon=True).start()


def main():
    start_background_scanner()
    log.info("Agent Watch on http://%s:%s/ (machine=%s, ips=%s)",
             CFG["host"], CFG["port"], CFG["machine"], local_ips())
    app.run(host=CFG["host"], port=int(CFG["port"]), threaded=True, use_reloader=False)



# Native interaction uses separately provisioned, device-bound credentials.
@app.errorhandler(ControlError)
def control_error(error):
    return jsonify({"ok": False, "error": error.code}), error.status


def control_task(key):
    task = next((t for t in STORE.view() if t["ui_key"] == key), None)
    if task is None:
        raise ControlError("task_not_found", 404)
    device = task.get("machine_id")
    if not device:
        raise ControlError("device_identity_required")
    session = task["extra"].get("thread_id") if task["tool"] == "codex" else task["session_id"]
    return device, task["tool"], session


@app.route("/api/control/tasks/<path:key>", methods=["GET", "POST", "PATCH"])
@require_admin(check_csrf=True)
def api_control_task(key):
    identity = control_task(key)
    if request.method == "GET":
        return jsonify({"ok": True, **CONTROL.status(*identity)})
    if request.content_length is None or request.content_length > 65536:
        raise ControlError("invalid_body_size", 413)
    data = request.get_json(silent=True)
    if request.method == "PATCH":
        if (not isinstance(data, dict) or not {'id', 'operation'} <= data.keys() <= {'id', 'operation', 'revision'}):
            raise ControlError("invalid_queue_operation", 400)
        return jsonify({"ok": True, "command": CONTROL.manage_waiting(*identity, data['id'], data['operation'], data.get('revision'))})
    if not isinstance(data, dict) or set(data) != {"id", "action"}:
        raise ControlError("invalid_command", 400)
    return jsonify({"ok": True, "command": CONTROL.submit(*identity, data["id"], data["action"])})


@app.route("/api/history/tasks/<path:key>")
@require_admin()
def api_task_history(key):
    identity = control_task(key)
    result = CONTROL.history(*identity, request.args.get('cursor'))
    response = jsonify({'ok': True, **result})
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.route("/api/attachments/tasks/<path:key>")
@require_admin()
def api_task_attachment(key):
    from aw.control.attachments import validate_media
    if set(request.args) != {'id'}:
        raise ControlError('invalid_attachment', 400)
    result = CONTROL.history(*control_task(key), attachment=request.args['id'])
    if result.get('media') and result.get('ready'):
        media = result['media']
        data = validate_media(media)
        from aw.control.html_preview import preview_html, CSP
        response = Response(preview_html(data) if media['mime'] == 'text/html' else data, mimetype=media['mime'])
        response.headers['Content-Disposition'] = 'inline; filename="attachment"'
        response.headers['Content-Security-Policy'] = CSP if media['mime'] == 'text/html' else "default-src 'none'; sandbox"
        response.headers['X-Content-Type-Options'] = 'nosniff'
    else:
        response = jsonify({'ok': True, **result})
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Cross-Origin-Resource-Policy'] = 'same-origin'
    return response


@app.route("/api/control/devices", methods=["GET"])
@require_admin()
def api_control_devices():
    devices = {}
    for task in STORE.view():
        if task.get("machine_id"):
            devices[task["machine_id"]] = task.get("machine", task["machine_id"])
    # Retain configured connections even when their collector is temporarily absent.
    with CONTROL.db() as db:
        for row in db.execute('SELECT id FROM devices'):
            devices.setdefault(row[0], row[0])
    response = jsonify({"ok":True, "devices":[{**CONTROL.device_status(device), "name":name} for device,name in devices.items()]})
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/control/devices/<device>", methods=["POST", "DELETE", "PATCH"])
@require_admin(check_csrf=True)
def api_control_device(device):
    if request.method == "PATCH":
        data = request.get_json(silent=True)
        if not isinstance(data,dict) or set(data) != {"enabled"}:
            raise ControlError("invalid_control_setting",400)
        return jsonify({"ok":True, **CONTROL.set_enabled(device,data["enabled"])})
    if request.method == "DELETE":
        CONTROL.revoke(device)
        return jsonify({"ok": True})
    if not any(t.get("machine_id") == device for t in STORE.view()):
        raise ControlError("unknown_device", 404)
    token = CONTROL.provision(device)
    response = jsonify({"ok": True, "device_id": device, "token": token})
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/control/exchange", methods=["POST"])
def api_control_exchange():
    if request.content_length is None or request.content_length > 4*1024*1024:
        raise ControlError("invalid_body_size", 413)
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not {"device_id", "worker_id", "sessions", "receipts"} <= set(data) or set(data) - {"device_id", "worker_id", "sessions", "receipts", "read_results"}:
        raise ControlError("invalid_exchange", 400)
    header = request.headers.get("Authorization", "")
    # Serialize revocation with the authenticated exchange, including its claim.
    with CONTROL.lock:
        if not header.startswith("Bearer ") or not CONTROL.authenticate(data["device_id"], header[7:]):
            raise ControlError("unauthorized", 401)
        result = CONTROL.exchange(data["device_id"], data["worker_id"], data["sessions"], data["receipts"], data.get("read_results"))
    return jsonify({"ok": True, **result})

if __name__ == "__main__":
    main()
