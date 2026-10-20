"""HTTP Mock checks: locally rejected inputs must not spend model budget."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from agent_skill.api_server import create_app
from core.model_gateway import ModelConfig, ModelGateway
from customer_service.store import Store


class TestPreflightBudget(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        configuration = patch.dict(os.environ, {
            "MODEL_ENABLED": "true", "LLM_PROVIDER": "deepseek",
            "DEEPSEEK_API_KEY": "offline-fixture-only",
            "MODEL_MAX_INPUT_TOKENS": "4000", "MODEL_MAX_OUTPUT_TOKENS": "128",
            "SUPPORT_MODEL_MAX_OUTPUT_TOKENS": "32",
            "MODEL_DAILY_CALL_LIMIT": "10", "MODEL_DAILY_TOKEN_LIMIT": "50000",
            "MODEL_DAILY_COST_LIMIT": "", "DEEPSEEK_INPUT_PRICE_PER_MILLION": "",
            "DEEPSEEK_OUTPUT_PRICE_PER_MILLION": "",
        })
        configuration.start()
        self.addCleanup(configuration.stop)
        self.requests = []
        self.selection = {"product_ids": ["p1"], "knowledge_ids": [], "needs_human": False}
        self.gateway = ModelGateway(config=ModelConfig.from_env(),
                                    transport=httpx.MockTransport(self.respond))
        self.store = Store(str(Path(self.directory.name) / "support.db"))
        self.store.upsert_product("p1", name="合成试验杯", description="仅供离线测试", price_cents=12000)
        self.app = create_app(store=self.store, gateway=self.gateway)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        session = self.client.post("/support/conversations").json()
        self.cid = session["id"]
        self.headers = {"X-Session-Token": session["session_token"]}

    def respond(self, request):
        self.requests.append(request)
        selection = json.dumps(self.selection)
        return httpx.Response(200, json={"choices": [{"message": {"content": selection}}],
                                        "usage": {"prompt_tokens": 100, "completion_tokens": 20}})

    def ask(self):
        response = self.client.post(f"/support/conversations/{self.cid}/messages",
                                    headers=self.headers, json={"message": "合成试验杯多少钱？"})
        self.assertEqual(response.status_code, 200)
        return response.json()

    def assert_no_spending(self):
        self.assertEqual(self.requests, [])
        summary = self.app.state.customer_service.budget.summary()
        self.assertEqual((summary["calls"], summary["tokens"]), (0, 0))
        with self.store.connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM model_budget_reservations").fetchone()[0], 0)

    def test_long_product_rejected_before_budget_reservation(self):
        self.store.upsert_product("p1", name="合成试验杯", description="测试说明" * 1500, price_cents=12000)
        result = self.ask()
        self.assertEqual(result["source"], "human_handoff")
        self.assertEqual(result["handoff"]["reason"], "context_too_large")
        self.assert_no_spending()

    def test_long_knowledge_rejected_before_budget_reservation(self):
        self.store.upsert_knowledge("long", title="合成试验杯说明", content="使用说明" * 2000, product_id="p1")
        result = self.ask()
        self.assertEqual(result["source"], "human_handoff")
        self.assertEqual(result["handoff"]["reason"], "context_too_large")
        self.assert_no_spending()

    def test_old_history_is_removed_as_a_whole_to_fit_the_context(self):
        self.store.add_message(self.cid, "user", "合成历史" * 1500, "customer")
        result = self.ask()
        self.assertEqual(result["products"][0]["id"], "p1")
        payload = json.loads(self.requests[0].content)
        packed = json.loads(payload["messages"][1]["content"])
        self.assertEqual(packed["untrusted_history"], [])
        self.assertEqual(len(self.requests), 1)

    def test_low_ranked_documents_are_removed_whole_and_top_policy_is_preserved(self):
        self.store.upsert_knowledge("relevant", title="合成试验杯保修期限", content="保修一年", product_id="p1")
        for doc_id in ("a-noise", "b-noise", "c-noise"):
            self.store.upsert_knowledge(doc_id, title="补充包装资料", content="包装信息" * 400,
                                        product_id="p1")
        self.selection = {"product_ids": [], "knowledge_ids": ["relevant"], "needs_human": False}
        response = self.client.post(f"/support/conversations/{self.cid}/messages", headers=self.headers,
                                    json={"message": "合成试验杯保修政策是什么？"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.json()["citations"]], ["relevant"])
        payload = json.loads(self.requests[0].content)
        packed = json.loads(payload["messages"][1]["content"])
        candidate_ids = {item["id"] for item in packed["untrusted_managed_knowledge"]}
        self.assertIn("relevant", candidate_ids)
        self.assertTrue(candidate_ids.isdisjoint({"a-noise", "b-noise", "c-noise"}))

    def test_customer_service_uses_its_smaller_output_limit_without_mutating_gateway(self):
        result = self.ask()
        self.assertEqual(result["products"][0]["id"], "p1")
        payload = json.loads(self.requests[0].content)
        self.assertEqual(payload["max_tokens"], 32)
        self.assertEqual(self.gateway.config.max_output_tokens, 128)
        self.assertEqual(self.app.state.customer_service.budget.summary()["max_output_tokens"], 32)

    def test_customer_output_limit_never_exceeds_provider_limit(self):
        with patch.dict(os.environ, {"SUPPORT_MODEL_MAX_OUTPUT_TOKENS": "256"}):
            self.ask()
        payload = json.loads(self.requests[0].content)
        self.assertEqual(payload["max_tokens"], 128)
        with self.store.connection() as conn:
            reservation = conn.execute("SELECT output_tokens FROM model_budget_reservations").fetchone()
        self.assertEqual(reservation["output_tokens"], 128)

    def test_invalid_support_output_limit_fails_before_budget_reservation(self):
        with patch.dict(os.environ, {"SUPPORT_MODEL_MAX_OUTPUT_TOKENS": "0"}):
            result = self.ask()
        self.assertEqual(result["error_code"], "configuration")
        self.assert_no_spending()

    def test_valid_request_reserves_then_settles_recorded_usage(self):
        result = self.ask()
        self.assertEqual(result["products"][0]["id"], "p1")
        self.assertEqual(len(self.requests), 1)
        summary = self.app.state.customer_service.budget.summary()
        self.assertEqual((summary["calls"], summary["tokens"]), (1, 120))


if __name__ == "__main__":
    unittest.main()
