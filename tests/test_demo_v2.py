"""The demonstration must never turn an intentionally disabled model into PASS."""

import os
import unittest
from unittest.mock import patch

from scripts.demo_workflow import run


class DemoV2Tests(unittest.TestCase):
    def test_explicit_mock_completes_lifecycle_without_live_acceptance(self):
        report, code = run(mock=True)
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "MOCK_DEMO_COMPLETE")
        self.assertEqual(report["model_status"], "MOCK")
        for check in ("explicit_ai_resume", "consent_withdrawal", "close_and_delete"):
            self.assertEqual(report["checks"][check], "PASS")

    def test_disabled_model_is_not_a_successful_model_demo(self):
        with patch.dict(os.environ, {"MODEL_ENABLED": "false", "LLM_PROVIDER": "ollama", "OLLAMA_MODEL": ""}):
            report, code = run(mock=False)
        self.assertEqual(code, 2)
        self.assertEqual(report["status"], "DISABLED")
        self.assertEqual(report["model_status"], "DISABLED")
        self.assertTrue(all(value == "PASS" for value in report["checks"].values()))


if __name__ == "__main__":
    unittest.main()
