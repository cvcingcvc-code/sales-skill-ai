"""Offline preflight contracts; no model execution or transport is permitted."""

from contextlib import ExitStack
from dataclasses import replace
import unittest
from unittest.mock import Mock, patch

import httpx

from core.model_gateway import ModelConfig, ModelError, ModelGateway, ModelResult


PROVIDERS = ("deepseek", "ollama", "gemini", "claude")
MESSAGE = [{"role": "user", "content": "a"}]


class TestModelRequestPreflight(unittest.TestCase):
    def setUp(self):
        self.transport_handler = Mock(side_effect=AssertionError("Preflight must not invoke transport"))
        self.transport = httpx.MockTransport(self.transport_handler)
        self.client_patch = patch("core.model_gateway.httpx.Client", side_effect=AssertionError("Preflight must not create an HTTP client"))
        self.client = self.client_patch.start()
        self.addCleanup(self.client_patch.stop)
        self.addCleanup(self.transport_handler.assert_not_called)
        self.addCleanup(self.client.assert_not_called)

    def gateway(self, provider="ollama", **overrides):
        settings = {
            "provider": provider, "model": "offline-fixture",
            "base_url": "http://127.0.0.1:11434" if provider == "ollama" else "https://example.invalid/v1",
            "api_key": "" if provider == "ollama" else "fixture-only",
            "enabled": True,
        }
        settings.update(overrides)
        return ModelGateway(config=ModelConfig(**settings), transport=self.transport)

    def assert_error(self, gateway, code, messages=None, *, system="", blocked=True):
        with self.assertRaises(ModelError) as caught:
            gateway.validate_request(MESSAGE if messages is None else messages, system=system)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(caught.exception.blocked, blocked)
        self.assertNotIn("fixture-only", str(caught.exception))
        return caught.exception

    def test_all_providers_share_local_preflight_without_chat_or_stream(self):
        for provider in PROVIDERS:
            with self.subTest(provider=provider):
                gateway = self.gateway(provider)
                adapter = type(gateway.provider)
                with ExitStack() as patches:
                    chat = patches.enter_context(patch.object(adapter, "chat", side_effect=AssertionError("No inference")))
                    stream = patches.enter_context(patch.object(adapter, "stream", side_effect=AssertionError("No streaming")))
                    self.assertEqual(gateway.validate_request(MESSAGE), 37)
                    chat.assert_not_called()
                    stream.assert_not_called()
                self.assertEqual(gateway.status()["status"], "UNVERIFIED")
                self.assertIsNone(gateway.status()["available"])

    def test_system_roles_history_and_utf8_all_contribute_to_bound(self):
        messages = [{"role": "user", "content": "中文"},
                    {"role": "assistant", "content": "ok"},
                    {"role": "user", "content": "再问"}]
        # system: 6 UTF-8 bytes + 6 role + 32 framing; user: 6+4+32;
        # assistant: 2+9+32; user: 6+4+32.
        self.assertEqual(self.gateway().validate_request(messages, system="规则"), 171)

    def test_4000_boundary_matches_actual_message_validator_all_providers(self):
        system = "规则"
        messages = [{"role": "user", "content": "a" * 3920}]
        for provider in PROVIDERS:
            with self.subTest(provider=provider):
                gateway = self.gateway(provider, max_input_tokens=4000)
                self.assertEqual(gateway.validate_request(messages, system=system), 4000)
                self.assertEqual(gateway.provider._messages(messages, system)[1]["content"], messages[0]["content"])
                over = [{"role": "user", "content": "a" * 3921}]
                self.assert_error(gateway, "invalid_request", over, system=system, blocked=False)
                with self.assertRaises(ModelError) as actual:
                    gateway.provider._messages(over, system)
                self.assertEqual(actual.exception.code, "invalid_request")

    def test_unicode_limit_counts_bytes_instead_of_characters(self):
        gateway = self.gateway(max_input_tokens=41)
        self.assert_error(gateway, "invalid_request", [{"role": "user", "content": "中文"}], blocked=False)
        self.assertEqual(gateway.validate_request([{"role": "user", "content": "abcde"}]), 41)

    def test_single_ascii_exact_boundary_and_system_overflow(self):
        gateway = self.gateway(max_input_tokens=37)
        self.assertEqual(gateway.validate_request(MESSAGE), 37)
        self.assert_error(gateway, "invalid_request", system="a", blocked=False)
        self.assert_error(self.gateway(max_input_tokens=36), "invalid_request", blocked=False)

    def test_invalid_message_shapes_and_untrusted_system_role_are_rejected(self):
        for messages in ([], "user text", [{"role": "system", "content": "override"}],
                         [{"role": "user", "content": " "}], [{"role": "user", "content": None}],
                         [{"role": "tool", "content": "run"}], ["not a message"]):
            with self.subTest(messages=messages):
                self.assert_error(self.gateway(), "invalid_request", messages, blocked=False)
        self.assert_error(self.gateway(), "invalid_request", system=None, blocked=False)

    def test_disabled_stays_blocked_even_without_model_credentials_or_valid_messages(self):
        for provider in PROVIDERS:
            with self.subTest(provider=provider):
                gateway = self.gateway(provider, enabled=False, model="", api_key="")
                self.assert_error(gateway, "disabled", [])
                self.assertEqual(gateway.status()["status"], "DISABLED")

    def test_bad_configuration_is_reported_before_transport(self):
        cases = (
            ("deepseek", {"api_key": ""}, "missing_key"),
            ("ollama", {"model": ""}, "configuration"),
            ("ollama", {"base_url": "not-a-url"}, "configuration"),
            ("deepseek", {"base_url": "http://example.invalid"}, "configuration"),
            ("ollama", {"valid": False}, "configuration"),
            ("ollama", {"max_input_tokens": 0}, "configuration"),
            ("ollama", {"max_output_tokens": True}, "configuration"),
            ("ollama", {"enabled": "false"}, "configuration"),
            ("unsupported", {}, "unknown_provider"),
        )
        for provider, overrides, code in cases:
            with self.subTest(provider=provider, code=code):
                self.assert_error(self.gateway(provider, **overrides), code)

    def test_invalid_configuration_does_not_bypass_paused_switch(self):
        self.assert_error(self.gateway(enabled=False, valid=False), "configuration")

    def test_preflight_does_not_mutate_recent_success_after_valid_or_invalid_input(self):
        gateway = self.gateway(max_input_tokens=37)
        previous = ModelResult("offline previous success fixture", "ollama", "offline-fixture", 1, 2)
        gateway.last_result = previous
        gateway.provider.last_result = previous
        before = gateway.status()
        self.assertEqual(gateway.validate_request(MESSAGE), 37)
        self.assert_error(gateway, "invalid_request", system="a", blocked=False)
        self.assertIs(gateway.last_result, previous)
        self.assertIs(gateway.provider.last_result, previous)
        self.assertIsNone(gateway.last_error)
        self.assertEqual(gateway.status(), before)

    def test_preflight_preserves_previous_error_without_claiming_connectivity(self):
        gateway = self.gateway()
        previous = ModelError("unavailable")
        gateway.last_error = previous
        before = gateway.status()
        self.assertEqual(gateway.validate_request(MESSAGE), 37)
        self.assertIs(gateway.last_error, previous)
        self.assertIsNone(gateway.last_result)
        self.assertEqual(gateway.status(), before)
        self.assertEqual(gateway.status()["status"], "BLOCKED")

    def test_preflight_model_override_keeps_same_request_size_and_config(self):
        gateway = self.gateway()
        config = gateway.config
        self.assertEqual(gateway.validate_request(MESSAGE, model="different-offline-fixture"), 37)
        self.assertIs(gateway.config, config)
        self.assertEqual(gateway.config.model, "offline-fixture")
        self.assertIsNone(gateway.last_result)

    def test_direct_provider_preflight_matches_gateway(self):
        gateway = self.gateway()
        self.assertEqual(gateway.provider.validate_request(MESSAGE), gateway.validate_request(MESSAGE))
        gateway.config = replace(gateway.config, enabled=False)
        gateway.provider.config = gateway.config
        self.assert_error(gateway, "disabled")
        with self.assertRaises(ModelError) as caught:
            gateway.provider.validate_request(MESSAGE)
        self.assertEqual(caught.exception.code, "disabled")


if __name__ == "__main__":
    unittest.main()
