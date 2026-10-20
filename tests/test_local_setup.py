"""Local preflight remains offline and never emits supplied credentials."""

import contextlib
import importlib.metadata
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import check_local_setup as checker


class LocalSetupTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.env = {"MODEL_ENABLED": "false", "LLM_PROVIDER": "ollama",
                    "OLLAMA_MODEL": "", "ADMIN_API_TOKEN": "private-test-admin-token"}
        self.packages = patch.object(checker.importlib.metadata, "version", return_value="1.0")
        self.packages.start()
        self.addCleanup(self.packages.stop)

    def inspect(self, **updates):
        return checker.inspect_setup({**self.env, **updates}, self.root)

    def test_disabled_local_setup_needs_no_model_or_cloud_key(self):
        report = self.inspect()
        self.assertEqual(report["status"], "OFFLINE_READY")
        self.assertEqual(report["model_execution"], "DISABLED")
        self.assertEqual(report["model_verification"], "NOT_RUN")
        self.assertNotIn("MODEL_READY", json.dumps(report))
        self.assertFalse((self.root / "data").exists())

    def test_enabled_model_needs_tag_without_claiming_availability(self):
        self.assertEqual(self.inspect(MODEL_ENABLED="true")["status"], "CONFIGURATION_REQUIRED")
        report = self.inspect(MODEL_ENABLED="true", OLLAMA_MODEL="private-model-tag")
        self.assertEqual(report["status"], "OFFLINE_READY")
        self.assertEqual(report["model_execution"], "ENABLED_NOT_VERIFIED")
        self.assertNotIn("private-model-tag", json.dumps(report))

    def test_missing_explicit_switch_does_not_suggest_safe_offline_default(self):
        self.assertEqual(self.inspect(MODEL_ENABLED="")["status"], "CONFIGURATION_REQUIRED")
        self.assertEqual(self.inspect(MODEL_ENABLED="yes")["model_execution"], "INVALID")

    def test_missing_admin_reports_presence_only(self):
        report = self.inspect(ADMIN_API_TOKEN="")
        self.assertEqual(report["status"], "CONFIGURATION_REQUIRED")
        self.assertFalse(report["checks"]["admin_token_present"])

    def test_same_database_or_directory_or_invalid_path_is_rejected(self):
        for changes in ({"DB_PATH": "same.db", "SUPPORT_DB_PATH": "same.db"},
                        {"DB_PATH": str(self.root)}, {"DB_PATH": "bad\x00path"},
                        {"DB_PATH": ""}, {"DB_PATH": ":memory:"}):
            with self.subTest(changes=changes):
                self.assertFalse(self.inspect(**changes)["checks"]["separate_writable_database_locations"])

    def test_database_permission_is_metadata_only(self):
        with patch.object(checker.os, "access", return_value=False):
            self.assertEqual(self.inspect()["status"], "CONFIGURATION_REQUIRED")

    def test_missing_dependency_has_static_name_only(self):
        def installed(name):
            if name == "fastapi":
                raise importlib.metadata.PackageNotFoundError("private-package-manager-detail")
            return "1.0"
        with patch.object(checker.importlib.metadata, "version", side_effect=installed):
            report = self.inspect()
        self.assertEqual(report["missing_dependencies"], ["fastapi"])
        self.assertNotIn("private-package-manager-detail", json.dumps(report))

    def test_url_credentials_query_and_bad_port_are_rejected_and_never_echoed(self):
        for address in ("http://secret-user:secret-pass@127.0.0.1:11434", "http://127.0.0.1:11434/?key=secret-key",
                        "http://127.0.0.1:bad", "http://127.0.0.1:11434/api", "http://[broken"):
            with self.subTest(address=address):
                report = self.inspect(OLLAMA_BASE_URL=address)
                self.assertEqual(report["status"], "CONFIGURATION_REQUIRED")
                self.assertNotIn("secret", json.dumps(report))

    def test_custom_host_is_not_echoed(self):
        report = self.inspect(OLLAMA_BASE_URL="https://private-model.example.invalid")
        self.assertIn("custom_model_address_requires_private_network_review", report["notes"])
        self.assertNotIn("private-model.example.invalid", json.dumps(report))

    def test_cannot_connect_network_or_read_database_even_when_enabled(self):
        with patch("socket.socket", side_effect=AssertionError("network forbidden")), \
                patch("sqlite3.connect", side_effect=AssertionError("database forbidden")):
            report = self.inspect(MODEL_ENABLED="true", OLLAMA_MODEL="synthetic-tag")
        self.assertEqual(report["status"], "OFFLINE_READY")
        self.assertEqual(report["network_requests"], 0)

    def test_optional_cloud_provider_requires_key_only_when_enabled(self):
        self.assertEqual(self.inspect(LLM_PROVIDER="deepseek")["status"], "OFFLINE_READY")
        self.assertEqual(self.inspect(LLM_PROVIDER="deepseek", MODEL_ENABLED="true")["status"], "CONFIGURATION_REQUIRED")
        report = self.inspect(LLM_PROVIDER="deepseek", MODEL_ENABLED="true", DEEPSEEK_API_KEY="private-cloud-key")
        self.assertEqual(report["status"], "OFFLINE_READY")
        self.assertNotIn("private-cloud-key", json.dumps(report))

    def test_invalid_provider_or_token_limit_is_rejected_without_echo(self):
        for updates in ({"LLM_PROVIDER": "secret-provider"}, {"MODEL_MAX_INPUT_TOKENS": "0"},
                        {"MODEL_MAX_OUTPUT_TOKENS": "secret-value"}):
            report = self.inspect(**updates)
            self.assertEqual(report["status"], "CONFIGURATION_REQUIRED")
            self.assertNotIn("secret", json.dumps(report))

    def test_json_cli_prints_no_credentials(self):
        output = io.StringIO()
        with patch.dict("os.environ", self.env, clear=True), \
                patch("dotenv.load_dotenv", return_value=False), \
                patch.object(checker, "_database_locations_ready", return_value=True), \
                patch("socket.socket", side_effect=AssertionError("network forbidden")), \
                contextlib.redirect_stdout(output):
            code = checker.main(["--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "OFFLINE_READY")
        self.assertNotIn(self.env["ADMIN_API_TOKEN"], output.getvalue())


if __name__ == "__main__":
    unittest.main()
