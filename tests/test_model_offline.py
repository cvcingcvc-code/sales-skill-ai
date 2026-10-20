"""Offline switch/local-routing regressions. All HTTP replies are explicit mocks.

These tests establish adapter behavior, never local-model installation/readiness.
"""

import json
import os
import unittest
from unittest.mock import patch

import httpx

from core.llm_client import LLMClient
from core.model_gateway import (
    ClaudeProvider, DeepSeekProvider, GeminiProvider, ModelConfig, ModelError,
    ModelGateway, OllamaProvider,
)


MESSAGE = [{"role": "user", "content": "a"}]


class TestOfflineModelConfiguration(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        self.requests = []
        self.transport = httpx.MockTransport(self.respond)

    def respond(self, request):
        self.requests.append(request)
        if request.url.path.endswith("/api/chat"):
            return httpx.Response(200, json={"done": True, "message": {"content": "offline fixture"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "offline fixture"}}]})

    def config(self, provider="ollama", **overrides):
        values = dict(provider=provider, model="offline-fixture-model",
                      base_url="http://127.0.0.1:11434" if provider == "ollama" else "https://example.invalid/v1",
                      api_key="" if provider == "ollama" else "fixture-only")
        values.update(overrides)
        return ModelConfig(**values)

    def assert_error(self, function, code, blocked=True):
        with self.assertRaises(ModelError) as caught:
            function()
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(caught.exception.blocked, blocked)

    def test_existing_dataclass_constructor_stays_enabled(self):
        config = self.config("deepseek")
        self.assertTrue(config.enabled)
        self.assertEqual((config.max_input_tokens, config.max_output_tokens), (16000, 4096))
        self.assertEqual(ModelGateway(config=config, transport=self.transport).chat(MESSAGE).text, "offline fixture")

    def test_missing_switch_preserves_legacy_deepseek_configuration(self):
        os.environ["DEEPSEEK_API_KEY"] = "fixture-only"
        gateway = ModelGateway(transport=self.transport)
        self.assertTrue(gateway.config.enabled)
        self.assertEqual(gateway.config.provider, "deepseek")
        gateway.chat(MESSAGE)
        self.assertEqual(str(self.requests[0].url), "https://api.deepseek.com/v1/chat/completions")

    def test_template_is_explicitly_disabled_local_without_model(self):
        from pathlib import Path
        from dotenv import dotenv_values
        template = dotenv_values(Path(__file__).resolve().parents[1] / ".env.example")
        self.assertEqual(template["MODEL_ENABLED"], "false")
        self.assertEqual(template["LLM_PROVIDER"], "ollama")
        self.assertEqual(template["OLLAMA_MODEL"], "")

    def test_disabled_gateway_chat_has_zero_requests_and_fixed_error(self):
        gateway = ModelGateway(config=self.config(enabled=False), transport=self.transport)
        self.assert_error(lambda: gateway.chat(MESSAGE), "disabled")
        self.assertEqual(self.requests, [])
        self.assertIsNone(gateway.last_result)
        self.assertEqual(gateway.status()["status"], "DISABLED")

    def test_disabled_gateway_stream_has_zero_requests(self):
        gateway = ModelGateway(config=self.config(enabled=False), transport=self.transport)
        self.assert_error(lambda: list(gateway.stream(MESSAGE)), "disabled")
        self.assertEqual(self.requests, [])

    def test_disabled_direct_provider_chat_and_stream_prevent_all_transports(self):
        for provider, adapter in (("deepseek", DeepSeekProvider), ("ollama", OllamaProvider),
                                  ("gemini", GeminiProvider), ("claude", ClaudeProvider)):
            with self.subTest(provider=provider):
                instance = adapter(self.config(provider, enabled=False), transport=self.transport)
                with patch("httpx.Client", side_effect=AssertionError("No HTTP client may be created")):
                    self.assert_error(lambda: instance.chat(MESSAGE), "disabled")
                    self.assert_error(lambda: list(instance.stream(MESSAGE)), "disabled")
        self.assertEqual(self.requests, [])

    def test_disabled_does_not_require_model_or_key(self):
        for provider in ("ollama", "deepseek"):
            with self.subTest(provider=provider):
                gateway = ModelGateway(config=self.config(provider, enabled=False, model="", api_key=""), transport=self.transport)
                self.assert_error(lambda: gateway.chat([]), "disabled")
                state = gateway.status()
                self.assertEqual(state["status"], "DISABLED")
                self.assertFalse(state["configured"])
                self.assertFalse(state["available"])
                self.assertFalse(state["enabled"])
                self.assertIn(state["configuration_code"], {"configuration", "missing_key"})
        self.assertEqual(self.requests, [])

    def test_disabled_valid_configuration_is_distinct_from_readiness(self):
        gateway = ModelGateway(config=self.config(enabled=False), transport=self.transport)
        state = gateway.status()
        self.assertTrue(state["configured"])
        self.assertFalse(state["available"])
        self.assertIsNone(state["configuration_code"])
        self.assertEqual(state["code"], "disabled")
        self.assertEqual(state["status"], "DISABLED")

    def test_disabled_legacy_facade_prevents_custom_call_and_fallback(self):
        os.environ.update(MODEL_ENABLED="false", LLM_PROVIDER="deepseek", DEEPSEEK_API_KEY="fixture-only")
        client = LLMClient()
        client.gemini_key = "fixture-only"
        with patch.object(client, "_call", side_effect=AssertionError("No call/fallback may run")) as invoke:
            self.assert_error(lambda: client.chat(MESSAGE), "disabled")
            self.assert_error(lambda: client.chat(MESSAGE, stream=True), "disabled")
            invoke.assert_not_called()
        self.assertEqual(client.status()["status"], "DISABLED")

    def test_disabled_raw_legacy_stream_prevents_http(self):
        config = self.config("deepseek", enabled=False)
        provider = DeepSeekProvider(config, transport=self.transport)
        self.assert_error(lambda: list(provider._stream_payload({"model": config.model}, {})), "disabled")
        self.assertEqual(self.requests, [])

    def test_invalid_switch_is_configuration_error_never_implicit_enable(self):
        for value in ("", "1", "0", "yes", "off", "tru", "None"):
            with self.subTest(value=value), patch.dict(os.environ, {"MODEL_ENABLED": value, "DEEPSEEK_API_KEY": "fixture-only"}):
                gateway = ModelGateway(transport=self.transport)
                self.assert_error(lambda: gateway.chat(MESSAGE), "configuration")
                self.assert_error(lambda: list(gateway.stream(MESSAGE)), "configuration")
                self.assertEqual(gateway.status()["status"], "BLOCKED")
        self.assertEqual(self.requests, [])

    def test_switch_accepts_case_and_whitespace(self):
        with patch.dict(os.environ, {"MODEL_ENABLED": "  FALSE  "}):
            self.assertFalse(ModelConfig.from_env().enabled)
            self.assertTrue(ModelConfig.from_env().valid)
        with patch.dict(os.environ, {"MODEL_ENABLED": " TRUE "}):
            self.assertTrue(ModelConfig.from_env().enabled)

    def test_invalid_limits_block_before_http(self):
        for name in ("MODEL_MAX_INPUT_TOKENS", "MODEL_MAX_OUTPUT_TOKENS"):
            for value in ("0", "-1", "1.5", "NaN", "", "True"):
                with self.subTest(name=name, value=value), patch.dict(os.environ, {name: value, "DEEPSEEK_API_KEY": "fixture-only"}):
                    gateway = ModelGateway(transport=self.transport)
                    self.assert_error(lambda: gateway.chat(MESSAGE), "configuration")
        self.assertEqual(self.requests, [])

    def test_manual_invalid_limits_block_before_http(self):
        for value in (0, -1, True, 1.2):
            with self.subTest(value=value):
                gateway = ModelGateway(config=self.config(max_input_tokens=value), transport=self.transport)
                self.assert_error(lambda: gateway.chat(MESSAGE), "configuration")
        self.assertEqual(self.requests, [])

    def test_utf8_input_limit_exact_boundary_and_system_are_included(self):
        # user role: 4 bytes, one byte content, 32 framing allowance = 37.
        gateway = ModelGateway(config=self.config(max_input_tokens=37), transport=self.transport)
        gateway.chat(MESSAGE)
        self.assertEqual(len(self.requests), 1)
        too_small = ModelGateway(config=self.config(max_input_tokens=36), transport=self.transport)
        self.assert_error(lambda: too_small.chat(MESSAGE), "invalid_request", blocked=False)
        self.assert_error(lambda: gateway.chat(MESSAGE, system="a"), "invalid_request", blocked=False)
        self.assert_error(lambda: gateway.chat([{"role": "user", "content": "中"}]), "invalid_request", blocked=False)
        self.assertEqual(len(self.requests), 1)

    def test_input_cap_covers_direct_provider_stream_before_network(self):
        for provider, adapter in (("deepseek", DeepSeekProvider), ("ollama", OllamaProvider),
                                  ("gemini", GeminiProvider), ("claude", ClaudeProvider)):
            with self.subTest(provider=provider):
                instance = adapter(self.config(provider, max_input_tokens=1), transport=self.transport)
                self.assert_error(lambda: instance.chat(MESSAGE), "invalid_request", blocked=False)
                self.assert_error(lambda: list(instance.stream(MESSAGE)), "invalid_request", blocked=False)
        self.assertEqual(self.requests, [])

    def test_output_cap_deepseek_and_ollama_requests(self):
        for provider in ("deepseek", "ollama"):
            with self.subTest(provider=provider):
                gateway = ModelGateway(config=self.config(provider, max_output_tokens=73), transport=self.transport)
                gateway.chat(MESSAGE)
                payload = json.loads(self.requests[-1].content)
                if provider == "ollama":
                    self.assertEqual(payload["options"]["num_predict"], 73)
                else:
                    self.assertEqual(payload["max_tokens"], 73)

    def test_stream_payload_limits_match_nonstream(self):
        for provider, adapter in (("deepseek", DeepSeekProvider), ("ollama", OllamaProvider)):
            with self.subTest(provider=provider):
                instance = adapter(self.config(provider, max_output_tokens=37), transport=self.transport)
                normal = instance._payload(MESSAGE, "", None, stream=False)
                stream = instance._payload(MESSAGE, "", None, stream=True)
                normal["stream"] = True
                self.assertEqual(normal, stream)

    def test_status_documents_limits_without_creating_http_client(self):
        gateway = ModelGateway(config=self.config(), transport=self.transport)
        with patch("httpx.Client", side_effect=AssertionError("status must not probe")):
            state = gateway.status()
        self.assertEqual((state["max_input_tokens"], state["max_output_tokens"]), (16000, 4096))
        self.assertEqual(state["input_token_estimate"], "utf8_bytes_upper_bound")
        self.assertEqual(state["status"], "UNVERIFIED")
        self.assertIsNone(state["available"])

    def test_unconfigured_ollama_is_explicitly_unavailable_without_probe(self):
        os.environ["LLM_PROVIDER"] = "ollama"
        gateway = ModelGateway(transport=self.transport)
        with patch("httpx.Client", side_effect=AssertionError("No local daemon probe")):
            state = gateway.status()
            self.assert_error(lambda: gateway.chat(MESSAGE), "configuration")
        self.assertEqual((state["status"], state["code"], state["available"]), ("BLOCKED", "configuration", False))

    def test_loopback_ollama_bypasses_proxy_and_keeps_tls_verification(self):
        original_client = httpx.Client
        for url in ("http://127.0.0.1:11434", "http://localhost:11434", "http://[::1]:11434", "https://localhost:11434"):
            with self.subTest(url=url), patch("core.model_gateway.httpx.Client", wraps=original_client) as client_class:
                gateway = ModelGateway(config=self.config(base_url=url), transport=self.transport)
                gateway.chat(MESSAGE)
                self.assertIs(client_class.call_args.kwargs["trust_env"], False)
                self.assertIsNot(client_class.call_args.kwargs.get("verify", True), False)
                self.assertEqual(self.requests[-1].url.host, httpx.URL(url).host)
                self.assertNotIn("authorization", self.requests[-1].headers)

    def test_remote_ollama_retains_environment_routing_and_tls(self):
        original_client = httpx.Client
        for url in ("http://192.168.1.10:11434", "https://model.example.invalid"):
            with self.subTest(url=url), patch("core.model_gateway.httpx.Client", wraps=original_client) as client_class:
                ModelGateway(config=self.config(base_url=url), transport=self.transport).chat(MESSAGE)
                self.assertIs(client_class.call_args.kwargs["trust_env"], True)
                self.assertIsNot(client_class.call_args.kwargs.get("verify", True), False)

    def test_ollama_connection_failure_never_contacts_cloud(self):
        os.environ.update(LLM_PROVIDER="ollama", OLLAMA_MODEL="fixture-only", DEEPSEEK_API_KEY="fixture-only")
        calls = []

        def offline(request):
            calls.append(str(request.url))
            raise httpx.ConnectError("offline local fixture", request=request)

        gateway = ModelGateway(transport=httpx.MockTransport(offline))
        self.assert_error(lambda: gateway.chat(MESSAGE), "unavailable")
        self.assertEqual(calls, ["http://127.0.0.1:11434/api/chat"])

    def test_live_acceptance_disabled_is_blocked_without_network(self):
        from scripts.verify_live import verify
        os.environ.update(MODEL_ENABLED="false", LLM_PROVIDER="ollama", OLLAMA_MODEL="fixture-only")
        with patch("httpx.Client", side_effect=AssertionError("No model HTTP")):
            report, exit_code = verify()
        self.assertEqual((exit_code, report["status"], report["reason"]), (2, "BLOCKED", "disabled"))
        self.assertEqual(report["provider"], "ollama")
        self.assertNotIn("PASS", report["checks"].values())

    def test_live_acceptance_ollama_needs_model_but_no_key(self):
        from scripts.verify_live import verify
        os.environ.update(MODEL_ENABLED="true", LLM_PROVIDER="ollama")
        with patch("httpx.Client", side_effect=AssertionError("No model configured")):
            report, exit_code = verify("ollama")
        self.assertEqual((exit_code, report["status"], report["reason"]), (2, "BLOCKED", "configuration"))

    def test_live_acceptance_ollama_full_flow_uses_explicit_mock_not_key(self):
        from scripts.verify_live import verify
        calls = []

        def reply(request):
            calls.append(request)
            selection = json.dumps({"product_ids": ["synthetic-acceptance-cup"],
                                    "knowledge_ids": ["synthetic-acceptance-policy"], "needs_human": False})
            return httpx.Response(200, json={"done": True, "message": {"content": selection},
                                             "prompt_eval_count": 20, "eval_count": 10})

        gateway = ModelGateway(config=self.config(), transport=httpx.MockTransport(reply))
        with patch("scripts.verify_live.ModelGateway", return_value=gateway):
            report, exit_code = verify("ollama")
        self.assertEqual((exit_code, report["status"]), (0, "PASS"))
        self.assertEqual(len(calls), 1)
        self.assertNotIn("authorization", calls[0].headers)
        self.assertEqual(report["target"]["host"], "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
