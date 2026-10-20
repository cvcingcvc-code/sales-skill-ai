"""Offline HTTP lifecycle, resource, and late-response integration contracts."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from agent_skill.api_server import create_app
from core.model_gateway import ModelError, ModelResult
from customer_service.store import Store


class ExplicitFakeGateway:
    def __init__(self):
        self.calls = 0
        self.disabled = False
        self.hook = None
        self.error = None

    def status(self):
        return {"provider": "mock", "model": "offline-only", "configured": True,
                "status": "DISABLED" if self.disabled else "UNVERIFIED"}

    def chat(self, messages, **kwargs):
        self.calls += 1
        if self.hook:
            self.hook()
        if self.error:
            raise self.error
        data = json.loads(messages[-1]["content"])
        return ModelResult(json.dumps({"product_ids": [p["id"] for p in data["untrusted_managed_products"]],
            "knowledge_ids": [], "needs_human": False}), "mock", "offline-only", 40, 10)


class WorkflowV2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, {"ADMIN_API_TOKEN": "v2-test-admin-only", "MODEL_ENABLED": "true",
            "MODEL_DAILY_CALL_LIMIT": "20", "MODEL_DAILY_TOKEN_LIMIT": "200000", "MODEL_DAILY_COST_LIMIT": "",
            "MODEL_MAX_INPUT_TOKENS": "4000", "MODEL_MAX_OUTPUT_TOKENS": "1000",
            "SUPPORT_CREATE_LIMIT_PER_HOUR": "20", "SUPPORT_MESSAGE_LIMIT_PER_MINUTE": "20"})
        env.start(); self.addCleanup(env.stop)
        self.store = Store(str(Path(self.tmp.name) / "support.db"))
        self.gateway = ExplicitFakeGateway()
        self.app = create_app(store=self.store, gateway=self.gateway)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.admin = {"Authorization": "Bearer v2-test-admin-only"}
        session = self.client.post("/support/conversations").json()
        self.cid, self.visitor = session["id"], {"X-Session-Token": session["session_token"]}
        self.store.upsert_product("sample", name="合成测试水杯", description="仅用于自动化测试", price_cents=10000)

    @property
    def path(self):
        return "/support/conversations/" + self.cid

    def message(self):
        return self.client.post(self.path + "/messages", headers=self.visitor, json={"message": "合成测试水杯多少钱？"})

    def handoff_and_resolve(self):
        task = self.store.create_handoff(self.cid, "customer_request")
        self.store.resolve_handoff(task["id"], "合成人工答复")

    def test_new_administration_operations_require_authorization(self):
        for method, path, body in (
            ("POST", f"/admin/conversations/{self.cid}/close", {}),
            ("POST", f"/admin/conversations/{self.cid}/resume", {}),
            ("DELETE", f"/admin/conversations/{self.cid}", {"confirm": True}),
            ("DELETE", f"/admin/conversations/{self.cid}/lead", {"confirm": True}),
            ("POST", "/admin/privacy/retention/purge", {"days": 30, "confirm": True}),
            ("GET", "/admin/audit", None), ("GET", "/admin/limits", None)):
            with self.subTest(path=path):
                response = self.client.request(method, path, json=body)
                self.assertEqual(response.status_code, 401)

    def test_close_read_then_reject_message_without_model_call(self):
        self.assertEqual(self.client.post(self.path + "/close", headers=self.visitor).status_code, 200)
        self.assertEqual(self.client.get(self.path, headers=self.visitor).json()["status"], "closed")
        self.assertEqual(self.message().status_code, 409)
        self.assertEqual(self.gateway.calls, 0)

    def test_visitors_cannot_close_or_delete_another_conversation(self):
        wrong = {"X-Session-Token": "unrelated"}
        for method, suffix, payload in (("POST", "/close", None), ("DELETE", "", {"confirm": True}),
                                         ("DELETE", "/leads", {"confirm": True})):
            response = self.client.request(method, self.path + suffix, headers=wrong, json=payload)
            self.assertEqual(response.status_code, 403)

    def test_deletion_requires_strict_explicit_confirmation(self):
        for payload in ({}, {"confirm": False}, {"confirm": "true"}, {"confirm": 1}):
            self.assertEqual(self.client.request("DELETE", self.path, headers=self.visitor, json=payload).status_code, 422)
        self.assertEqual(self.client.get(self.path, headers=self.visitor).status_code, 200)

    def test_visitor_delete_revokes_capability_and_keeps_no_messages(self):
        self.client.post(self.path + "/messages", headers=self.visitor, json={"message": "你好"})
        response = self.client.request("DELETE", self.path, headers=self.visitor, json={"confirm": True})
        self.assertTrue(response.json()["deleted"])
        self.assertEqual(self.client.get(self.path, headers=self.visitor).status_code, 403)
        with self.store.connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM messages WHERE conversation_id=?", (self.cid,)).fetchone()[0], 0)

    def test_consent_withdrawal_removes_only_lead(self):
        saved = self.client.post(self.path + "/leads", headers=self.visitor,
            json={"consent": True, "contact": "synthetic@example.invalid", "need": "测试需求"})
        self.assertEqual(saved.status_code, 201)
        removed = self.client.request("DELETE", self.path + "/leads", headers=self.visitor, json={"confirm": True})
        self.assertTrue(removed.json()["consent_withdrawn"])
        self.assertEqual(self.store.list_leads(), [])
        self.assertEqual(self.client.get(self.path, headers=self.visitor).status_code, 200)

    def test_resume_requires_pending_work_to_be_resolved(self):
        handoff = self.store.create_handoff(self.cid, "customer_request")
        path = f"/admin/conversations/{self.cid}/resume"
        self.assertEqual(self.client.post(path, headers=self.admin).status_code, 409)
        self.store.resolve_handoff(handoff["id"], "合成人工答复")
        self.assertEqual(self.client.post(path, headers=self.admin).json()["status"], "ai")
        self.assertEqual(self.message().json()["model_status"], "MOCK")

    def test_disabled_does_not_reserve_or_invoke_model(self):
        self.gateway.disabled = True
        response = self.message()
        self.assertEqual(response.json()["model_status"], "DISABLED")
        self.assertEqual(self.gateway.calls, 0)
        self.assertEqual(self.app.state.customer_service.budget.summary()["calls"], 0)

    def test_budget_limit_prevents_call_and_transfers_to_human(self):
        with patch.dict(os.environ, {"MODEL_DAILY_CALL_LIMIT": "0"}):
            response = self.message()
        self.assertEqual(response.json()["error_code"], "budget_exceeded")
        self.assertIsNotNone(response.json()["handoff"])
        self.assertEqual(self.gateway.calls, 0)

    def test_message_limit_returns_429_and_retry_after(self):
        with patch.dict(os.environ, {"SUPPORT_MESSAGE_LIMIT_PER_MINUTE": "1"}):
            self.assertEqual(self.client.post(self.path + "/messages", headers=self.visitor, json={"message": "你好"}).status_code, 200)
            response = self.message()
        self.assertEqual(response.status_code, 429)
        self.assertGreater(int(response.headers["Retry-After"]), 0)
        self.assertEqual(self.gateway.calls, 0)

    def test_invalid_capability_does_not_consume_message_quota(self):
        with patch.dict(os.environ, {"SUPPORT_MESSAGE_LIMIT_PER_MINUTE": "1"}):
            response = self.client.post(self.path + "/messages", headers={"X-Session-Token": "wrong"}, json={"message": "你好"})
            self.assertEqual(response.status_code, 403)
            self.assertEqual(self.message().status_code, 200)

    def test_create_limit_ignores_spoofed_forwarded_header(self):
        with patch.dict(os.environ, {"SUPPORT_CREATE_LIMIT_PER_HOUR": "1"}):
            response = self.client.post("/support/conversations", headers={"X-Forwarded-For": "198.51.100.1"})
            self.assertEqual(response.status_code, 429)

    def test_retention_preview_is_safe_and_purge_preserves_open_session(self):
        self.store.close_conversation(self.cid)
        old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        with self.store.connection() as conn:
            conn.execute("UPDATE conversations SET last_activity_at=? WHERE id=?", (old, self.cid))
        open_session = self.client.post("/support/conversations").json()
        preview = self.client.post("/admin/privacy/retention/preview", headers=self.admin, json={"days": 30})
        self.assertEqual(preview.json()["eligible_count"], 1)
        self.assertEqual(self.client.get(self.path, headers=self.visitor).status_code, 200)
        self.assertEqual(self.client.post("/admin/privacy/retention/purge", headers=self.admin,
            json={"days": 30, "confirm": False}).status_code, 422)
        purge = self.client.post("/admin/privacy/retention/purge", headers=self.admin, json={"days": 30, "confirm": True})
        self.assertEqual(purge.json()["deleted_count"], 1)
        self.assertEqual(self.store.get_admin_conversation(open_session["id"])["status"], "ai")

    def test_resource_and_audit_metadata_exclude_credentials(self):
        self.store.close_conversation(self.cid)
        audit = self.client.get("/admin/audit", headers=self.admin).json()
        self.assertTrue(audit)
        text = json.dumps(audit)
        self.assertNotIn("v2-test-admin-only", text)
        self.assertNotIn(self.visitor["X-Session-Token"], text)
        limits = self.client.get("/admin/limits", headers=self.admin).json()
        self.assertEqual(limits["model_budget"]["scope"], "customer_service")

    def test_late_success_does_not_survive_takeover_and_resume(self):
        def hook():
            self.handoff_and_resolve(); self.store.resume_ai(self.cid)
        self.gateway.hook = hook
        self.assertEqual(self.message().status_code, 409)
        self.assertEqual(self.store.get_admin_conversation(self.cid)["status"], "ai")
        self.assertFalse(any(m["source"] == "mock_selection" for m in self.store.get_admin_conversation(self.cid)["messages"]))

    def test_late_error_does_not_reopen_resumed_conversation(self):
        def hook():
            self.handoff_and_resolve(); self.store.resume_ai(self.cid)
        self.gateway.hook = hook; self.gateway.error = ModelError("timeout")
        self.assertEqual(self.message().status_code, 409)
        self.assertEqual(self.store.get_admin_conversation(self.cid)["status"], "ai")
        self.assertEqual(self.store.list_handoffs(status="pending"), [])

    def test_resolved_human_queue_is_not_reopened_by_old_reply(self):
        self.gateway.hook = self.handoff_and_resolve
        self.assertEqual(self.message().status_code, 409)
        self.assertEqual(self.store.list_handoffs(status="pending"), [])

    def assert_resolution_after_pending_snapshot_discards_model_return(self, error=None):
        operator = Store(self.store.db_path)
        create_handoff = self.store.create_handoff
        pending = {}

        def takeover_during_inference():
            pending.update(create_handoff(self.cid, "customer_request"))

        def resolve_before_handoff_write(cid, reason, expected_epoch=None):
            # The service has already read human+pending; a separate database
            # connection resolves it before the stale writer takes its lock.
            self.assertEqual((cid, reason), (self.cid, "human_in_progress"))
            self.assertEqual(expected_epoch, pending["conversation_epoch"])
            operator.resolve_handoff(pending["id"], "合成人工答复")
            return create_handoff(cid, reason, expected_epoch=expected_epoch)

        self.gateway.hook = takeover_during_inference
        self.gateway.error = error
        with patch.object(self.store, "create_handoff", side_effect=resolve_before_handoff_write) as writer:
            response = self.message()
        self.assertEqual(writer.call_count, 1)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "conversation_changed")
        self.assertEqual(self.gateway.calls, 1)
        latest = operator.get_admin_conversation(self.cid)
        self.assertEqual(latest["status"], "human")
        self.assertEqual([(h["id"], h["status"]) for h in latest["handoffs"]],
                         [(pending["id"], "resolved")])
        self.assertEqual([m["role"] for m in latest["messages"]], ["user", "human"])

    def test_late_success_cannot_reopen_handoff_resolved_after_pending_snapshot(self):
        self.assert_resolution_after_pending_snapshot_discards_model_return()

    def test_late_error_cannot_reopen_handoff_resolved_after_pending_snapshot(self):
        self.assert_resolution_after_pending_snapshot_discards_model_return(ModelError("timeout"))

    def test_delete_during_model_work_discards_reply_and_preserves_budget(self):
        self.gateway.hook = lambda: self.store.delete_conversation(self.cid)
        self.assertEqual(self.message().status_code, 404)
        summary = self.app.state.customer_service.budget.summary()
        self.assertEqual(summary["calls"], 1)
        self.assertGreater(summary["tokens"], 0)


if __name__ == "__main__":
    unittest.main()
