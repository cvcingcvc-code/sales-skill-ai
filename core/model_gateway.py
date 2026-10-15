"""Model gateway: verified responses, safe errors, and no implicit provider failover.

The gateway never executes model-supplied tools or database writes. Configuration
comes from server environment variables; user messages cannot override it.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import json
import math
import os
from typing import Iterator, Optional
from urllib.parse import urlsplit

import httpx


_ERROR_MESSAGES = {
    "missing_key": "BLOCKED：模型 API Key 未配置，请在环境设置中安全配置。",
    "configuration": "BLOCKED：模型配置无效或模型名称未配置。",
    "authentication": "BLOCKED：模型服务鉴权失败，请检查配置的凭据。",
    "timeout": "BLOCKED：模型请求超时，请稍后重试。",
    "unavailable": "BLOCKED：模型服务不可用或网络连接失败。",
    "rate_limited": "BLOCKED：模型服务限流，请稍后重试。",
    "invalid_response": "模型返回格式无效，本次调用未成功。",
    "invalid_request": "模型请求无效，请检查消息格式。",
    "unknown_provider": "BLOCKED：不支持所配置的模型 Provider。",
}


class ModelError(Exception):
    """Public error; never contains an upstream response, request, or credential."""

    def __init__(self, code: str, public_message: Optional[str] = None, blocked: bool = True):
        self.code = code
        self.public_message = public_message or _ERROR_MESSAGES.get(code, _ERROR_MESSAGES["unavailable"])
        self.blocked = blocked
        super().__init__(self.public_message)


@dataclass(frozen=True)
class ModelResult:
    text: str
    provider: str
    model: str
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    estimated_cost: Optional[float] = None


@dataclass(frozen=True)
class ModelConfig:
    provider: str
    model: str
    base_url: str
    api_key: str = field(default="", repr=False)
    timeout: float = 30.0
    input_price_per_million: Optional[float] = None
    output_price_per_million: Optional[float] = None
    valid: bool = True

    @classmethod
    def from_env(cls, provider: Optional[str] = None):
        name = (provider or os.getenv("LLM_PROVIDER", "deepseek")).strip().lower()
        prefixes = {"deepseek": "DEEPSEEK", "ollama": "OLLAMA", "gemini": "GEMINI", "claude": "CLAUDE"}
        prefix = prefixes.get(name, name.upper())
        defaults = {
            "deepseek": ("deepseek-chat", "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY"),
            "ollama": ("", "http://127.0.0.1:11434", ""),
            "gemini": ("", "https://generativelanguage.googleapis.com", "GOOGLE_API_KEY"),
            "claude": ("", "https://api.anthropic.com", "ANTHROPIC_API_KEY"),
        }
        default_model, default_url, key_env = defaults.get(name, ("", "", ""))
        valid = True
        try:
            timeout = float(os.getenv("MODEL_TIMEOUT_SECONDS", "30"))
            if not math.isfinite(timeout) or timeout <= 0 or timeout > 600:
                raise ValueError
        except ValueError:
            timeout, valid = 30.0, False
        prices = []
        for suffix in ("INPUT_PRICE_PER_MILLION", "OUTPUT_PRICE_PER_MILLION"):
            raw = os.getenv(f"{prefix}_{suffix}", "").strip()
            try:
                value = float(raw) if raw else None
                if value is not None and (not math.isfinite(value) or value < 0):
                    raise ValueError
                prices.append(value)
            except ValueError:
                prices.append(None)
                valid = False
        return cls(
            provider=name,
            model=os.getenv(f"{prefix}_MODEL", default_model).strip(),
            base_url=os.getenv(f"{prefix}_BASE_URL", default_url).strip().rstrip("/"),
            api_key=os.getenv(key_env, "").strip() if key_env else "",
            timeout=timeout,
            input_price_per_million=prices[0],
            output_price_per_million=prices[1],
            valid=valid,
        )

    def configuration_error(self):
        if self.provider not in {"deepseek", "ollama", "gemini", "claude"}:
            return ModelError("unknown_provider")
        if self.provider != "ollama" and not self.api_key:
            return ModelError("missing_key")
        try:
            url = urlsplit(self.base_url)
            valid_url = bool(url.hostname) and url.scheme in {"http", "https"}
            valid_url = valid_url and not (url.username or url.password or url.query or url.fragment)
            # Cloud credentials must never be sent over cleartext transport.
            if self.provider != "ollama" and url.scheme != "https":
                valid_url = False
        except ValueError:
            valid_url = False
        if not self.valid or not self.model or not valid_url:
            return ModelError("configuration")
        return None


def _token_count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _messages(messages, system):
    if not isinstance(messages, list) or not messages or not isinstance(system, str):
        raise ModelError("invalid_request", blocked=False)
    output = []
    if system:
        output.append({"role": "system", "content": system})
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in {"user", "assistant"}:
            raise ModelError("invalid_request", blocked=False)
        if not isinstance(message.get("content"), str) or not message["content"].strip():
            raise ModelError("invalid_request", blocked=False)
        output.append({"role": message["role"], "content": message["content"]})
    return output


def _safe_exception(error):
    if isinstance(error, ModelError):
        return error
    if isinstance(error, httpx.TimeoutException) or "timeout" in type(error).__name__.lower():
        return ModelError("timeout")
    status = getattr(error, "status_code", None)
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
    if status in (401, 403) or "authentication" in type(error).__name__.lower():
        return ModelError("authentication")
    if status == 429 or "ratelimit" in type(error).__name__.lower():
        return ModelError("rate_limited")
    if isinstance(status, int) and 400 <= status < 500:
        return ModelError("invalid_request", blocked=False)
    return ModelError("unavailable")


class ModelProvider(ABC):
    """Provider contract shared by cloud and local adapters."""

    def __init__(self, config: ModelConfig, *, transport=None):
        self.config = config
        self.transport = transport
        self.last_result = None

    def _check(self):
        error = self.config.configuration_error()
        if error:
            raise error

    def status(self):
        error = self.config.configuration_error()
        # Invalid URLs are omitted rather than echoing possible embedded secrets.
        url = self.config.base_url if not error or error.code == "missing_key" else None
        try:
            parsed = urlsplit(url or "")
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                url = None
        except ValueError:
            url = None
        return {
            "provider": self.config.provider,
            "model": self.config.model or None,
            "base_url": url,
            "configured": error is None,
            "available": None,
            "status": "BLOCKED" if error else "UNVERIFIED",
            "code": error.code if error else None,
            "message": error.public_message if error else "配置已读取，尚未完成真实模型连通性验证。",
            "credentials_present": bool(self.config.api_key) if self.config.provider != "ollama" else None,
            "cost_configured": self.config.input_price_per_million is not None and self.config.output_price_per_million is not None,
        }

    def _result(self, text, model, prompt_tokens=None, completion_tokens=None):
        if not isinstance(text, str) or not text.strip():
            raise ModelError("invalid_response", blocked=False)
        prompt_tokens = _token_count(prompt_tokens)
        completion_tokens = _token_count(completion_tokens)
        cost = None
        if (prompt_tokens is not None and completion_tokens is not None
                and self.config.input_price_per_million is not None
                and self.config.output_price_per_million is not None):
            cost = round((prompt_tokens * self.config.input_price_per_million
                          + completion_tokens * self.config.output_price_per_million) / 1_000_000, 10)
        self.last_result = ModelResult(text, self.config.provider, model, prompt_tokens, completion_tokens, cost)
        return self.last_result

    @abstractmethod
    def chat(self, messages, system="", model=None) -> ModelResult:
        pass

    @abstractmethod
    def stream(self, messages, system="", model=None) -> Iterator[str]:
        pass


class DeepSeekProvider(ModelProvider):
    """Official DeepSeek OpenAI-compatible Chat Completions API."""

    def _payload(self, messages, system, model, stream=False):
        self._check()
        return {"model": model or self.config.model, "messages": _messages(messages, system),
                "stream": stream, "max_tokens": 4096}

    def _headers(self):
        return {"Authorization": f"Bearer {self.config.api_key}", "Content-Type": "application/json"}

    def _client(self):
        return httpx.Client(timeout=self.config.timeout, transport=self.transport)

    def chat(self, messages, system="", model=None):
        payload = self._payload(messages, system, model)
        try:
            with self._client() as client:
                response = client.post(f"{self.config.base_url}/chat/completions", headers=self._headers(), json=payload)
                response.raise_for_status()
                try:
                    data = response.json()
                    text = data["choices"][0]["message"]["content"]
                    usage = data.get("usage") or {}
                    if not isinstance(usage, dict):
                        raise TypeError
                    return self._result(text, payload["model"], usage.get("prompt_tokens"), usage.get("completion_tokens"))
                except (ValueError, KeyError, IndexError, TypeError, AttributeError):
                    raise ModelError("invalid_response", blocked=False) from None
        except Exception as error:
            raise _safe_exception(error) from None

    def stream(self, messages, system="", model=None):
        payload = self._payload(messages, system, model, stream=True)
        payload["stream_options"] = {"include_usage": True}
        yield from self._stream_payload(payload, self._headers())

    def _stream_payload(self, payload, headers):
        """Raw SSE parser, also used by the legacy parser compatibility hook."""
        parts, usage, complete = [], {}, False
        try:
            with self._client() as client:
                with client.stream("POST", f"{self.config.base_url}/chat/completions", headers=headers, json=payload) as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if not line.startswith("data:"):
                            continue
                        raw = line[5:].strip()
                        if raw == "[DONE]":
                            complete = True
                            break
                        try:
                            data = json.loads(raw)
                            if not isinstance(data, dict) or "error" in data:
                                raise ValueError
                            if data.get("usage"):
                                if not isinstance(data["usage"], dict):
                                    raise ValueError
                                usage = data["usage"]
                            choices = data.get("choices", [])
                            if not isinstance(choices, list):
                                raise ValueError
                            if not choices:
                                continue
                            content = choices[0].get("delta", {}).get("content", "")
                            if content is None:
                                content = ""
                            if not isinstance(content, str):
                                raise ValueError
                        except (ValueError, KeyError, TypeError, AttributeError, IndexError):
                            raise ModelError("invalid_response", blocked=False) from None
                        if content:
                            parts.append(content)
                            yield content
            if not complete:
                raise ModelError("invalid_response", blocked=False)
            if parts:
                self._result("".join(parts), payload["model"], usage.get("prompt_tokens"), usage.get("completion_tokens"))
        except Exception as error:
            raise _safe_exception(error) from None


class OllamaProvider(ModelProvider):
    """Local Ollama adapter; no model download or daemon startup is implied."""

    def _payload(self, messages, system, model, stream=False):
        self._check()
        return {"model": model or self.config.model, "messages": _messages(messages, system), "stream": stream}

    def chat(self, messages, system="", model=None):
        payload = self._payload(messages, system, model)
        try:
            with httpx.Client(timeout=self.config.timeout, transport=self.transport) as client:
                response = client.post(f"{self.config.base_url}/api/chat", json=payload)
                response.raise_for_status()
                try:
                    data = response.json()
                    if data.get("done") is not True:
                        raise ValueError
                    return self._result(data["message"]["content"], payload["model"], data.get("prompt_eval_count"), data.get("eval_count"))
                except (ValueError, KeyError, TypeError, AttributeError):
                    raise ModelError("invalid_response", blocked=False) from None
        except Exception as error:
            raise _safe_exception(error) from None

    def stream(self, messages, system="", model=None):
        payload = self._payload(messages, system, model, stream=True)
        parts, complete = [], False
        try:
            with httpx.Client(timeout=self.config.timeout, transport=self.transport) as client:
                with client.stream("POST", f"{self.config.base_url}/api/chat", json=payload) as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                            if not isinstance(data, dict) or "error" in data:
                                raise ValueError
                            content = data.get("message", {}).get("content", "")
                            if not isinstance(content, str):
                                raise ValueError
                        except (ValueError, KeyError, TypeError, AttributeError):
                            raise ModelError("invalid_response", blocked=False) from None
                        if content:
                            parts.append(content)
                            yield content
                        if data.get("done") is True:
                            complete = True
                            self._result("".join(parts), payload["model"], data.get("prompt_eval_count"), data.get("eval_count"))
                            break
            if not complete:
                raise ModelError("invalid_response", blocked=False)
        except Exception as error:
            raise _safe_exception(error) from None


class GeminiProvider(ModelProvider):
    """Legacy Gemini support. A currently valid model must be configured explicitly."""

    @staticmethod
    def history(messages):
        return [{"role": "model" if m["role"] == "assistant" else "user", "parts": [m["content"]]} for m in messages]

    def _send(self, messages, system, model, stream):
        self._check()
        _messages(messages, system)
        import google.generativeai as genai
        genai.configure(api_key=self.config.api_key)
        instance = genai.GenerativeModel(model_name=model or self.config.model, system_instruction=system or None)
        chat = instance.start_chat(history=self.history(messages[:-1]))
        return chat.send_message(messages[-1]["content"], stream=stream, request_options={"timeout": self.config.timeout})

    def chat(self, messages, system="", model=None):
        try:
            response = self._send(messages, system, model, False)
            usage = getattr(response, "usage_metadata", None)
            return self._result(response.text, model or self.config.model, getattr(usage, "prompt_token_count", None), getattr(usage, "candidates_token_count", None))
        except Exception as error:
            raise _safe_exception(error) from None

    def stream(self, messages, system="", model=None):
        parts = []
        try:
            for chunk in self._send(messages, system, model, True):
                if chunk.text:
                    parts.append(chunk.text)
                    yield chunk.text
            self._result("".join(parts), model or self.config.model)
        except Exception as error:
            raise _safe_exception(error) from None


class ClaudeProvider(ModelProvider):
    """Legacy Claude adapter, with explicit model selection and safe SDK errors."""

    def _client(self):
        self._check()
        import anthropic
        return anthropic.Anthropic(api_key=self.config.api_key, timeout=self.config.timeout)

    def chat(self, messages, system="", model=None):
        _messages(messages, system)
        try:
            with self._client() as client:
                kwargs = {"model": model or self.config.model, "max_tokens": 4096, "messages": messages}
                if system:
                    kwargs["system"] = system
                response = client.messages.create(**kwargs)
                text = "".join(block.text for block in response.content if block.type == "text")
                usage = getattr(response, "usage", None)
                return self._result(text, kwargs["model"], getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None))
        except Exception as error:
            raise _safe_exception(error) from None

    def stream(self, messages, system="", model=None):
        _messages(messages, system)
        parts = []
        try:
            with self._client() as client:
                kwargs = {"model": model or self.config.model, "max_tokens": 4096, "messages": messages}
                if system:
                    kwargs["system"] = system
                with client.messages.stream(**kwargs) as response:
                    for text in response.text_stream:
                        parts.append(text)
                        yield text
                    final = response.get_final_message()
                    usage = getattr(final, "usage", None)
                    self._result("".join(parts), kwargs["model"], getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None))
        except Exception as error:
            raise _safe_exception(error) from None


class ModelGateway:
    """One explicitly selected provider. Configuration presence does not prove readiness."""

    def __init__(self, provider=None, *, transport=None, config=None):
        self.config = config or ModelConfig.from_env(provider)
        providers = {"deepseek": DeepSeekProvider, "ollama": OllamaProvider, "gemini": GeminiProvider, "claude": ClaudeProvider}
        provider_class = providers.get(self.config.provider, DeepSeekProvider)
        self.provider = provider_class(self.config, transport=transport)
        self.last_result = None
        self.last_error = None

    def chat(self, messages, system="", model=None) -> ModelResult:
        self.last_result = None
        try:
            self.last_result = self.provider.chat(messages, system, model)
            self.last_error = None
            return self.last_result
        except Exception as error:
            self.last_error = _safe_exception(error)
            raise self.last_error from None

    def stream(self, messages, system="", model=None):
        self.last_result = None
        self.provider.last_result = None
        try:
            yield from self.provider.stream(messages, system, model)
            if self.provider.last_result is None:
                raise ModelError("invalid_response", blocked=False)
            self.last_result = self.provider.last_result
            self.last_error = None
        except Exception as error:
            self.last_error = _safe_exception(error)
            raise self.last_error from None

    def status(self):
        result = self.provider.status()
        if self.last_error:
            result.update(status="BLOCKED" if self.last_error.blocked else "ERROR", available=False,
                          code=self.last_error.code, message=self.last_error.public_message)
        elif self.last_result:
            result.update(status="AVAILABLE", available=True, code=None,
                          message="最近一次真实模型请求成功；这不保证后续请求一定成功。")
        return result
