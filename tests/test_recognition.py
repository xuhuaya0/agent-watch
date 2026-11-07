"""Regression cases for cross-platform paths and task/agent state transitions."""
import copy
import datetime
import json
import os
from pathlib import Path
import tempfile
import time
import unittest

from aw.adapters import antigravity as ag, codex, dsh
from aw.config import DEFAULTS

NOW = 1_800_000_000.0


def iso(ago):
    return datetime.datetime.fromtimestamp(NOW - ago, datetime.timezone.utc).isoformat()


def event(kind, ago, **payload):
    return {"timestamp": iso(ago), "type": "event_msg", "payload": {"type": kind, **payload}}


def item(kind, ago, **payload):
    return {"timestamp": iso(ago), "type": "response_item", "payload": {"type": kind, **payload}}


def step(kind, ago, **payload):
    return {"type": kind, "created_at": iso(ago), "status": "DONE", **payload}


class CodexRecognitionTests(unittest.TestCase):
    def test_response_item_fixture_progression(self):
        path = Path(__file__).parent / "fixtures" / "codex_response_items.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        now = datetime.datetime.fromisoformat("2026-10-27T12:00:06+00:00").timestamp()
        self.assertEqual(codex.analyze(rows[:3], now, DEFAULTS)["prompt"], "Fix the parser")
        self.assertEqual(codex.analyze(rows[:4], now, DEFAULTS)["state"], "waiting_input")
        self.assertEqual(codex.analyze(rows[:5], now, DEFAULTS)["state"], "running")
        final = codex.analyze(rows, now, DEFAULTS)
        self.assertEqual((final["state"], final["detail"]), ("done", "Parser fixed"))

    def test_legacy_user_message(self):
        rows = [event("user_message", 20, message="Legacy request"), event("task_started", 19)]
        self.assertEqual(codex.analyze(rows, NOW, DEFAULTS)["prompt"], "Legacy request")

    def test_approval_clears_only_after_matching_output(self):
        rows = [event("task_started", 60), event("exec_approval_request", 30, call_id="a"),
                item("function_call_output", 10, call_id="unrelated", output="ok")]
        self.assertEqual(codex.analyze(rows, NOW, DEFAULTS)["state"], "waiting_approval")
        rows.append(item("function_call_output", 5, call_id="a", output="ok"))
        self.assertEqual(codex.analyze(rows, NOW, DEFAULTS)["state"], "running")

    def test_independent_approvals(self):
        rows = [event("task_started", 60), event("exec_approval_request", 30, call_id="a"),
                event("exec_approval_request", 20, call_id="b"),
                item("custom_tool_call_output", 10, call_id="a", output="ok")]
        state = codex.analyze(rows, NOW, DEFAULTS)
        self.assertEqual(state["state"], "waiting_approval")
        self.assertEqual(state["since"], NOW - 20)
        rows.append(event("exec_approval_decided", 5, call_id="b"))
        self.assertEqual(codex.analyze(rows, NOW, DEFAULTS)["state"], "running")

    def test_legacy_unkeyed_approval_clears_on_tool_result(self):
        rows = [event("task_started", 60), event("exec_approval_request", 30),
                item("function_call_output", 1, call_id="a", output="ok")]
        self.assertEqual(codex.analyze(rows, NOW, DEFAULTS)["state"], "running")

    def test_new_turn_clears_old_waits_and_detail(self):
        rows = [event("task_started", 60), event("exec_approval_request", 30),
                event("task_complete", 20, last_agent_message="Old result"),
                event("task_started", 1)]
        state = codex.analyze(rows, NOW, DEFAULTS)
        self.assertEqual((state["state"], state["detail"]), ("running", ""))

    def test_pending_tool_uses_longer_stall_threshold(self):
        rows = [event("task_started", 900), item("function_call", 700, name="exec", call_id="a")]
        self.assertEqual(codex.analyze(rows, NOW, DEFAULTS)["state"], "running")
        self.assertEqual(codex.analyze(rows, NOW + 1200, DEFAULTS)["state"], "stalled")

    def test_async_question_does_not_block_turn(self):
        rows = [event("task_started", 30), item("function_call", 1, name="request_user_input_async", call_id="a")]
        self.assertEqual(codex.analyze(rows, NOW, DEFAULTS)["state"], "running")

    def test_partial_tail_preserves_task_and_completed_append_is_seen(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "sessions" / "2026" / "10" / "27" / "rollout-sample.jsonl"
            path.parent.mkdir(parents=True)
            complete = json.dumps(event("task_complete", 1, last_agent_message="Finished"))
            prefix = json.dumps(event("task_started", 10)) + "\n"
            path.write_text(prefix + complete[:20], encoding="utf-8")
            os.utime(path, (NOW, NOW))
            cfg = copy.deepcopy(DEFAULTS)
            cfg["adapters"]["codex"]["root"] = root
            tasks = codex.scan(cfg, NOW)
            self.assertEqual([t.state for t in tasks], ["running"])
            with path.open("a", encoding="utf-8") as f:
                f.write(complete[20:] + "\n")
            os.utime(path, (NOW, NOW))
            self.assertEqual([t.state for t in codex.scan(cfg, NOW)], ["done"])

    def test_malformed_line_does_not_drop_valid_session(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "sessions" / "rollout-sample.jsonl"
            path.parent.mkdir()
            rows = [event("task_started", 10), event("task_complete", 1)]
            path.write_text(json.dumps(rows[0]) + "\ninvalid\n" + json.dumps(rows[1]) + "\n")
            os.utime(path, (NOW, NOW))
            cfg = copy.deepcopy(DEFAULTS)
            cfg["adapters"]["codex"]["root"] = root
            with self.assertLogs("aw.adapters.codex", level="WARNING"):
                tasks = codex.scan(cfg, NOW)
            self.assertEqual([t.state for t in tasks], ["done"])

    def test_windows_cwd_can_be_read_on_any_os(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "sessions" / "rollout-sample.jsonl"
            path.parent.mkdir()
            rows = [{"type": "session_meta", "timestamp": iso(10), "payload": {"cwd": "C:\\Users\\example\\projects\\parser"}}, event("task_started", 5)]
            path.write_text("\n".join(json.dumps(r) for r in rows))
            os.utime(path, (NOW, NOW))
            cfg = copy.deepcopy(DEFAULTS)
            cfg["adapters"]["codex"]["root"] = root
            self.assertEqual(codex.scan(cfg, NOW)[0].project, "parser")


class AntigravityRecognitionTests(unittest.TestCase):
    def background_turn(self, ids):
        return [step("USER_INPUT", 2400, content="Run checks")] + [
            step("GENERIC", 2300, status="RUNNING",
                 content=f"Tool is running as a background task with task id: {tid}\n")
            for tid in ids]

    def test_cancelled_background_results_clear_only_the_named_tasks(self):
        ids=['session/task-1279','session/task-1288','session/task-1292']
        rows=self.background_turn(ids)
        for tid in ids:
            rows += [step("PLANNER_RESPONSE", 2200, tool_calls=[{
                'name':'manage_task','args':{'Action':'"kill"','TaskId':json.dumps(tid)}}]),
                step("GENERIC", 2199, status="DONE", content=(
                    'Created At: 2026-10-29T00:00:16+08:00\n'
                    'Completed At: 2026-10-29T00:00:16+08:00\n'
                    f'Task "{tid}" cancelled.'))]
            final=step("PLANNER_RESPONSE", 2198, content="Checks passed", tool_calls=[])
            expected='done' if tid==ids[-1] else 'stalled'
            result=ag.analyze(rows+[final],NOW,NOW-2198,DEFAULTS)
            self.assertEqual(result['state'],expected)
        self.assertEqual(result['reply'],'Checks passed')
        self.assertEqual(result['since'],NOW-2198)

    def test_background_status_poll_and_unconfirmed_kill_remain_pending(self):
        base=self.background_turn(['job-1'])
        variants=[
            [step('GENERIC',2200,status='DONE',content='Task: job-1\nStatus: RUNNING\nLog output: still working')],
            [step('PLANNER_RESPONSE',2200,tool_calls=[{'name':'manage_task','args':{'Action':'"kill"','TaskId':'"job-1"'}}])],
            [step('GENERIC',2200,status='DONE',content='Failed to cancel task "job-1".')],
            [step('GENERIC',2200,status='DONE',content='Task "other-job" cancelled.')],
        ]
        for middle in variants:
            with self.subTest(middle=middle):
                rows=base+middle+[step('PLANNER_RESPONSE',2198,content='Waiting',tool_calls=[])]
                self.assertEqual(ag.analyze(rows,NOW,NOW-2198,DEFAULTS)['state'],'stalled')

    def test_quoted_log_output_and_failed_result_cannot_cancel_background(self):
        for content,status,error in [
            ('Task: job-1\nStatus: RUNNING\nLog output:\nTask "job-1" cancelled.','DONE',None),
            ('Command output:\nTask "job-1" cancelled.','DONE',None),
            ('Task "job-1" cancelled.','ERROR',None),
            ('Task "job-1" cancelled.','DONE','Tool failed'),
        ]:
            rows=self.background_turn(['job-1'])+[
                step('GENERIC',2200,status=status,error=error,content=content),
                step('PLANNER_RESPONSE',2198,content='Waiting',tool_calls=[])]
            self.assertEqual(ag.analyze(rows,NOW,NOW-2198,DEFAULTS)['state'],'stalled')

    def test_project_paths_on_all_platforms(self):
        for path in (r"C:\Users\example\Documents\project\server.py",
                     "/Users/example/Documents/project/server.py",
                     "/home/example/projects/project/server.py", "/root/projects/project/server.py"):
            with self.subTest(path=path):
                rows = [step("PLANNER_RESPONSE", 1, tool_calls=[{"name": "view_file", "args": {"Path": path}}])]
                self.assertEqual(ag.infer_project(rows), "project")

    def test_pending_background_timeout_is_not_completion(self):
        rows = [step("USER_INPUT", 2200, content="Build"),
                step("PLANNER_RESPONSE", 2100, tool_calls=[{"name": "run_command", "args": {}}]),
                step("GENERIC", 2099, content="Tool is running as a background task with task id: job-1\n"),
                step("PLANNER_RESPONSE", 2098, content="Started", tool_calls=[])]
        self.assertEqual(ag.analyze(rows, NOW - 1000, NOW - 2098, DEFAULTS)["state"], "running")
        self.assertEqual(ag.analyze(rows, NOW, NOW - 2098, DEFAULTS)["state"], "stalled")
        rows += [step("SYSTEM_MESSAGE", 2, content='Task id "job-1" finished'),
                 step("PLANNER_RESPONSE", 1, content="Finished", tool_calls=[])]
        self.assertEqual(ag.analyze(rows, NOW, NOW - 1, DEFAULTS)["state"], "done")


class DshTreeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = copy.deepcopy(DEFAULTS)
        self.cfg["adapters"]["dsh"]["root"] = self.tmp.name
        self.now = time.time()
        dsh._LOGS.clear()

    def ev(self, kind, ago, **data):
        return {"type": kind, "time": int((self.now - ago) * 1000), "data": data}

    def write(self, sid, children=(), tail=()):
        rows = [{"type": "session", "id": sid, "cwd": "/project"}, self.ev("turn/start", 60, turn=1)]
        rows += [self.ev("subagent/catalog", 50, childId=c, label=c) for c in children]
        rows += list(tail)
        path = Path(self.tmp.name) / "sessions" / "project" / sid / "session.v4.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(r) + "\n" for r in rows))

    def test_grandchild_attention_propagates_with_correct_timestamp(self):
        self.write("session-parent", ["child"])
        self.write("child", ["grandchild"])
        self.write("grandchild", tail=[self.ev("approval/asked", 5, id="a", toolName="shell", reason="Permission")])
        tasks = dsh.scan(self.cfg, self.now)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].state, "waiting_approval")
        self.assertAlmostEqual(tasks[0].state_since, self.now - 5, delta=.01)
        self.assertEqual(tasks[0].extra["subagents"][0]["subagents"][0]["state"], "waiting_approval")

    def test_sibling_order_does_not_hide_higher_priority_approval(self):
        self.write("input", tail=[self.ev("tool/call", 10, callId="q", name="ask_user")])
        self.write("approval", tail=[self.ev("approval/asked", 5, id="a", toolName="shell")])
        for children in (["input", "approval"], ["approval", "input"]):
            self.write("session-parent", children)
            dsh._LOGS.clear()
            self.assertEqual(dsh.scan(self.cfg, self.now)[0].state, "waiting_approval")

    def test_child_error_stays_in_details_across_parent_turns(self):
        self.write("child", tail=[self.ev("turn/end", 40, turn=1,
            reason={"kind": "error", "error": {"message": "Synthetic request failed (413)"}})])
        self.write("session-parent", ["child"])
        task, = dsh.scan(self.cfg, self.now)
        self.assertEqual(task.state, "running")
        self.assertEqual(task.extra["subagents"][0]["state"], "error")
        self.assertIn("413", task.extra["subagents"][0]["detail"])
        self.assertAlmostEqual(task.state_since, self.now - 60, delta=.01)
        self.write("session-parent", ["child"], [self.ev("turn/end", 20, turn=1,
            reason={"kind": "completed"})])
        dsh._LOGS.clear()
        self.assertEqual(dsh.scan(self.cfg, self.now)[0].state, "done")
        self.write("session-parent", ["child"], [self.ev("turn/end", 20, turn=1,
            reason={"kind": "completed"}), self.ev("turn/start", 1, turn=2)])
        dsh._LOGS.clear()
        task, = dsh.scan(self.cfg, self.now)
        self.assertEqual(task.state, "running")
        self.assertAlmostEqual(task.state_since, self.now - 1, delta=.01)

    def test_child_stall_does_not_replace_active_parent_but_parent_stall_remains(self):
        self.write("child", tail=[self.ev("turn/start", 2000, turn=2)])
        self.write("session-parent", ["child"])
        task, = dsh.scan(self.cfg, self.now)
        self.assertEqual(task.state, "running")
        self.assertEqual(task.extra["subagents"][0]["state"], "stalled")
        task, = dsh.scan(self.cfg, self.now + 2000)
        self.assertEqual(task.state, "stalled")
        self.assertFalse(task.detail.startswith("子代理"))

    def test_nested_error_is_retained_without_masking_real_parent_error_or_question(self):
        self.write("leaf", tail=[self.ev("turn/end", 10, turn=1,
            reason={"kind": "error", "error": {"message": "Synthetic child failure"}})])
        self.write("middle", ["leaf"])
        self.write("session-parent", ["middle"])
        task, = dsh.scan(self.cfg, self.now)
        self.assertEqual(task.state, "running")
        self.assertEqual(task.extra["subagents"][0]["subagents"][0]["state"], "error")
        self.write("session-parent", ["middle"], [self.ev("turn/end", 1, turn=1,
            reason={"kind": "error", "error": {"message": "Synthetic parent failure"}})])
        dsh._LOGS.clear()
        task, = dsh.scan(self.cfg, self.now)
        self.assertEqual((task.state, task.detail), ("error", "Synthetic parent failure"))
        self.write("session-parent", ["middle"], [self.ev("tool/call", 1, callId="q", name="ask_user")])
        dsh._LOGS.clear()
        self.assertEqual(dsh.scan(self.cfg, self.now)[0].state, "waiting_input")

    def test_cycle_and_missing_child_are_bounded_and_visible(self):
        self.write("session-parent", ["child", "missing"])
        self.write("child", ["session-parent"])
        tasks = dsh.scan(self.cfg, self.now)
        self.assertEqual(len(tasks), 1)
        subs = tasks[0].extra["subagents"]
        self.assertEqual(subs[0]["subagents"][0]["state"], "unknown")
        self.assertEqual(subs[1]["state"], "unknown")

    def test_cancelled_parent_is_not_resurrected_by_old_child(self):
        self.write("session-parent", ["child"], [self.ev("turn/end", 1, turn=1, reason={"kind": "aborted"})])
        self.write("child", tail=[self.ev("approval/asked", 5, id="a", toolName="shell")])
        self.assertEqual(dsh.scan(self.cfg, self.now)[0].state, "idle")


if __name__ == "__main__":
    unittest.main()
