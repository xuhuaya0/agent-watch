"""Exercise identity from settings through transport, persistence and UI APIs."""
import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock
import uuid
from concurrent.futures import ThreadPoolExecutor

from aw.config import DEFAULTS, load_config, save_config
from aw.model import Task
from aw.pusher import MultiIpPusher
from aw.scanner import scan_all
from aw.store import Store

A = "00000000-0000-4000-8000-000000000001"
B = "00000000-0000-4000-8000-000000000002"


def task(state="running", since=None):
    now = time.time()
    return {"tool": "codex", "session_id": "same-session", "title": "Example", "state": state,
            "state_since": since or now, "last_event_at": now, "machine": "forged-name", "machine_id": B}


class ConfigIdentityTests(unittest.TestCase):
    def test_persistent_unique_ids_without_changing_config(self):
        with tempfile.TemporaryDirectory() as root:
            p = str(Path(root) / "a" / "config.json")
            q = str(Path(root) / "b" / "config.json")
            a = load_config(p)
            self.assertEqual(a["machine_id"], load_config(p)["machine_id"])
            self.assertEqual(str(uuid.UUID(a["machine_id"])), a["machine_id"])
            self.assertNotEqual(a["machine_id"], load_config(q)["machine_id"])
            self.assertFalse(Path(p).exists())
            a["machine"] = "renamed"
            save_config(a, p)
            self.assertEqual(load_config(p)["machine_id"], a["machine_id"])

    def test_invalid_persisted_id_fails_instead_of_silently_reidentifying(self):
        with tempfile.TemporaryDirectory() as root:
            p = str(Path(root) / "config.json")
            Path(p + ".machine-id").write_text("corrupt")
            with self.assertRaises(ValueError):
                load_config(p)

    def test_concurrent_startup_uses_one_installation_id(self):
        with tempfile.TemporaryDirectory() as root:
            p = str(Path(root) / "config.json")
            with ThreadPoolExecutor(max_workers=8) as pool:
                ids = list(pool.map(lambda _: load_config(p)["machine_id"], range(16)))
            self.assertEqual(len(set(ids)), 1)

    def test_scanner_attaches_installation_id(self):
        cfg = copy.deepcopy(DEFAULTS)
        cfg["machine_id"] = A
        cfg["adapters"]["dsh"]["enabled"] = False
        t = Task.from_dict(task())
        with patch.dict("aw.scanner.ADAPTERS", {"antigravity": lambda cfg, now: [t]}, clear=True):
            tasks, errors = scan_all(cfg)
        self.assertFalse(errors)
        self.assertEqual(tasks[0].machine_id, A)

    def test_transport_keeps_identity_when_failing_over(self):
        response = MagicMock()
        response.__enter__.return_value.status = 200
        with patch("aw.pusher.local_ips", return_value=["192.168.1.2"]), \
             patch("aw.pusher.urllib.request.urlopen", side_effect=[OSError("offline"), response]) as send:
            ok, selected = MultiIpPusher().push(["http://lan", "http://vpn"], "token", "same-host",
                                               [Task.from_dict(task())], machine_id=A)
        self.assertTrue(ok)
        self.assertEqual(selected, "http://vpn")
        for call in send.call_args_list:
            body = json.loads(call.args[0].data)
            self.assertEqual(body["machine_id"], A)
            self.assertEqual(body["machine"], "same-host")


class StoreIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = copy.deepcopy(DEFAULTS)
        self.store = Store(self.cfg, self.tmp.name)

    def test_same_named_devices_do_not_overwrite_each_other(self):
        self.store.ingest("same-host", [task()], machine_id=A, remote_ip="192.168.1.10")
        self.store.ingest("same-host", [task()], machine_id=B, remote_ip="192.168.1.11")
        views = self.store.view()
        self.assertEqual(len(views), 2)
        self.assertEqual(len({t["key"] for t in views}), 2)
        self.assertEqual({t["machine"] for t in views}, {"same-host"})
        self.assertEqual(set(self.store.machines_status()), {"id:" + A, "id:" + B})

    def test_rename_ip_change_reload_preserve_key_and_ack(self):
        payload = task("done", time.time() - 30)
        self.store.ingest("old-name", [payload], machine_id=A, remote_ip="192.168.1.10")
        key = self.store.all_tasks()[0].key
        self.assertTrue(self.store.ack(key))
        self.store.ingest("new-name", [payload], machine_id=A, remote_ip="10.0.0.10")
        reloaded = Store(self.cfg, self.tmp.name)
        views = reloaded.view()
        self.assertEqual(len(views), 1)
        self.assertEqual(views[0]["key"], key)
        self.assertEqual(views[0]["machine"], "new-name")
        self.assertEqual(views[0]["machine_id"], A)
        self.assertTrue(views[0]["acked"])
        self.assertEqual(set(views[0]["machine_ips"]), {"192.168.1.10", "10.0.0.10"})
        self.assertTrue(reloaded.remove_machine("id:" + A))
        self.assertEqual(reloaded.view(), [])

    def test_legacy_disk_record_and_new_device_are_kept_separate(self):
        # Pre-upgrade JSON has neither a record-level display name nor an ID.
        legacy = task()
        legacy.pop("machine_id")
        legacy["machine"] = "same-host"
        Path(self.store.remote_file).write_text(json.dumps({"same-host": {"at": time.time(), "ips": [], "tasks": [legacy]}}))
        s = Store(self.cfg, self.tmp.name)
        s.ingest("same-host", [task()], machine_id=A)
        self.assertEqual(len(s.all_tasks()), 2)
        self.assertIn("same-host/codex/same-session", {t.key for t in s.all_tasks()})
        self.assertTrue(s.remove_machine("same-host"))
        self.assertEqual(s.all_tasks()[0].machine_id, A)

    def test_local_and_remote_same_display_name_do_not_collide(self):
        self.cfg.update(machine="same-host", machine_id=A)
        self.store.set_local([Task.from_dict({**task(), "machine": "same-host"})], {})
        self.store.ingest("same-host", [task()], machine_id=B)
        self.assertEqual(len(self.store.all_tasks()), 2)
        self.store.ingest("same-host", [task()], machine_id=A)
        self.assertEqual(len(self.store.all_tasks()), 2)  # self-push is deduplicated
        self.assertTrue(self.store.machines_status()["id:" + A]["local"])

    def test_invalid_snapshot_does_not_erase_previous_tasks(self):
        self.store.ingest("host", [task()], machine_id=A)
        with self.assertRaises(TypeError):
            self.store.ingest("host", [{}], machine_id=A)
        self.assertEqual(len(self.store.all_tasks()), 1)

    def test_task_level_identity_cannot_override_envelope(self):
        self.store.ingest("host", [task()], machine_id=A)
        t = self.store.all_tasks()[0]
        self.assertEqual((t.machine, t.machine_id), ("host", A))


class HubIdentityTests(unittest.TestCase):
    def setUp(self):
        from hub_fixture import isolated_hub
        hub = self.enterContext(isolated_hub())
        from aw.auth import AuthManager
        self.hub = hub
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        cfg = copy.deepcopy(DEFAULTS)
        cfg["ingest_token"] = "secret"
        cfg["summary_token"] = "readonly-secret"
        auth = AuthManager(self.tmp.name, cfg)
        auth.set_password("test-admin-password")
        self.store = Store(cfg, self.tmp.name)
        for name, value in (("CFG", cfg), ("STORE", self.store), ("AUTH", auth)):
            patcher = patch.object(hub, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = hub.app.test_client()
        login = self.client.post("/api/auth/login", json={"password": "test-admin-password"})
        self.assertEqual(login.status_code, 200)
        self.csrf_headers = {"X-CSRF-Token": login.get_json()["csrf_token"]}

    def post(self, data):
        return self.client.post("/api/ingest", headers={"Authorization": "Bearer secret"}, json=data)

    def test_id_flows_through_ingest_summary_ack_and_delete(self):
        for mid in (A, B):
            response = self.post({"machine": "same-host", "machine_id": mid, "tasks": [task("done")]})
            self.assertEqual(response.status_code, 200)
        summary = self.client.get("/api/summary").get_json()
        self.assertEqual(len(summary["machines"]), 2)
        self.assertEqual({t["machine_id"] for t in summary["items"]}, {A, B})
        key = next(t["key"] for t in summary["items"] if t["machine_id"] == A)
        from urllib.parse import quote
        self.assertEqual(self.client.post("/api/tasks/" + quote(key, safe="") + "/ack", headers=self.csrf_headers).status_code, 200)
        remaining = self.client.get("/api/summary").get_json()["items"]
        self.assertEqual([t["machine_id"] for t in remaining], [B])
        self.assertEqual(self.client.delete("/api/machines/" + quote("id:" + A, safe=""), headers=self.csrf_headers).status_code, 200)
        self.assertEqual(set(self.store.machines_status()), {"id:" + B})

    def test_malformed_identity_and_payload_are_rejected(self):
        for data in ([], ["bad"], {"machine_id": "bad", "tasks": []}, {"tasks": None}, {"tasks": [1]}, {"tasks": [{}]}):
            with self.subTest(data=data):
                self.assertEqual(self.post(data).status_code, 400)
        self.assertFalse(self.store.remote)

    def test_legacy_clients_still_work(self):
        self.assertEqual(self.post({"machine": "legacy", "tasks": [task()]}).status_code, 200)
        t = self.store.all_tasks()[0]
        self.assertEqual(t.key, "legacy/codex/same-session")
        self.assertEqual(t.machine_id, "")


if __name__ == "__main__":
    unittest.main()
