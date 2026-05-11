"""LLM Client 单元测试 — 测试消息转换、降级逻辑、流式解析。"""

import os
import sys
import json
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DEEPSEEK_API_KEY"] = "test-key"
os.environ["LLM_PROVIDER"] = "deepseek"

from core.llm_client import LLMClient, LLMError


class TestLLMClient(unittest.TestCase):

    def setUp(self):
        self.client = LLMClient()

    # ---- 基础属性 ----

    def test_default_provider(self):
        self.assertEqual(self.client.provider, "deepseek")

    def test_has_credentials(self):
        self.assertTrue(self.client._has_credentials("deepseek"))
        self.assertFalse(self.client._has_credentials("gemini"))

    # ---- 消息转换 ----

    def test_gemini_history_user_role(self):
        messages = [{"role": "user", "content": "hello"}]
        result = self.client._to_gemini_history(messages)
        self.assertEqual(result[0]["role"], "user")
        self.assertEqual(result[0]["parts"], ["hello"])

    def test_gemini_history_assistant_role(self):
        messages = [{"role": "assistant", "content": "hi there"}]
        result = self.client._to_gemini_history(messages)
        self.assertEqual(result[0]["role"], "model")

    def test_gemini_history_mixed(self):
        messages = [
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q2"},
        ]
        result = self.client._to_gemini_history(messages)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0]["role"], "user")
        self.assertEqual(result[1]["role"], "model")
        self.assertEqual(result[2]["role"], "user")

    # ---- SSE 流式解析 ----

    def test_deepseek_stream_parse(self):
        """模拟 SSE 数据流进行解析。"""
        payload = {"model": "deepseek-chat", "messages": [], "stream": True}
        headers = {"Authorization": "Bearer test", "Content-Type": "application/json"}

        mock_lines = [
            'data: {"choices":[{"delta":{"content":"你好"}}]}',
            'data: {"choices":[{"delta":{"content":"世界"}}]}',
            "data: [DONE]",
        ]

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client
            mock_client.__enter__.return_value = mock_client

            mock_resp = MagicMock()
            mock_resp.iter_lines.return_value = mock_lines
            mock_client.stream.return_value = mock_resp
            mock_resp.__enter__.return_value = mock_resp

            chunks = list(self.client._deepseek_stream(payload, headers))
            self.assertEqual(chunks, ["你好", "世界"])

    def test_deepseek_stream_empty_delta(self):
        payload = {"model": "deepseek-chat", "messages": [], "stream": True}
        headers = {}

        mock_lines = [
            'data: {"choices":[{"delta":{}}]}',
            'data: {"choices":[{"delta":{"content":""}}]}',
            "data: [DONE]",
        ]

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client
            mock_client.__enter__.return_value = mock_client
            mock_resp = MagicMock()
            mock_resp.iter_lines.return_value = mock_lines
            mock_client.stream.return_value = mock_resp
            mock_resp.__enter__.return_value = mock_resp

            chunks = list(self.client._deepseek_stream(payload, headers))
            self.assertEqual(chunks, [])

    # ---- 降级链 ----

    @patch("core.llm_client.LLMClient._call")
    def test_fallback_on_failure(self, mock_call):
        mock_call.side_effect = [
            Exception("deepseek down"),  # 主 provider 3 次都失败
            Exception("deepseek down"),
            Exception("deepseek down"),
            "gemini response",  # fallback 成功
        ]

        self.client.gemini_key = "fake-gemini-key"
        result = self.client.chat(
            messages=[{"role": "user", "content": "hi"}],
        )
        self.assertEqual(result, "gemini response")

    def test_missing_credentials_raises(self):
        self.client.deepseek_key = ""
        os.environ["DEEPSEEK_API_KEY"] = ""
        client = LLMClient()
        client.gemini_key = ""
        client.claude_key = ""

        with self.assertRaises(LLMError):
            with patch.object(client, "_call", side_effect=Exception("fail")):
                client.chat(messages=[{"role": "user", "content": "hi"}])


if __name__ == "__main__":
    unittest.main()
