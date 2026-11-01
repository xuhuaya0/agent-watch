import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aw.adapters import antigravity as ag  # noqa: E402
from aw.adapters import dsh  # noqa: E402
from aw.config import DEFAULTS  # noqa: E402
from aw.model import Task  # noqa: E402

CFG = dict(DEFAULTS)
NOW = 1_800_000_000.0


def ev(t, sec, **data):
    return {"type": t, "time": int((NOW - sec) * 1000), "data": data}


def run_dsh(events, now=NOW):
    st = dsh.new_state()
    for e in events:
        dsh.reduce_event(st, e)
    return dsh.derive(st, now, CFG)


class DshTests(unittest.TestCase):
    base = [
        {"type": "session", "id": "session-x", "cwd": "C:\\p\\esp32", "createdAt": int((NOW - 1000) * 1000)},
        ev("user/message", 900, content=[{"type": "text", "text": "build it"}], source={"kind": "user"}),
        ev("turn/start", 900, turn=1),
    ]

    def test_running(self):
        state, since, start, prompt, detail = run_dsh(self.base + [ev("tool/call", 10, callId="c1", name="pwsh")])
        self.assertEqual(state, "running")
        self.assertEqual(prompt, "build it")
        self.assertAlmostEqual(start, NOW - 900, delta=1)
        self.assertIn("pwsh", detail)

    def test_waiting_approval(self):
        s = run_dsh(self.base + [ev("tool/call", 30, callId="c1", name="pwsh"),
                                 ev("approval/asked", 29, id="a1", toolName="pwsh", reason="need full access")])
        self.assertEqual(s[0], "waiting_approval")
        self.assertIn("need full access", s[4])

    def test_approval_decided_back_to_running(self):
        s = run_dsh(self.base + [ev("approval/asked", 29, id="a1", toolName="pwsh", reason="x"),
                                 ev("approval/decided", 5, id="a1", outcome="allowed-once")])
        self.assertEqual(s[0], "running")

    def test_waiting_input(self):
        s = run_dsh(self.base + [ev("tool/call", 30, callId="q", name="ask_user_question")])
        self.assertEqual(s[0], "waiting_input")

    def test_stalled(self):
        s = run_dsh(self.base + [ev("step/start", 700)])
        self.assertEqual(s[0], "stalled")

    def test_long_tool_not_stalled(self):
        s = run_dsh(self.base + [ev("tool/call", 700, callId="c1", name="idf.py build")])
        self.assertEqual(s[0], "running")

    def test_done_and_error_and_abort(self):
        done = run_dsh(self.base + [ev("assistant/message", 60, message={"content": [{"type": "text", "text": "all good\nmore"}]}),
                                    ev("turn/end", 60, turn=1, reason={"kind": "completed"})])
        self.assertEqual(done[0], "done")
        self.assertEqual(done[4], "all good")
        err = run_dsh(self.base + [ev("turn/end", 60, turn=1, reason={"kind": "error", "error": {"message": "boom"}})])
        self.assertEqual((err[0], err[4]), ("error", "boom"))
        ab = run_dsh(self.base + [ev("turn/end", 60, turn=1, reason={"kind": "aborted"})])
        self.assertEqual(ab[0], "idle")

    def test_old_done_is_idle(self):
        s = run_dsh(self.base + [ev("turn/end", 13 * 3600, turn=1, reason={"kind": "completed"})])
        self.assertEqual(s[0], "idle")

    def test_app_bookkeeping_does_not_count_as_activity(self):
        st = dsh.new_state()
        for e in self.base + [ev("turn/end", 5000, turn=1, reason={"kind": "completed"}),
                              ev("session/end-seed", 10)]:
            dsh.reduce_event(st, e)
        self.assertAlmostEqual(st["last_event_at"], NOW - 5000, delta=1)
        self.assertEqual(st["turns"], 1)

    def test_scan_real_layout_with_subagent(self):
        try:
            import zstandard
        except ImportError:
            self.skipTest("zstandard missing")
        c = zstandard.ZstdCompressor()
        now = time.time()

        def e(t, sec, **data):
            return {"type": t, "time": int((now - sec) * 1000), "data": data}

        with tempfile.TemporaryDirectory() as root:
            wsdir = os.path.join(root, "sessions", "--C-p-esp32--")
            parent = [{"type": "session", "id": "session-p", "cwd": "C:\\p\\esp32"},
                      e("user/message", 100, content=[{"type": "text", "text": "review"}], source={"kind": "user"}),
                      e("turn/start", 100, turn=1),
                      e("subagent/catalog", 90, childId="kid", label="Code review"),
                      e("tool/call", 90, callId="c", name="spawn")]
            child = [{"type": "session", "id": "kid", "cwd": "C:\\p\\esp32"},
                     e("turn/start", 80, turn=1),
                     e("approval/asked", 5, id="a", toolName="pwsh", reason="x")]
            for sid, evs in (("session-p", parent), ("kid", child)):
                os.makedirs(os.path.join(wsdir, sid))
                with open(os.path.join(wsdir, sid, "session.v4.jsonl.zstd"), "wb") as f:
                    for x in evs:
                        f.write(c.compress((json.dumps(x) + "\n").encode()))
            os.makedirs(os.path.join(root, "storages"))
            with open(os.path.join(root, "storages", "workspace.json"), "w") as f:
                json.dump({"tables": {"workspaces": {"w": {"path": "C:\\p\\esp32", "title": "ESP",
                                                           "sessionIds": ["session-p"]}}}}, f)
            cfg = json.loads(json.dumps(DEFAULTS))
            cfg["adapters"]["dsh"]["root"] = root
            tasks = dsh.scan(cfg, now)
            self.assertEqual(len(tasks), 1)  # child is folded into the parent
            t = tasks[0]
            self.assertEqual((t.project, t.state), ("ESP", "waiting_approval"))
            self.assertIn("Code review", t.detail)
            self.assertEqual(t.extra["subagents"][0]["state"], "waiting_approval")

    def test_incremental_zstd(self):
        try:
            import zstandard
        except ImportError:
            self.skipTest("zstandard missing")
        c = zstandard.ZstdCompressor(write_checksum=True)
        lines = [json.dumps(e) + "\n" for e in self.base]
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "session.v4.jsonl.zstd")
            frames = [c.compress(l.encode()) for l in lines]
            with open(p, "wb") as f:
                f.write(frames[0] + frames[1])
                f.write(frames[2][: len(frames[2]) // 2])  # torn tail
            log = dsh.SessionLog(p)
            log.update()
            self.assertIsNone(log.state["open_turn"])
            self.assertEqual(log.state["last_prompt"], "build it")
            with open(p, "wb") as f:
                f.write(b"".join(frames))
            log.update()
            self.assertIsNotNone(log.state["open_turn"])


def step(i, typ, sec, **kw):
    t = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW - sec))
    d = {"step_index": i, "type": typ, "status": kw.pop("status", "DONE"), "created_at": t}
    d.update(kw)
    return d


