"""
统一 LLM 调用层 — 支持 DeepSeek / Gemini / Claude 切换。

Provider 选择优先级:
1. 环境变量 LLM_PROVIDER (deepseek / gemini / claude)，默认 deepseek
2. 如果主 provider 调用失败且备用的 API Key 已配置，自动 fallback

System Prompt 角色扮演示例（供 README 引用）:
---
你是一位{customer_profile.name}，{customer_profile.occupation}。
你的性格特点：{customer_profile.personality}
你目前的顾虑：{customer_profile.pain_points}
你对推销员的初始态度：{initial_attitude}

规则：
- 你不知道对方在练习，你真实扮演这位客户
- 根据对方的话术质量自然地调整态度（好的话术让你有所触动，差的话术让你更抵触）
- 对话要自然，不要使用销售培训教材里的模板化语言
- 当以下情况发生时结束对话：
  a) 客户明确拒绝并挂断
  b) 客户同意下一步行动（留联系方式/预约见面）
  c) 对话超过 15 轮
- 对话结束时在最后一行输出：[RESULT: success|reject|followup]
---
"""

import os
import time
import json
import logging
from typing import Iterator, Optional

logger = logging.getLogger(__name__)


class LLMError(Exception):
    """LLM 调用统一异常。"""


class LLMClient:
    """统一 LLM 调用客户端，支持 DeepSeek / Gemini / Claude 多后端。

    用法:
        client = LLMClient()
        reply = client.chat(
            messages=[{"role": "user", "content": "你好"}],
            system="你是一个销售培训师",
        )
        # 流式输出
        for chunk in client.chat(..., stream=True):
            print(chunk, end="")
    """

    # provider 降级顺序
    _FALLBACK_ORDER = {
        "deepseek": ["gemini", "claude"],
        "gemini": ["deepseek", "claude"],
        "claude": ["deepseek", "gemini"],
    }

    def __init__(self):
        self.provider = os.getenv("LLM_PROVIDER", "deepseek").lower()
        self.deepseek_model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
        self.gemini_model = os.getenv("GEMINI_MODEL", "gemini-1.5-pro")
        self.claude_model = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")
        self.deepseek_key = os.getenv("DEEPSEEK_API_KEY", "")
        self.gemini_key = os.getenv("GOOGLE_API_KEY", "")
        self.claude_key = os.getenv("ANTHROPIC_API_KEY", "")
        self.max_retries = 3
        self._gemini_configured = False

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def chat(
        self,
        messages: list[dict],
        system: str = "",
        stream: bool = False,
        model: Optional[str] = None,
    ):
        """统一 chat 接口。

        Args:
            messages: [{"role": "user/assistant", "content": "..."}]
            system: 系统提示词
            stream: 是否流式输出（返回 generator）
            model: 覆盖默认模型名

        Returns:
            str (stream=False) 或 Iterator[str] (stream=True)
        """
        primary = self.provider
        fallbacks = self._FALLBACK_ORDER.get(primary, [])

        # 尝试主 provider
        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                return self._call(primary, messages, system, stream, model)
            except Exception as e:
                last_error = e
                logger.warning(
                    "[%s] 第 %d/%d 次调用失败: %s",
                    primary, attempt, self.max_retries, e,
                )
                if attempt < self.max_retries:
                    time.sleep(2 ** (attempt - 1))

        # 尝试 fallback provider
        for fb in fallbacks:
            if self._has_credentials(fb):
                logger.info("主 provider 失败，fallback 到 %s", fb)
                try:
                    return self._call(fb, messages, system, stream, model)
                except Exception as fb_err:
                    logger.warning("[%s] fallback 也失败: %s", fb, fb_err)
                    last_error = fb_err

        raise LLMError(f"所有 LLM provider 调用均失败，最后错误: {last_error}")

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _call(self, provider: str, messages, system, stream, model):
        if provider == "deepseek":
            return self._call_deepseek(messages, system, stream, model)
        elif provider == "gemini":
            return self._call_gemini(messages, system, stream, model)
        elif provider == "claude":
            return self._call_claude(messages, system, stream, model)
        else:
            raise LLMError(f"未知 LLM provider: {provider}")

    def _has_credentials(self, provider: str) -> bool:
        if provider == "deepseek":
            return bool(self.deepseek_key)
        elif provider == "gemini":
            return bool(self.gemini_key)
        elif provider == "claude":
            return bool(self.claude_key)
        return False

    # ---- DeepSeek ---------------------------------------------------------

    def _call_deepseek(self, messages, system, stream, model):
        import httpx

        model_name = model or self.deepseek_model

        # 构建 messages（system prompt 作为第一条消息）
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend(messages)

        payload = {
            "model": model_name,
            "messages": msgs,
            "stream": stream,
            "max_tokens": 4096,
        }
        headers = {
            "Authorization": f"Bearer {self.deepseek_key}",
            "Content-Type": "application/json",
        }

        if stream:
            return self._deepseek_stream(payload, headers)
        else:
            with httpx.Client(timeout=120) as client:
                resp = client.post(
                    "https://api.deepseek.com/v1/chat/completions",
                    headers=headers,
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"]

    def _deepseek_stream(self, payload, headers):
        """SSE 流式解析，返回字符串迭代器。"""
        import httpx

        with httpx.Client(timeout=120) as client:
            with client.stream(
                "POST",
                "https://api.deepseek.com/v1/chat/completions",
                headers=headers,
                json=payload,
            ) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            break
                        try:
                            data = json.loads(data_str)
                            delta = data["choices"][0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                yield content
                        except (json.JSONDecodeError, KeyError, IndexError):
                            continue

    # ---- Gemini ----------------------------------------------------------

    def _call_gemini(self, messages, system, stream, model):
        import google.generativeai as genai

        if not self._gemini_configured:
            genai.configure(api_key=self.gemini_key)
            self._gemini_configured = True

        model_name = model or self.gemini_model
        gemini_model = genai.GenerativeModel(
            model_name=model_name,
            system_instruction=system if system else None,
        )

        # 拆分历史（除最后一条以外的消息）和当前消息
        history = self._to_gemini_history(messages[:-1])
        current = messages[-1]["content"] if messages else ""

        chat = gemini_model.start_chat(history=history)

        if stream:
            response = chat.send_message(current, stream=True)
            return self._gemini_stream_wrapper(response)
        else:
            response = chat.send_message(current)
            return response.text

    def _to_gemini_history(self, messages: list[dict]) -> list[dict]:
        """将统一格式转为 Gemini history 格式。

        unified: {"role": "user/assistant", "content": "text"}
        gemini:  {"role": "user/model", "parts": ["text"]}
        """
        history = []
        for msg in messages:
            role = "model" if msg["role"] == "assistant" else "user"
            history.append({"role": role, "parts": [msg["content"]]})
        return history

    def _gemini_stream_wrapper(self, response):
        """将 Gemini 流式响应包装为统一的字符串迭代器。"""
        for chunk in response:
            if chunk.text:
                yield chunk.text

    # ---- Claude ----------------------------------------------------------

    def _call_claude(self, messages, system, stream, model):
        import anthropic

        client = anthropic.Anthropic(api_key=self.claude_key)
        model_name = model or self.claude_model

        if stream:
            return self._claude_stream_wrapper(
                client, model_name, messages, system
            )

        resp = client.messages.create(
            model=model_name,
            max_tokens=4096,
            system=system if system else anthropic.NOT_GIVEN,
            messages=messages,
        )
        # Claude 返回的是一个 ContentBlock 列表，取第一个 text block
        for block in resp.content:
            if block.type == "text":
                return block.text
        return ""

    def _claude_stream_wrapper(self, client, model, messages, system):
        """将 Claude 流式响应包装为统一的字符串迭代器。"""
        import anthropic

        with client.messages.stream(
            model=model,
            max_tokens=4096,
            system=system if system else anthropic.NOT_GIVEN,
            messages=messages,
        ) as stream:
            for text in stream.text_stream:
                yield text
