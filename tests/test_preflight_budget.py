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
            "MODEL_DAILY_CALL_LIMIT": "10", "MODEL_DAILY_TOKEN_LIMIT": "50000",
            "MODEL_DAILY_COST_LIMIT": "", "DEEPSEEK_INPUT_PRICE_PER_MILLION": "",
            "DEEPSEEK_OUTPUT_PRICE_PER_MILLION": "",
        })
        configuration.start()
        self.addCleanup(configuration.stop)
        self.requests = []
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
        selection = json.dumps({"product_ids": ["p1"], "knowledge_ids": [], "needs_human": False})
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
        self.assertEqual(result["error_code"], "invalid_request")
        self.assertEqual(result["source"], "human_handoff")
        self.assert_no_spending()

    def test_long_knowledge_rejected_before_budget_reservation(self):
        self.store.upsert_knowledge("long", title="合成试验杯说明", content="使用说明" * 2000, product_id="p1")
        result = self.ask()
        self.assertEqual(result["error_code"], "invalid_request")
        self.assert_no_spending()

    def test_long_history_rejected_before_budget_reservation(self):
        self.store.add_message(self.cid, "user", "合成历史" * 1500, "customer")
        result = self.ask()
        self.assertEqual(result["error_code"], "invalid_request")
        self.assert_no_spending()

    def test_valid_request_reserves_then_settles_recorded_usage(self):
        result = self.ask()
        self.assertEqual(result["products"][0]["id"], "p1")
        self.assertEqual(len(self.requests), 1)
        summary = self.app.state.customer_service.budget.summary()
        self.assertEqual((summary["calls"], summary["tokens"]), (1, 120))


if __name__ == "__main__":
    unittest.main()
