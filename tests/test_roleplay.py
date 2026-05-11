"""Roleplay 模块单元测试 — 测试 System Prompt 构建、结果解析、对话流程。"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.roleplay import RoleplaySession


SAMPLE_SCENARIO = {
    "id": "cold_visit_001",
    "name": "陌拜测试",
    "difficulty": "beginner",
    "sales_objective": "获得下次详谈机会",
    "success_criteria": ["客户同意留下联系方式"],
    "evaluation_dimensions": ["开场破冰", "需求挖掘", "价值陈述", "异议处理", "推进下一步"],
    "customer_profile": {
        "name": "王先生",
        "age": 35,
        "occupation": "企业中层管理",
        "personality": "理性、时间紧",
        "pain_points": ["担心骗局", "觉得不需要"],
        "budget_hint": "年预算5-10万",
        "initial_attitude": "cold",
    },
}


class TestRoleplaySession(unittest.TestCase):

    def setUp(self):
        self.mock_llm = MagicMock()
        self.session = RoleplaySession(SAMPLE_SCENARIO, "learner_001", self.mock_llm)

    # ---- System Prompt ----

    def test_system_prompt_contains_customer_name(self):
        prompt = self.session._system_prompt
        self.assertIn("王先生", prompt)

    def test_system_prompt_contains_attitude(self):
        prompt = self.session._system_prompt
        self.assertIn("态度冷淡", prompt)

    def test_system_prompt_contains_rules(self):
        prompt = self.session._system_prompt
        self.assertIn("[RESULT:", prompt)
        self.assertIn("15", prompt)  # max_rounds

    # ---- 开场白 ----

    def test_start_returns_opening(self):
        self.mock_llm.chat.return_value = "（语气冷淡）喂，哪位？"
        opening = self.session.start()
        self.assertEqual(opening, "（语气冷淡）喂，哪位？")
        self.assertEqual(len(self.session.transcript), 1)
        self.assertEqual(self.session.round, 1)

    # ---- 对话响应 ----

    def test_respond_adds_to_transcript(self):
        self.mock_llm.chat.return_value = "你好"
        self.session.start()
        self.session.respond("您好")
        self.assertEqual(len(self.session.transcript), 3)  # opening + sales + customer

    def test_respond_detects_result_success(self):
        self.mock_llm.chat.side_effect = [
            "开场白",
            "好的，我们约下周详谈。\n[RESULT: success]",
        ]
        self.session.start()
        reply, done = self.session.respond("您好，我们约下周详谈？")
        self.assertTrue(done)
        self.assertEqual(self.session.result, "success")
        self.assertNotIn("[RESULT:", reply)

    def test_respond_detects_result_reject(self):
        self.mock_llm.chat.side_effect = [
            "开场白",
            "不用了，别打了。\n[RESULT: reject]",
        ]
        self.session.start()
        reply, done = self.session.respond("买吗？")
        self.assertTrue(done)
        self.assertEqual(self.session.result, "reject")

    def test_respond_detects_result_followup(self):
        self.mock_llm.chat.side_effect = [
            "开场白",
            "先发资料看看吧。\n[RESULT: followup]",
        ]
        self.session.start()
        reply, done = self.session.respond("先了解下？")
        self.assertTrue(done)
        self.assertEqual(self.session.result, "followup")

    # ---- Transcript ----

    def test_get_transcript_returns_copy(self):
        self.mock_llm.chat.return_value = "测试"
        self.session.start()
        t1 = self.session.get_transcript()
        t1.append({"role": "extra", "content": "x"})
        t2 = self.session.get_transcript()
        self.assertNotEqual(len(t1), len(t2))

    # ---- 强制结束 ----

    def test_force_end(self):
        self.mock_llm.chat.return_value = "开场白"
        self.session.start()
        end_msg = self.session.force_end()
        self.assertTrue(self.session.is_finished)
        self.assertEqual(self.session.result, "followup")
        self.assertIn("对话结束", end_msg)

    # ---- 结果解析 ----

    def test_parse_result_success(self):
        done, result = self.session._parse_result("好的\n[RESULT: success]")
        self.assertTrue(done)
        self.assertEqual(result, "success")

    def test_parse_result_no_tag(self):
        done, result = self.session._parse_result("普通回复")
        self.assertFalse(done)
        self.assertIsNone(result)

    def test_strip_result_tag(self):
        clean = self.session._strip_result_tag("这是回复\n[RESULT: reject]")
        self.assertEqual(clean, "这是回复")


if __name__ == "__main__":
    unittest.main()
