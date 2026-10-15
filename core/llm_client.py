"""Backwards-compatible text facade over the explicit ModelProvider gateway.

New services should use ModelGateway for provider, usage, and error metadata.
Legacy patch points remain available for the original training regression suite.
"""

from dataclasses import replace
import logging
import os
from typing import Optional

from core.model_gateway import (
    GeminiProvider, ModelConfig, ModelError, ModelGateway, ModelResult,
)

logger = logging.getLogger(__name__)
LLMError = ModelError


class LLMClient:
    _FALLBACK_ORDER = {
        "deepseek": ["gemini", "claude"],
        "gemini": ["deepseek", "claude"],
        "claude": ["deepseek", "gemini"],
    }

    def __init__(self):
        self.provider = os.getenv("LLM_PROVIDER", "deepseek").lower()
        self.deepseek_model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
        self.gemini_model = os.getenv("GEMINI_MODEL", "")
        self.claude_model = os.getenv("CLAUDE_MODEL", "")
        self.deepseek_key = os.getenv("DEEPSEEK_API_KEY", "")
        self.gemini_key = os.getenv("GOOGLE_API_KEY", "")
        self.claude_key = os.getenv("ANTHROPIC_API_KEY", "")
        self.max_retries = 3
        self.last_result = None
        self.last_error = None
        self._active_gateway = None

    def _gateway(self, provider):
        config = ModelConfig.from_env(provider)
        if provider in {"deepseek", "gemini", "claude"}:
            config = replace(config, api_key=getattr(self, f"{provider}_key"), model=getattr(self, f"{provider}_model"))
        self._active_gateway = ModelGateway(config=config)
        return self._active_gateway

    def status(self):
        gateway = self._active_gateway or self._gateway(self.provider)
        if self.last_result:
            gateway.last_result = self.last_result
        if self.last_error:
            gateway.last_error = self.last_error
        return gateway.status()

    def chat(self, messages: list[dict], system: str = "", stream: bool = False, model: Optional[str] = None):
        self.last_result = None
        self.last_error = None
        # ModelError from real adapters is deliberately never silently routed to
        # another cloud. The fallback hook only preserves old extension behavior
        # for legacy implementations that raise untyped exceptions.
        providers = [self.provider] + [p for p in self._FALLBACK_ORDER.get(self.provider, []) if self._has_credentials(p)]
        for index, provider in enumerate(providers):
            attempts = self.max_retries if index == 0 else 1
            for _ in range(attempts):
                try:
                    value = self._call(provider, messages, system, stream, model)
                    if stream:
                        return self._checked_stream(value, provider, model)
                    if not isinstance(value, str) or not value.strip():
                        raise ModelError("invalid_response", blocked=False)
                    if self.last_result is None:
                        config = ModelConfig.from_env(provider)
                        self.last_result = ModelResult(value, provider, model or config.model)
                    if provider != self.provider:
                        logger.warning("Legacy fallback used provider=%s; inspect last_result for actual provider.", provider)
                    return value
                except ModelError as error:
                    self.last_error = error
                    raise
                except Exception:
                    # Never log/return upstream exception text or credential data.
                    logger.warning("Legacy provider call failed provider=%s code=unavailable", provider)
        self.last_error = ModelError("unavailable")
        raise self.last_error

    def _checked_stream(self, chunks, provider, model):
        parts = []
        try:
            for chunk in chunks:
                if not isinstance(chunk, str):
                    raise ModelError("invalid_response", blocked=False)
                parts.append(chunk)
                yield chunk
            if not "".join(parts).strip():
                raise ModelError("invalid_response", blocked=False)
            if self.last_result is None:
                self.last_result = ModelResult("".join(parts), provider, model or ModelConfig.from_env(provider).model)
        except ModelError as error:
            self.last_error = error
            raise
        except Exception:
            self.last_error = ModelError("unavailable")
            raise self.last_error from None

    def _call(self, provider, messages, system, stream, model):
        if provider == "deepseek":
            return self._call_deepseek(messages, system, stream, model)
        if provider == "gemini":
            return self._call_gemini(messages, system, stream, model)
        if provider == "claude":
            return self._call_claude(messages, system, stream, model)
        return self._call_gateway(provider, messages, system, stream, model)

    def _call_gateway(self, provider, messages, system, stream, model):
        gateway = self._gateway(provider)
        if stream:
            def iterator():
                yield from gateway.stream(messages, system, model)
                self.last_result = gateway.last_result
            return iterator()
        result = gateway.chat(messages, system, model)
        self.last_result = result
        return result.text

    def _has_credentials(self, provider):
        if provider in {"deepseek", "gemini", "claude"}:
            return bool(getattr(self, f"{provider}_key"))
        return provider == "ollama"

    def _call_deepseek(self, messages, system, stream, model):
        if not stream:
            return self._call_gateway("deepseek", messages, system, False, model)
        gateway = self._gateway("deepseek")
        payload = gateway.provider._payload(messages, system, model, stream=True)
        payload["stream_options"] = {"include_usage": True}
        return self._deepseek_stream(payload, gateway.provider._headers())

    def _deepseek_stream(self, payload, headers):
        gateway = self._active_gateway or self._gateway("deepseek")
        try:
            yield from gateway.provider._stream_payload(payload, headers)
            self.last_result = gateway.provider.last_result
        except ModelError as error:
            self.last_error = error
            raise

    def _call_gemini(self, messages, system, stream, model):
        return self._call_gateway("gemini", messages, system, stream, model)

    def _to_gemini_history(self, messages):
        return GeminiProvider.history(messages)

    def _call_claude(self, messages, system, stream, model):
        return self._call_gateway("claude", messages, system, stream, model)