def run_ag(steps, mtime_ago=None):
    last_sec = mtime_ago if mtime_ago is not None else 0
    return ag.analyze(steps, NOW, NOW - last_sec, CFG)


class AntigravityTests(unittest.TestCase):
    u = step(0, "USER_INPUT", 600, content="<USER_REQUEST>\nfix kindle\n</USER_REQUEST>\n<ADDITIONAL_METADATA>x</ADDITIONAL_METADATA>")

    def test_done(self):
        a = run_ag([self.u, step(1, "PLANNER_RESPONSE", 30, content="Fixed it.\nDetails", tool_calls=[])], mtime_ago=25)
        self.assertEqual(a["state"], "done")
        self.assertEqual(a["prompt"], "fix kindle")
        self.assertEqual(a["detail"], "Fixed it.")

    def test_running_tool(self):
        a = run_ag([self.u, step(1, "PLANNER_RESPONSE", 5, tool_calls=[{"name": "run_command", "args": {}}])], mtime_ago=5)
        self.assertEqual(a["state"], "running")
        self.assertAlmostEqual(a["turn_start"], NOW - 600, delta=2)

    def test_waiting_approval_guess(self):
        calls = [{"name": "run_command", "args": {"BypassSandbox": "true", "toolSummary": '"Install x"'}}]
        a = run_ag([self.u, step(1, "PLANNER_RESPONSE", 60, tool_calls=calls)], mtime_ago=60)
        self.assertEqual(a["state"], "waiting_approval")
        self.assertIn("Install x", a["detail"])

    def test_partial_results(self):
        calls = [{"name": "view_file", "args": {}}, {"name": "run_command", "args": {"BypassSandbox": True}}]
        steps = [self.u, step(1, "PLANNER_RESPONSE", 60, tool_calls=calls), step(2, "GENERIC", 59, content="ok")]
        self.assertEqual(run_ag(steps, mtime_ago=59)["state"], "waiting_approval")

    def test_ask_question(self):
        a = run_ag([self.u, step(1, "PLANNER_RESPONSE", 5, tool_calls=[{"name": "ask_question", "args": {}}])], mtime_ago=5)
        self.assertEqual(a["state"], "waiting_input")

    def test_plan_review(self):
        calls = [{"name": "write_file", "args": {"ArtifactMetadata": '{"RequestFeedback": true, "Summary": "x"}'}}]
        steps = [self.u, step(1, "PLANNER_RESPONSE", 40, tool_calls=calls), step(2, "GENERIC", 39),
                 step(3, "PLANNER_RESPONSE", 38, content="see plan", tool_calls=[])]
        self.assertEqual(run_ag(steps, mtime_ago=38)["state"], "waiting_input")

    def test_background_task_keeps_running(self):
        steps = [self.u,
                 step(1, "PLANNER_RESPONSE", 50, tool_calls=[{"name": "run_command", "args": {}}]),
                 step(2, "GENERIC", 49, status="RUNNING", content="Tool is running as a background task with task id: abc/task-2\n"),
                 step(3, "PLANNER_RESPONSE", 48, content="", tool_calls=[])]
        self.assertEqual(run_ag(steps, mtime_ago=48)["state"], "running")
        steps.append(step(4, "SYSTEM_MESSAGE", 10, content='content=Task id "abc/task-2" finished with result'))
        steps.append(step(5, "PLANNER_RESPONSE", 9, content="all done", tool_calls=[]))
        self.assertEqual(run_ag(steps, mtime_ago=9)["state"], "done")

    def test_stalled(self):
        u = step(0, "USER_INPUT", 1000, content="<USER_REQUEST>x</USER_REQUEST>")
        a = run_ag([u, step(1, "SYSTEM_MESSAGE", 700, content="x")], mtime_ago=700)
        self.assertEqual(a["state"], "stalled")

    def test_unhandled_notification_after_turn_end_is_done(self):
        u = step(0, "USER_INPUT", 5000, content="<USER_REQUEST>schedule it</USER_REQUEST>")
        steps = [u, step(1, "PLANNER_RESPONSE", 4990, content="scheduled", tool_calls=[]),
                 step(2, "SYSTEM_MESSAGE", 1500, content="cron fired")]
        a = run_ag(steps, mtime_ago=1500)
        self.assertEqual(a["state"], "done")
        self.assertAlmostEqual(a["since"], NOW - 4990, delta=2)

    def test_project_inference(self):
        calls = [{"name": "view_file", "args": {"Path": "c:\\\\Users\\\\me\\\\Documents\\\\antigravity\\\\kindle\\\\server.py"}},
                 {"name": "view_file", "args": {"Path": "c:\\\\Users\\\\me\\\\Documents\\\\antigravity\\\\kindle\\\\renderer.py"}},
                 {"name": "view_file", "args": {"Path": "C:\\\\Users\\\\me\\\\.gemini\\\\antigravity\\\\brain\\\\x\\\\a.md"}}]
        self.assertEqual(ag.infer_project([step(1, "PLANNER_RESPONSE", 1, tool_calls=calls)]), "kindle")


