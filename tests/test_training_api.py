"""Training HTTP boundaries use explicit fake engines, never a live model."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from agent_skill.api_server import create_app
from core.model_gateway import ModelError
from core.scorer import ScoringError
from customer_service.store import Store


class TrainingHTTPTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"ADMIN_API_TOKEN": "explicit-test-admin-credential"})
        self.env.start()
        self.client = TestClient(create_app(store=Store(str(Path(self.directory.name) / "support.db"))))
        self.headers = {"Authorization": "Bearer explicit-test-admin-credential"}

    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.directory.cleanup()

    def test_training_requires_admin_before_engine_or_data_access(self):
        with patch("agent_skill.api_server.SkillEngine") as engine:
            response = self.client.post("/skill/invoke", json={"action": "recommend", "learner_id": "another"})
        self.assertEqual(response.status_code, 401)
        engine.assert_not_called()

    def test_missing_model_key_is_blocked_not_success(self):
        with patch("agent_skill.api_server.SkillEngine") as engine:
            engine.return_value.start_roleplay.side_effect = ModelError("missing_key")
            response = self.client.post("/skill/invoke", headers=self.headers, json={"action": "roleplay"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["status"], "BLOCKED")

    def test_invalid_score_is_failed_not_success(self):
        with patch("agent_skill.api_server.SkillEngine") as engine:
            engine.return_value.evaluate_text.side_effect = ScoringError()
            response = self.client.post("/skill/invoke", headers=self.headers,
                                        json={"action": "evaluate", "sales_text": "您好"})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["detail"]["code"], "invalid_score")

    def test_training_sessions_have_separate_engines_and_bound_learners(self):
        class FakeEngine:
            def start_roleplay(self, scenario, learner):
                self.learner = learner
                self._current_session = type("Session", (), {"result": None})()
                return "opening"

            def respond(self, message):
                return self.learner + ":" + message, False

        with patch("agent_skill.api_server.SkillEngine", FakeEngine):
            first = self.client.post("/skill/invoke", headers=self.headers,
                json={"action": "roleplay", "learner_id": "first"}).json()["result"]["session_id"]
            second = self.client.post("/skill/invoke", headers=self.headers,
                json={"action": "roleplay", "learner_id": "second"}).json()["result"]["session_id"]
            self.assertNotEqual(first, second)
            self.assertEqual(len(first), 32)
            invalid = self.client.post("/skill/invoke", headers=self.headers,
                json={"action": "roleplay", "session_id": first, "learner_id": "second"})
            self.assertEqual(invalid.status_code, 403)
            reply = self.client.post("/skill/invoke", headers=self.headers,
                json={"action": "roleplay", "session_id": first, "customer_message": "hello"})
            self.assertEqual(reply.json()["result"]["customer_reply"], "first:hello")

    def test_unknown_training_session_does_not_start_new_session(self):
        with patch("agent_skill.api_server.SkillEngine") as engine:
            response = self.client.post("/skill/invoke", headers=self.headers,
                json={"action": "roleplay", "session_id": "nonexistent"})
        self.assertEqual(response.status_code, 404)
        engine.assert_not_called()


if __name__ == "__main__":
    unittest.main()
