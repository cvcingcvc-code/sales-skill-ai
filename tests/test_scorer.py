"""Scorer 模块单元测试 — 测试 JSON 解析、难度校准、话术推荐。"""

import os
import sys
import json
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.scorer import Scorer, ScoreResult


SAMPLE_SCENARIO = {
    "id": "cold_visit_001",
    "name": "陌拜测试",
    "difficulty": "beginner",
    "sales_objective": "获得下次详谈机会",
    "evaluation_dimensions": ["开场破冰", "需求挖掘", "价值陈述", "异议处理", "推进下一步"],
}


class TestScorer(unittest.TestCase):

    def setUp(self):
        self.mock_llm = MagicMock()
        self.scorer = Scorer(self.mock_llm)

    # ---- JSON 解析 ----

    def test_parse_valid_json(self):
        raw = json.dumps({
            "total_score": 80,
            "dimensions": {"开场破冰": 16, "需求挖掘": 15, "价值陈述": 17, "异议处理": 16, "推进下一步": 16},
            "strengths": ["开场自然"],
            "weaknesses": ["价格处理弱"],
            "suggestions": [{"issue": "xxx", "better_approach": "yyy", "example": "zzz"}],
        })
        result = self.scorer._parse_score_response(raw, self.scorer.STANDARD_DIMENSIONS)
        self.assertEqual(result.total_score, 80)
        self.assertEqual(len(result.strengths), 1)
        self.assertEqual(len(result.suggestions), 1)

    def test_parse_json_with_markdown_wrapper(self):
        raw = '```json\n{"total_score": 75, "dimensions": {"开场": 15}, "strengths": [], "weaknesses": [], "suggestions": []}\n```'
        result = self.scorer._parse_score_response(raw, ["开场"])
        self.assertEqual(result.total_score, 75)

    def test_parse_invalid_json_returns_fallback(self):
        raw = "这不是JSON"
        result = self.scorer._parse_score_response(raw, ["开场"])
        self.assertEqual(result.total_score, 50)
        self.assertIn("JSON", result.weaknesses[0])
        self.assertEqual(result.raw_response, raw)

    # ---- 难度校准 ----

    def test_beginner_bonus(self):
        self.assertEqual(self.scorer.DIFFICULTY_BONUS["beginner"], 1.15)

    def test_hard_penalty(self):
        self.assertEqual(self.scorer.DIFFICULTY_BONUS["hard"], 0.85)

    def test_score_calibration_beginner(self):
        self.mock_llm.chat.return_value = json.dumps({
            "total_score": 70,
            "dimensions": {"开场破冰": 14, "需求挖掘": 14, "价值陈述": 14, "异议处理": 14, "推进下一步": 14},
            "strengths": [], "weaknesses": [], "suggestions": [],
        })
        result = self.scorer.score_session(
            [{"role": "sales", "content": "您好，请问需要什么帮助？"}],
            SAMPLE_SCENARIO,
        )
        # 70 * 1.15 = 80.5 → round to 81
        self.assertEqual(result.total_score, 80)

    def test_score_calibration_hard(self):
        hard_scenario = {**SAMPLE_SCENARIO, "difficulty": "hard"}
        self.mock_llm.chat.return_value = json.dumps({
            "total_score": 70,
            "dimensions": {"开场破冰": 14, "需求挖掘": 14, "价值陈述": 14, "异议处理": 14, "推进下一步": 14},
            "strengths": [], "weaknesses": [], "suggestions": [],
        })
        result = self.scorer.score_session(
            [{"role": "sales", "content": "您好"}],
            hard_scenario,
        )
        # 70 * 0.85 = 59.5 → round() = 60 (Python banker's rounding)
        self.assertEqual(result.total_score, 60)

    # ---- 空对话 ----

    def test_empty_sales_returns_zero(self):
        result = self.scorer.score_session([], SAMPLE_SCENARIO)
        self.assertEqual(result.total_score, 0)

    # ---- 推荐逻辑 ----

    def test_recommend_by_keyword(self):
        recs = self.scorer._recommend_scripts(["价格处理太弱", "开场不够自然"])
        self.assertTrue(len(recs) > 0)
        # 应该包含价格相关的推荐
        has_price = any("obj_001" in r or "obj_002" in r for r in recs)
        self.assertTrue(has_price)

    # ---- ScoreResult dataclass ----

    def test_score_result_defaults(self):
        sr = ScoreResult()
        self.assertEqual(sr.total_score, 0)
        self.assertEqual(sr.dimensions, {})
        self.assertEqual(sr.strengths, [])
        self.assertEqual(sr.suggestions, [])


if __name__ == "__main__":
    unittest.main()