class StoreAndHubTests(unittest.TestCase):
    def test_store_ack_and_summary(self):
        from aw.store import Store
        with tempfile.TemporaryDirectory() as d:
            s = Store(dict(DEFAULTS), d)
            t1 = Task(tool="dsh", session_id="a", title="A", state="done", state_since=time.time() - 30, last_event_at=time.time() - 30)
            t2 = Task(tool="antigravity", session_id="b", title="B", state="running", state_since=time.time() - 300,
                      last_event_at=time.time(), turn_started_at=time.time() - 300)
            s.set_local([t1, t2], {})
            summ = s.task_summary()
            self.assertEqual([i["state"] for i in summ["items"]], ["running", "done"])
            self.assertEqual(summ["counts"]["done"], 1)
            self.assertTrue(s.ack(t1.key))
            self.assertEqual([i["state"] for i in s.task_summary()["items"]], ["running"])
            # a new turn ending later resurfaces the task
            t1b = Task(tool="dsh", session_id="a", title="A", state="done", state_since=time.time(), last_event_at=time.time())
            s.set_local([t1b, t2], {})
            self.assertEqual(len(s.task_summary()["items"]), 2)

    def test_hub_endpoints_and_security(self):
        from hub_fixture import isolated_hub
        with isolated_hub() as hub:
            from aw.auth import CSRF_HEADER
            hub.CFG["ingest_token"] = "ingest-secret-token"
            hub.CFG["summary_token"] = "summary-readonly-token"

            c = hub.app.test_client()

            # 1. Before setup: business endpoints & summary are blocked (401)
            self.assertEqual(c.get("/api/tasks").status_code, 401)
            self.assertEqual(c.get("/api/settings").status_code, 401)
            self.assertEqual(c.get("/api/summary").status_code, 401)

            # 2. First-time admin password setup
            r = c.post("/api/auth/setup", json={"password": "admin123", "remember": True})
            self.assertEqual(r.status_code, 200)
            csrf = r.get_json()["csrf_token"]
            # Second setup attempt is rejected
            self.assertEqual(c.post("/api/auth/setup", json={"password": "other"}).status_code, 400)

            # 3. Independent collector token (POST /api/ingest) without browser session
            anon = hub.app.test_client()
            self.assertEqual(anon.post("/api/ingest", json={"machine": "laptop", "tasks": []}).status_code, 401)
            r = anon.post("/api/ingest", headers={"Authorization": "Bearer ingest-secret-token"}, json={
                "machine": "laptop",
                "tasks": [{"tool": "codex", "session_id": "r1", "title": "Remote", "state": "waiting_approval",
                           "state_since": time.time() - 5, "last_event_at": time.time()}],
            })
            self.assertEqual(r.status_code, 200)

            # 4. Independent Get read-only summary_token (query param & Bearer header) without browser session
            self.assertEqual(anon.get("/api/summary").status_code, 401)
            self.assertEqual(anon.get("/api/summary?token=wrong").status_code, 401)
            r = anon.get("/api/summary?token=summary-readonly-token")
            self.assertEqual(r.status_code, 200)
            self.assertTrue(any(i["machine"] == "laptop" for i in r.get_json()["items"]))
            tag = r.headers["ETag"]
            h = r.headers["X-Summary-Hash"]
            self.assertEqual(
                anon.get("/api/summary", headers={"Authorization": "Bearer summary-readonly-token", "If-None-Match": tag}).status_code,
                304,
            )
            self.assertEqual(anon.get(f"/api/eink/summary?token=summary-readonly-token&since={h}").status_code, 304)

            # 5. Authenticated admin can read /api/tasks and /api/settings, but mutating calls require CSRF
            self.assertEqual(c.get("/api/tasks").status_code, 200)
            self.assertEqual(c.get("/api/settings").status_code, 200)
            from urllib.parse import quote
            key = quote("laptop/codex/r1", safe="")
            # Missing CSRF -> 403
            self.assertEqual(c.post(f"/api/tasks/{key}/ack").status_code, 403)
            self.assertEqual(c.post("/api/settings", json={"machine": "NewName"}).status_code, 403)
            self.assertEqual(c.delete("/api/machines/laptop").status_code, 403)
            # Valid CSRF -> 200
            self.assertEqual(c.post(f"/api/tasks/{key}/ack", headers={CSRF_HEADER: csrf}).status_code, 200)

            # 6. Password change invalidates old sessions on other clients
            other_client = hub.app.test_client()
            r_login = other_client.post("/api/auth/login", json={"password": "admin123", "remember": False})
            self.assertEqual(r_login.status_code, 200)
            self.assertEqual(other_client.get("/api/tasks").status_code, 200)

            r_pw = c.post("/api/auth/password", headers={CSRF_HEADER: csrf},
                          json={"old_password": "admin123", "new_password": "newpassword456"})
            self.assertEqual(r_pw.status_code, 200)
            new_csrf = r_pw.get_json()["csrf_token"]
            # Old session on other_client is immediately invalidated!
            self.assertEqual(other_client.get("/api/tasks").status_code, 401)
            # Current client with updated cookie still works
            self.assertEqual(c.get("/api/tasks").status_code, 200)

            # 7. Explicit logout revokes session
            self.assertEqual(c.post("/api/auth/logout", headers={CSRF_HEADER: new_csrf}).status_code, 200)
            self.assertEqual(c.get("/api/tasks").status_code, 401)

            # 8. Rate limiting on failed login attempts
            attacker = hub.app.test_client()
            for _ in range(4):
                self.assertEqual(attacker.post("/api/auth/login", json={"password": "wrong"}).status_code, 401)
            # 5th failure triggers 429 lockout
            self.assertEqual(attacker.post("/api/auth/login", json={"password": "wrong"}).status_code, 429)
            # Even with the right password, locked IP is rejected until lockout expires
            self.assertEqual(attacker.post("/api/auth/login", json={"password": "newpassword456"}).status_code, 429)

    def test_offline_retention_and_multi_ip_persistence(self):
        from aw.store import Store
        with tempfile.TemporaryDirectory() as d:
            cfg = dict(DEFAULTS)
            cfg["offline_after_sec"] = 60
            s = Store(cfg, d)
            now = time.time()
            # Machine pushes from LAN IP first, then from WireGuard IP
            s.ingest("macbook", [{"tool": "codex", "session_id": "c1", "title": "Fix auth",
                                  "state": "running", "state_since": now - 100, "last_event_at": now - 10,
                                  "machine": "macbook"}],
                     remote_ip="192.0.2.88", extra_ips=["10.0.0.8"])
            s.ingest("macbook", [{"tool": "codex", "session_id": "c1", "title": "Fix auth",
                                  "state": "done", "state_since": now - 5, "last_event_at": now - 5,
                                  "machine": "macbook"}],
                     remote_ip="10.0.0.8")
            # Simulate machine going offline 10 minutes ago
            s.remote["macbook"]["at"] = now - 600
            s._save_remote()

            # Reload from disk into a fresh Store instance (e.g. Hub restarted while macbook is off)
            s2 = Store(cfg, d)
            views = s2.view(include_idle=True)
            self.assertEqual(len(views), 1)
            v = views[0]
            self.assertEqual(v["state"], "done")
            self.assertFalse(v["machine_online"])
            self.assertEqual(v["machine_offline_age"], "10m")
            self.assertIn("192.0.2.88", v["machine_ips"])
            self.assertIn("10.0.0.8", v["machine_ips"])


