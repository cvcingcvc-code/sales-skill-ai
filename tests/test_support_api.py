"""HTTP access controls and the complete support handoff workflow (offline mocks).

These are contract/integration tests, not evidence of a live model request.
"""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from agent_skill.api_server import create_app
from customer_service.store import Store
from core.model_gateway import ModelConfig, ModelError, ModelGateway, ModelResult


class FakeGateway:
    """Return structured selections; customer-facing facts still come from Store."""

    def __init__(self):
        self.calls = []
        self.product_ids = []
        self.knowledge_ids = []

    def chat(self, messages, **kwargs):
        self.calls.append(messages)
        return ModelResult(
            text=json.dumps({
                "reply": "请参考已审核的商品和政策。",
                "product_ids": self.product_ids,
                "knowledge_ids": self.knowledge_ids,
                "needs_human": False,
            }, ensure_ascii=False),
            provider="mock", model="offline-api-test", prompt_tokens=11,
            completion_tokens=7, estimated_cost=None,
        )

    def status(self):
        return {"provider": "mock", "model": "offline-api-test", "status": "configured", "configured": True}


class TestSupportAPI(unittest.TestCase):
    ADMIN_TOKEN = "offline-admin-token-only"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.environment = patch.dict(os.environ, {"ADMIN_API_TOKEN": self.ADMIN_TOKEN})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        memory_path = patch("core.memory.DB_PATH", str(Path(self.tmp.name) / "learners.db"))
        memory_path.start()
        self.addCleanup(memory_path.stop)
        self.store = Store(str(Path(self.tmp.name) / "support.db"))
        self.gateway = FakeGateway()
        self.client = TestClient(create_app(store=self.store, gateway=self.gateway))
        self.addCleanup(self.client.close)
        self.admin = {"Authorization": f"Bearer {self.ADMIN_TOKEN}"}

    def conversation(self):
        response = self.client.post("/support/conversations")
        self.assertEqual(response.status_code, 201, response.text)
        data = response.json()
        self.assertTrue(data["id"])
        self.assertTrue(data["session_token"])
        self.assertTrue(data["privacy_notice"])
        return data, {"X-Session-Token": data["session_token"]}

    def product(self, active=True, name="测试保温杯"):
        response = self.client.post("/admin/products", headers=self.admin, json={
            "name": name, "description": "500ml 食品级保温杯，适合办公使用", "price_cents": 12900,
            "url": "https://shop.example.invalid/cup", "active": active,
        })
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def knowledge(self, product_id):
        response = self.client.post("/admin/knowledge", headers=self.admin, json={
            "title": "保温杯售后政策", "content": "收到商品七日内可联系客服申请退货，需保持完好。",
            "kind": "policy", "product_id": product_id, "active": True,
        })
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_public_health_scenarios_and_chat_page(self):
        health = self.client.get("/skill/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["status"], "ok")
        scenarios = self.client.get("/skill/scenarios")
        self.assertEqual(scenarios.status_code, 200)
        self.assertTrue(scenarios.json()["scenarios"])
        page = self.client.get("/chat")
        self.assertEqual(page.status_code, 200)
        self.assertIn("text/html", page.headers["content-type"])
        self.assertNotIn(self.ADMIN_TOKEN, page.text)

    def test_all_admin_routes_require_admin_token(self):
        for endpoint in ("products", "knowledge", "conversations", "handoffs", "leads", "model/status", "model/usage"):
            with self.subTest(endpoint=endpoint):
                response = self.client.get(f"/admin/{endpoint}")
                self.assertIn(response.status_code, (401, 403))
        response = self.client.post("/skill/invoke", json={"action": "recommend"})
        self.assertIn(response.status_code, (401, 403))

    def test_wrong_admin_token_is_rejected(self):
        response = self.client.get("/admin/products", headers={"Authorization": "Bearer incorrect"})
        self.assertIn(response.status_code, (401, 403))

    def test_unconfigured_admin_is_blocked(self):
        with patch.dict(os.environ, {"ADMIN_API_TOKEN": ""}):
            client = TestClient(create_app(store=self.store, gateway=self.gateway))
            try:
                response = client.get("/admin/products", headers=self.admin)
                self.assertEqual(response.status_code, 503)
                self.assertIn("BLOCKED", response.text)
            finally:
                client.close()

    def test_session_token_cannot_grant_admin_access(self):
        conversation, headers = self.conversation()
        response = self.client.get("/admin/conversations", headers=headers)
        self.assertIn(response.status_code, (401, 403))
        response = self.client.get("/admin/leads", headers={"Authorization": f"Bearer {conversation['session_token']}"})
        self.assertIn(response.status_code, (401, 403))

    def test_conversations_have_distinct_unpredictable_credentials(self):
        first, _ = self.conversation()
        second, _ = self.conversation()
        self.assertNotEqual(first["id"], second["id"])
        self.assertNotEqual(first["session_token"], second["session_token"])
        self.assertGreaterEqual(len(first["session_token"]), 32)

    def test_conversation_read_requires_own_token(self):
        first, headers = self.conversation()
        second, other = self.conversation()
        path = f"/support/conversations/{first['id']}"
        self.assertIn(self.client.get(path).status_code, (401, 403))
        self.assertIn(self.client.get(path, headers=other).status_code, (401, 403))
        self.assertEqual(self.client.get(path, headers=headers).status_code, 200)
        self.assertIn(self.client.get(path, headers=self.admin).status_code, (401, 403))

    def test_cross_session_mutations_are_rejected(self):
        first, _ = self.conversation()
        _, other = self.conversation()
        for suffix, body in (
            ("messages", {"message": "查询商品"}),
            ("leads", {"consent": True, "name": "合成用户", "contact": "synthetic@example.invalid", "need": "咨询"}),
            ("handoff", {"reason": "人工帮助"}),
        ):
            with self.subTest(suffix=suffix):
                response = self.client.post(f"/support/conversations/{first['id']}/{suffix}", headers=other, json=body)
                self.assertIn(response.status_code, (401, 403))

    def test_catalog_excludes_unpublished_products(self):
        active = self.product()
        self.product(active=False, name="未上架内部商品")
        response = self.client.get("/support/products")
        self.assertEqual(response.status_code, 200)
        products = response.json()
        self.assertEqual([p["id"] for p in products], [active["id"]])
        admin_products = self.client.get("/admin/products", headers=self.admin).json()
        self.assertEqual(len(admin_products), 2)

    def test_empty_message_is_rejected_without_model_call(self):
        conversation, headers = self.conversation()
        response = self.client.post(f"/support/conversations/{conversation['id']}/messages", headers=headers, json={"message": ""})
        self.assertIn(response.status_code, (400, 422))
        self.assertEqual(self.gateway.calls, [])

    def test_contact_requires_explicit_consent(self):
        conversation, headers = self.conversation()
        response = self.client.post(f"/support/conversations/{conversation['id']}/leads", headers=headers, json={
            "consent": False, "name": "合成用户", "contact": "synthetic@example.invalid", "need": "保温杯",
        })
        self.assertIn(response.status_code, (400, 422))
        leads = self.client.get("/admin/leads", headers=self.admin).json()
        self.assertEqual(leads, [])

    def test_full_support_lead_and_human_reply_workflow(self):
        product = self.product()
        knowledge = self.knowledge(product["id"])
        self.gateway.product_ids = [product["id"]]
        self.gateway.knowledge_ids = [knowledge["id"]]
        conversation, headers = self.conversation()
        path = f"/support/conversations/{conversation['id']}"
        response = self.client.post(f"{path}/messages", headers=headers, json={"message": "想买保温杯，价格多少，有什么售后政策？"})
        self.assertEqual(response.status_code, 200, response.text)
        answer = response.json()
        self.assertTrue(answer["reply"])
        self.assertIn(product["id"], [p["id"] for p in answer["products"]])
        self.assertIn(knowledge["id"], [k["id"] for k in answer["citations"]])
        self.assertEqual(len(self.gateway.calls), 1)
        self.assertEqual(answer["source"], "mock_selection")
        self.assertEqual(answer["model_status"], "MOCK")
        self.assertIn("129.00", answer["reply"])
        lead = self.client.post(f"{path}/leads", headers=headers, json={
            "consent": True, "name": "合成验收用户", "contact": "acceptance@example.invalid", "need": "购买保温杯",
        })
        self.assertEqual(lead.status_code, 201, lead.text)
        leads = self.client.get("/admin/leads", headers=self.admin).json()
        self.assertEqual(len(leads), 1)
        self.assertEqual(leads[0]["contact"], "acceptance@example.invalid")
        handoff = self.client.post(f"{path}/handoff", headers=headers, json={"reason": "需要人工确认批量采购"})
        self.assertEqual(handoff.status_code, 200, handoff.text)
        queue = self.client.get("/admin/handoffs", headers=self.admin).json()
        self.assertEqual(len(queue), 1)
        human_reply = "您好，我是人工客服，请告知所需数量，我会为您核实。"
        resolution = self.client.post(f"/admin/handoffs/{queue[0]['id']}/resolve", headers=self.admin, json={"reply": human_reply})
        self.assertEqual(resolution.status_code, 200, resolution.text)
        self.assertEqual(resolution.json()["status"], "resolved")
        self.assertEqual(self.client.get("/admin/handoffs", headers=self.admin, params={"status": "pending"}).json(), [])
        history = self.client.get(path, headers=headers)
        self.assertEqual(history.status_code, 200)
        self.assertIn(human_reply, json.dumps(history.json(), ensure_ascii=False))
        self.assertNotIn(conversation["session_token"], history.text)
        admin_history = self.client.get(f"/admin/conversations/{conversation['id']}", headers=self.admin)
        self.assertEqual(admin_history.status_code, 200)
        self.assertIn(human_reply, admin_history.text)

    def test_admin_writes_and_detail_require_authorization(self):
        conversation, _ = self.conversation()
        self.assertIn(self.client.get(f"/admin/conversations/{conversation['id']}").status_code, (401, 403))
        requests = (
            ("/admin/products", {"name": "合成商品", "description": "测试", "price_cents": 100}),
            ("/admin/knowledge", {"title": "测试知识", "content": "合成内容"}),
            ("/admin/handoffs/nonexistent/resolve", {"reply": "未经授权不能回复"}),
        )
        for path, body in requests:
            with self.subTest(path=path):
                self.assertIn(self.client.post(path, json=body).status_code, (401, 403))
        self.assertEqual(self.store.list_products(), [])
        self.assertEqual(self.store.list_knowledge(), [])

    def test_invalid_product_prices_and_links_are_rejected(self):
        for invalid in ({"price_cents": -1}, {"price_cents": 1.5}, {"url": "javascript:alert(1)"}, {"url": "https://user:secret@example.invalid"}):
            body = {"name": "合成商品", "description": "测试", "price_cents": 100, **invalid}
            with self.subTest(invalid=invalid):
                response = self.client.post("/admin/products", headers=self.admin, json=body)
                self.assertIn(response.status_code, (400, 422))
        self.assertEqual(self.store.list_products(), [])

    def test_model_failure_is_blocked_and_queues_human(self):
        self.product()
        conversation, headers = self.conversation()
        with patch.object(self.gateway, "chat", side_effect=ModelError("timeout")):
            response = self.client.post(f"/support/conversations/{conversation['id']}/messages", headers=headers,
                                        json={"message": "请介绍保温杯"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["model_status"], "BLOCKED")
        self.assertEqual(response.json()["source"], "human_handoff")
        self.assertEqual(response.json()["error_code"], "timeout")
        queue = self.client.get("/admin/handoffs", headers=self.admin).json()
        self.assertEqual(len(queue), 1)
        usage = self.client.get("/admin/model/usage", headers=self.admin).json()
        self.assertEqual(usage["blocked_calls"], 1)

    def test_upstream_exception_details_are_not_returned(self):
        self.product()
        conversation, headers = self.conversation()
        with patch.object(self.gateway, "chat", side_effect=RuntimeError("private-upstream-auth-header")):
            response = self.client.post(f"/support/conversations/{conversation['id']}/messages", headers=headers,
                                        json={"message": "保温杯适合办公吗"})
        self.assertEqual(response.json()["model_status"], "BLOCKED")
        self.assertNotIn("private-upstream", response.text)
        history = self.client.get(f"/support/conversations/{conversation['id']}", headers=headers)
        self.assertNotIn("private-upstream", history.text)

    def test_non_boolean_consent_is_not_accepted(self):
        conversation, headers = self.conversation()
        response = self.client.post(f"/support/conversations/{conversation['id']}/leads", headers=headers,
                                    json={"consent": "true", "contact": "synthetic@example.invalid"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.store.list_leads(), [])

    def test_admin_token_not_exposed_in_model_status_or_usage(self):
        for endpoint in ("status", "usage"):
            response = self.client.get(f"/admin/model/{endpoint}", headers=self.admin)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertNotIn(self.ADMIN_TOKEN, response.text)
            self.assertNotIn("api_key", response.text.lower())

    def test_admin_training_recommendation_remains_available(self):
        response = self.client.post("/skill/invoke", headers=self.admin, json={"action": "recommend", "learner_id": "api-offline-test"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["success"])


class TestLiveAcceptanceReport(unittest.TestCase):
    """Exercise report branches using an explicit HTTP mock, never a live request."""

    def gateway(self, handler):
        config = ModelConfig(provider="deepseek", model="offline-test-model",
                             base_url="https://api.deepseek.com/v1", api_key="offline-fixture-only")
        return ModelGateway(config=config, transport=httpx.MockTransport(handler))

    def test_missing_key_is_blocked_without_http(self):
        from scripts.verify_live import verify
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}), patch("httpx.Client.post", side_effect=AssertionError("HTTP forbidden")):
            report, code = verify("deepseek")
        self.assertEqual(code, 2)
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["reason"], "missing_key")
        self.assertNotIn("PASS", report["checks"].values())

    def test_connection_failure_is_blocked_and_sanitized(self):
        from scripts.verify_live import verify

        def fail(request):
            raise httpx.ConnectError("private upstream data must never be reported", request=request)

        with patch("scripts.verify_live.ModelGateway", return_value=self.gateway(fail)):
            report, code = verify("deepseek")
        self.assertEqual(code, 2)
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["reason"], "unavailable")
        self.assertNotIn("private upstream", json.dumps(report))

    def test_timeout_is_blocked(self):
        from scripts.verify_live import verify

        def timeout(request):
            raise httpx.ReadTimeout("private timeout body", request=request)

        with patch("scripts.verify_live.ModelGateway", return_value=self.gateway(timeout)):
            report, code = verify("deepseek")
        self.assertEqual((code, report["status"], report["reason"]), (2, "BLOCKED", "timeout"))
        self.assertNotIn("private timeout", json.dumps(report))

    def test_service_unavailable_is_blocked(self):
        from scripts.verify_live import verify
        gateway = self.gateway(lambda request: httpx.Response(503, text="private outage body"))
        with patch("scripts.verify_live.ModelGateway", return_value=gateway):
            report, code = verify("deepseek")
        self.assertEqual((code, report["status"], report["reason"]), (2, "BLOCKED", "unavailable"))
        self.assertNotIn("private outage", json.dumps(report))

    def test_successful_http_with_invalid_selection_does_not_pass_acceptance(self):
        from scripts.verify_live import verify
        gateway = self.gateway(lambda request: httpx.Response(200, json={
            "choices": [{"message": {"content": "unsupported freeform answer"}}]}))
        with patch("scripts.verify_live.ModelGateway", return_value=gateway):
            report, code = verify("deepseek")
        self.assertEqual((code, report["status"]), (1, "FAIL"))
        self.assertEqual(report["checks"]["real_model_request"], "PASS")
        self.assertEqual(report["checks"]["managed_product_reply"], "FAIL")

    def test_authentication_failure_is_blocked_and_sanitized(self):
        from scripts.verify_live import verify
        gateway = self.gateway(lambda request: httpx.Response(401, json={"error": "private upstream auth body"}))
        with patch("scripts.verify_live.ModelGateway", return_value=gateway):
            report, code = verify("deepseek")
        self.assertEqual((code, report["status"], report["reason"]), (2, "BLOCKED", "authentication"))
        self.assertNotIn("private upstream", json.dumps(report))

    def test_success_report_requires_complete_synthetic_workflow(self):
        from scripts.verify_live import verify
        calls = []

        def respond(request):
            calls.append(request)
            content = json.dumps({"product_ids": ["synthetic-acceptance-cup"],
                                  "knowledge_ids": ["synthetic-acceptance-policy"], "needs_human": False})
            return httpx.Response(200, json={"choices": [{"message": {"content": content}}],
                                             "usage": {"prompt_tokens": 10, "completion_tokens": 20}})

        with patch("scripts.verify_live.ModelGateway", return_value=self.gateway(respond)):
            report, code = verify("deepseek")
        self.assertEqual((code, report["status"]), (0, "PASS"))
        self.assertEqual(len(calls), 1)
        self.assertEqual(report["target"], {"host": "api.deepseek.com", "model": "offline-test-model"})
        self.assertTrue(report["synthetic_data_only"])
        self.assertEqual(set(report["checks"].values()), {"PASS"})
        self.assertNotIn("offline-fixture-only", json.dumps(report))
        self.assertNotIn("session_token", json.dumps(report))


if __name__ == "__main__":
    unittest.main()
