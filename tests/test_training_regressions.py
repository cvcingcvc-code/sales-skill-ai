"""真实训练评分、安全边界与难度筛选的回归测试；模型响应均为显式 Mock。"""

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from rich.console import Console

from core import memory
from core.llm_client import LLMError
from core.roleplay import RoleplaySession
from core.scorer import Scorer, ScoreResult, ScoringError
from core.skill_engine import SkillEngine
from report.generator import generate_report
from scenarios.scenario_loader import list_scenarios, load_scenario


SCENARIO = {
    "id": "cold_visit_001",
    "name": "测试场景",
    "difficulty": "beginner",
    "evaluation_dimensions": Scorer.STANDARD_DIMENSIONS,
}
TRANSCRIPT = [{"role": "sales", "content": "您好，希望先了解您的需求。"}]


def score_payload(total=70):
    return {
        "total_score": total,
        "dimensions": {name: total // 5 for name in Scorer.STANDARD_DIMENSIONS},
        "strengths": ["有需求提问"],
        "weaknesses": ["价格说明可加强"],
        "suggestions": [{"issue": "缺少例子", "better_approach": "补充例子", "example": "例如…"}],
    }


class TestScoringValidation(unittest.TestCase):
    def setUp(self):
        self.llm = MagicMock()
        self.scorer = Scorer(self.llm)

    def test_valid_model_score_has_explicit_source_and_calibration(self):
        self.llm.chat.return_value = json.dumps(score_payload())
        result = self.scorer.score_session(TRANSCRIPT, SCENARIO)
        self.assertEqual(result.score_source, "model")
        self.assertEqual(result.score_status, "valid")
        self.assertEqual(result.total_score, 80)
        self.assertEqual(result.calibration["model_total_score"], 70)
        self.assertEqual(result.calibration["multiplier"], 1.15)
        self.assertEqual(sum(result.dimensions.values()), 70)
        self.assertIsNone(result.error)

    def test_empty_or_blank_sales_is_explicit_rule_score(self):
        for transcript in ([], [{"role": "customer", "content": "您好"}],
                           [{"role": "sales", "content": "  "}]):
            with self.subTest(transcript=transcript):
                result = self.scorer.score_session(transcript, SCENARIO)
                self.assertEqual((result.total_score, result.score_source, result.score_status), (0, "rule", "valid"))
        self.llm.chat.assert_not_called()

    def test_legacy_parser_fallback_is_marked_invalid_and_has_no_fake_dimensions(self):
        result = self.scorer._parse_score_response("not JSON", ["开场"])
        self.assertEqual(result.total_score, 50)  # 旧私有接口兼容，不能被实际会话使用。
        self.assertEqual(result.score_status, "invalid")
        self.assertEqual(result.score_source, "legacy_invalid")
        self.assertEqual(result.dimensions, {})
        self.assertEqual(result.error, ScoringError.public_message)

    def test_invalid_json_never_turns_into_a_real_score(self):
        self.llm.chat.return_value = "not JSON secret-should-not-appear"
        with self.assertRaises(ScoringError) as raised:
            self.scorer.score_session(TRANSCRIPT, SCENARIO)
        self.assertNotIn("secret-should-not-appear", str(raised.exception))
        self.assertEqual(raised.exception.code, "invalid_score")

    def test_schema_types_ranges_names_and_total_are_validated(self):
        mutations = [
            {"total_score": "70"}, {"total_score": True}, {"total_score": -1},
            {"total_score": 101}, {"total_score": 71}, {"total_score": 70.0},
            {"dimensions": {"开场": 21}}, {"dimensions": {"开场": -1}},
            {"dimensions": {"开场": True}}, {"dimensions": {"开场": "14"}},
            {"dimensions": {}}, {"dimensions": []},
            {"strengths": "good"}, {"strengths": [False]},
            {"weaknesses": [1]}, {"suggestions": [{}]},
            {"suggestions": [{"issue": "x", "better_approach": "y", "example": 1}]},
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.llm.chat.return_value = json.dumps({**score_payload(), **mutation})
                with self.assertRaises(ScoringError):
                    self.scorer.score_session(TRANSCRIPT, SCENARIO)
        for raw in ("[]", "null", '{"total_score": 1, "total_score": 70}',
                    '{"total_score": NaN}', json.dumps({"total_score": 70})):
            with self.subTest(raw=raw):
                self.llm.chat.return_value = raw
                with self.assertRaises(ScoringError):
                    self.scorer.score_session(TRANSCRIPT, SCENARIO)

    def test_model_call_failure_is_propagated_without_scoring(self):
        failure = RuntimeError("transport unavailable")
        self.llm.chat.side_effect = failure
        with self.assertRaises(RuntimeError) as raised:
            self.scorer.score_session(TRANSCRIPT, SCENARIO)
        self.assertIs(raised.exception, failure)


class TestTrainingPersistence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(memory, "DB_PATH", str(Path(self.tmp.name) / "learners.db"))
        self.db_patch.start()
        self.llm = MagicMock()
        self.engine = SkillEngine(self.llm)
        memory.create_learner("test", "test")
        session = RoleplaySession(SCENARIO, "test", self.llm)
        session.transcript = list(TRANSCRIPT)
        session.is_finished = True
        self.engine._current_session = session
        self.engine._current_scenario = SCENARIO
        self.engine._current_learner_id = "test"

    def tearDown(self):
        self.db_patch.stop()
        self.tmp.cleanup()

    def test_invalid_score_is_not_persisted(self):
        self.llm.chat.return_value = "invalid JSON"
        with self.assertRaises(ScoringError):
            self.engine.finish_session()
        self.assertEqual(memory.get_learner_sessions("test"), [])
        self.assertEqual(memory.get_learner_progress("test")["dimensions"], {})

    def test_legacy_invalid_result_cannot_bypass_storage_guard(self):
        self.engine.scorer.score_session = MagicMock(return_value=ScoreResult(total_score=50))
        with self.assertRaises(ScoringError):
            self.engine.finish_session()
        self.assertEqual(memory.get_learner_sessions("test"), [])

    def test_saved_score_includes_provenance_and_calibration(self):
        self.llm.chat.return_value = json.dumps(score_payload())
        self.engine.finish_session()
        stored = json.loads(memory.get_learner_sessions("test")[0]["score_json"])
        self.assertEqual(stored["score_source"], "model")
        self.assertEqual(stored["score_status"], "valid")
        self.assertIsNone(stored["error"])
        self.assertEqual(stored["calibration"]["model_total_score"], 70)
        self.assertIn("recommended_practice", stored)
        self.assertNotIn("raw_response", stored)

    def test_report_honors_selected_scenario_and_shows_provenance(self):
        for sid, total in (("cold_visit_001", 70), ("follow_up_001", 80)):
            data = {**score_payload(total), "score_source": "model", "score_status": "valid"}
            memory.save_session("test", sid, data, [])
        report = generate_report("test", scenario_id="cold_visit_001")
        self.assertIn("模型评分", report)
        self.assertIn("**70**", report)
        self.assertNotIn("**80**", report)
        report_missing = generate_report("test", scenario_id="missing")
        self.assertIn("无有效评分", report_missing)

    def test_legacy_report_does_not_present_unknown_provenance_as_model_score(self):
        memory.save_session("test", "cold_visit_001", score_payload(), [])
        report = generate_report("test")
        self.assertIn("历史或无效评分", report)
        self.assertIn("无有效评分", report)
        self.assertNotIn("**70**", report)

    def test_script_category_is_allowlisted(self):
        self.assertTrue(self.engine.get_scripts_by_category("objections"))
        self.assertEqual(self.engine.get_scripts_by_category("../scripts/objections"), [])
        self.assertEqual(self.engine.get_scripts_by_category("/tmp/private"), [])


class TestRoleplayFailureRecovery(unittest.TestCase):
    def test_failed_model_turn_keeps_transcript_and_round_for_retry(self):
        llm = MagicMock()
        llm.chat.side_effect = ["您好", RuntimeError("timeout"), "请继续"]
        session = RoleplaySession(SCENARIO, "test", llm)
        session.start()
        with self.assertRaises(RuntimeError):
            session.respond("希望了解您的需求")
        self.assertEqual(session.round, 1)
        self.assertEqual(len(session.transcript), 1)
        session.respond("希望了解您的需求")
        self.assertEqual(session.round, 2)
        self.assertEqual(len(session.transcript), 3)

    def test_finished_session_rejects_more_turns(self):
        llm = MagicMock()
        session = RoleplaySession(SCENARIO, "test", llm)
        session.force_end()
        with self.assertRaises(RuntimeError):
            session.respond("继续")
        llm.chat.assert_not_called()


class TestScenarioBoundaries(unittest.TestCase):
    def test_difficulty_filter_returns_only_matching_scenarios(self):
        all_scenarios = list_scenarios()
        self.assertTrue(all_scenarios)
        for difficulty in {s["difficulty"] for s in all_scenarios}:
            with self.subTest(difficulty=difficulty):
                filtered = list_scenarios(difficulty)
                self.assertTrue(filtered)
                self.assertTrue(all(s["difficulty"] == difficulty for s in filtered))
        self.assertEqual(list_scenarios("nonexistent"), [])

    def test_scenario_loading_allows_known_id_and_stem_without_path_access(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            templates = root / "templates"
            templates.mkdir()
            (templates / "demo.yaml").write_text("id: known\nname: safe\n", encoding="utf-8")
            (root / "outside.yaml").write_text("id: outside\nname: private\n", encoding="utf-8")
            with patch("scenarios.scenario_loader._TEMPLATES_DIR", templates):
                self.assertEqual(load_scenario("known")["name"], "safe")
                self.assertEqual(load_scenario("demo")["name"], "safe")
                self.assertIsNone(load_scenario("../outside"))
                self.assertIsNone(load_scenario(str(root / "outside")))

    def test_cli_does_not_print_raw_model_error_or_invalid_fallback_score(self):
        from cli.main import show_model_error, show_score
        output = io.StringIO()
        with patch("cli.main.console", Console(file=output, force_terminal=False)):
            show_model_error(LLMError("secret-should-not-appear"))
            show_score(Scorer(MagicMock())._parse_score_response("invalid", ["开场"]))
        self.assertNotIn("secret-should-not-appear", output.getvalue())
        self.assertNotIn("总分: 50", output.getvalue())
        self.assertIn("无效评分", output.getvalue())


if __name__ == "__main__":
    unittest.main()