if __name__ == "__main__":
    unittest.main(verbosity=1)

class AntigravityExternalInputTests(unittest.TestCase):
    def test_restart_notice_does_not_replace_external_prompt(self):
        envelope='\n<SYSTEM_MESSAGE>\n[Message] timestamp=2026-10-29T13:55:55Z sender=system priority=MESSAGE_PRIORITY_{} content={}\n</SYSTEM_MESSAGE>'
        rows=[step(0,'USER_INPUT',600,content='Original question'),
              step(1,'SYSTEM_MESSAGE',5,content=envelope.format('HIGH','Panel question')),
              step(2,'SYSTEM_MESSAGE',4,content=envelope.format('LOW','[Notice] All your subagents and background tasks have been stopped due to server restart.'))]
        pending=run_ag(rows,mtime_ago=4)
        self.assertEqual(pending['notification'],'Panel question')
        self.assertEqual(pending['reply'],'')
        self.assertAlmostEqual(pending['turn_start'],NOW-5,delta=2)
        rows.append(step(3,'PLANNER_RESPONSE',1,content='Panel answer'))
        done=run_ag(rows,mtime_ago=1)
        self.assertEqual(done['notification'],'Panel question')
        self.assertEqual(done['reply'],'Panel answer')
        self.assertEqual(done['state'],'done')

    def test_notification_is_distinct_and_does_not_show_old_reply_as_new(self):
        content='The following is a <SYSTEM_MESSAGE> not actually sent by the user.\n<SYSTEM_MESSAGE>\n[Message] timestamp=2026-10-29T12:57:16Z sender=system priority=MESSAGE_PRIORITY_HIGH content=New question\nsecond line\n</SYSTEM_MESSAGE>'
        rows=[step(0,'USER_INPUT',600,content='Original question'),step(1,'PLANNER_RESPONSE',590,content='Old reply'),step(2,'SYSTEM_MESSAGE',3,content=content)]
        a=run_ag(rows,mtime_ago=3)
        self.assertEqual(a['prompt'],'Original question')
        self.assertEqual(a['notification'],'New question\nsecond line')
        self.assertEqual(a['reply'],'')
        self.assertEqual(a['state'],'running')
        self.assertAlmostEqual(a['turn_start'],NOW-3,delta=2)
        rows.append(step(3,'PLANNER_RESPONSE',1,content='New reply'))
        self.assertEqual(run_ag(rows,mtime_ago=1)['reply'],'New reply')
        rows.append(step(4,'USER_INPUT',0,content='Back in native app'))
        self.assertEqual(run_ag(rows).get('notification',''),'')

    def test_tool_echo_and_background_notifications_are_not_external_input(self):
        rows=[step(0,'USER_INPUT',600,content='Original'),step(1,'PLANNER_RESPONSE',20,content='Done'),step(2,'SYSTEM_MESSAGE',1,content='Task id "job" finished')]
        a=run_ag(rows,mtime_ago=1)
        self.assertEqual(a['notification'],'')
        self.assertEqual(a['reply'],'Done')
