"""训练原始分、百分制画像及未知历史量纲的回归测试（不调用真实模型）。"""

import base64
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from rich.console import Console
from streamlit.testing.v1 import AppTest

from core import memory
from core.scorer import ScoreResult
from core.skill_engine import SkillEngine
from report.generator import generate_report


class ScoreScaleTestCase(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tempdir.name) / "scale.db")
        self.db_patch = patch.object(memory, "DB_PATH", self.db_path)
        self.db_patch.start()
        memory.init_db()
        memory.create_learner("learner", "测试学员")

    def tearDown(self):
        self.db_patch.stop()
        self.tempdir.cleanup()

    def save(self, dimensions=None, **overrides):
        payload = {
            "total_score": 80,
            "dimensions": dimensions or {"开场": 16},
            "score_source": "model",
            "score_status": "valid",
            "scale": {"dimension_max": 20, "total_max": 100},
        }
        payload.update(overrides)
        memory.save_session("learner", "cold_visit_001", payload, [])


class TestScoreScaleMemory(ScoreScaleTestCase):
    def test_explicit_twenty_point_scale_normalizes_without_mutating_raw_score(self):
        self.save({"开场": 16, "需求挖掘": 14})
        progress = memory.get_learner_progress("learner")
        self.assertEqual(progress["dimensions"], {"开场": 80, "需求挖掘": 70})
        self.assertEqual(progress["normalized_dimensions"], progress["dimensions"])
        self.assertEqual(progress["raw_dimensions"], {"开场": 16, "需求挖掘": 14})
        self.assertEqual(progress["provenance"]["开场"]["score_source"], "model")
        self.assertEqual(progress["scale"]["display_dimension_max"], 100)
        stored = json.loads(memory.get_learner_sessions("learner")[0]["score_json"])
        self.assertEqual(stored["dimensions"]["开场"], 16)
        self.assertEqual(stored["scale"]["dimension_max"], 20)

    def test_weakness_threshold_uses_percent_and_excludes_boundary_seventy(self):
        self.save({"开场": 16, "需求挖掘": 14, "异议处理": 13})
        self.assertEqual(memory.get_weak_areas("learner"), ["异议处理"])
        self.assertIn("objection_001", memory.get_recommended_scenarios("learner"))

    def test_explicit_hundred_point_scale_preserves_low_scores(self):
        self.save({"开场": 16}, scale={"dimension_max": 100, "total_max": 100})
        self.assertEqual(memory.get_learner_progress("learner")["dimensions"]["开场"], 16)
        self.assertEqual(memory.get_weak_areas("learner"), ["开场"])

    def test_legacy_low_scores_are_never_guessed_to_be_twenty_point_scale(self):
        memory.save_session("learner", "old", {"dimensions": {"开场": 16, "需求挖掘": 18}}, [])
        progress = memory.get_learner_progress("learner")
        self.assertEqual(progress["dimensions"], {"开场": 16, "需求挖掘": 18})
        self.assertEqual(progress["normalized_dimensions"], {})
        self.assertEqual(progress["provenance"]["开场"]["scale_status"], "legacy_unverified")
        self.assertEqual(memory.get_weak_areas("learner"), [])

    def test_legacy_hundred_point_examples_keep_previous_threshold_behavior(self):
        memory.save_session("learner", "old", {"dimensions": {"开场": 65, "需求挖掘": 80}}, [])
        progress = memory.get_learner_progress("learner")
        self.assertEqual(progress["dimensions"], {"开场": 65, "需求挖掘": 80})
        self.assertEqual(progress["normalized_dimensions"], {})
        self.assertEqual(memory.get_weak_areas("learner"), ["开场"])

    def test_invalid_scale_stays_unverified_and_is_not_used_for_recommendations(self):
        for scale in (None, {}, [], {"dimension_max": 0}, {"dimension_max": -20},
                      {"dimension_max": "20"}, {"dimension_max": True},
                      {"dimension_max": 25}, {"dimension_max": float("inf")},
                      {"dimension_max": 20, "total_max": 20}):
            with self.subTest(scale=scale):
                self.save(scale=scale)
                progress = memory.get_learner_progress("learner")
                self.assertEqual(progress["dimensions"]["开场"], 16)
                self.assertEqual(progress["normalized_dimensions"], {})
                self.assertEqual(progress["provenance"]["开场"]["scale_status"], "invalid_scale")
                self.assertEqual(memory.get_weak_areas("learner"), [])

    def test_out_of_range_raw_score_does_not_create_an_invalid_percent(self):
        self.save({"开场": 21})
        progress = memory.get_learner_progress("learner")
        self.assertEqual(progress["raw_dimensions"]["开场"], 21)
        self.assertEqual(progress["normalized_dimensions"], {})
        self.assertEqual(progress["provenance"]["开场"]["scale_status"], "invalid_scale")

    def test_nonfinite_or_nonnumeric_dimensions_fail_before_any_write(self):
        for value in (float("nan"), float("inf"), True, "16", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.save({"开场": value})
        self.assertEqual(memory.get_learner_sessions("learner"), [])

    def test_latest_score_metadata_tracks_each_dimension_independently(self):
        memory.save_session("learner", "old", {"dimensions": {"开场": 18, "需求挖掘": 15}}, [])
        self.save({"开场": 16}, score_source="rule")
        progress = memory.get_learner_progress("learner")
        self.assertEqual(progress["dimensions"], {"开场": 80, "需求挖掘": 15})
        self.assertEqual(progress["normalized_dimensions"], {"开场": 80})
        self.assertEqual(progress["provenance"]["开场"]["score_source"], "rule")
        self.assertEqual(progress["provenance"]["需求挖掘"]["scale_status"], "legacy_unverified")

    def test_existing_database_migration_retains_raw_scores(self):
        old_db = str(Path(self.tempdir.name) / "legacy.db")
        with sqlite3.connect(old_db) as conn:
            conn.execute("CREATE TABLE skills (learner_id TEXT, dimension TEXT, score REAL, updated_at TEXT, PRIMARY KEY (learner_id, dimension))")
            conn.execute("INSERT INTO skills VALUES ('learner', '开场', 18, '2026-01-01')")
        with patch.object(memory, "DB_PATH", old_db):
            memory.init_db()
            memory.init_db()  # 重复启动安全。
            progress = memory.get_learner_progress("learner")
        self.assertEqual(progress["dimensions"], {"开场": 18})
        self.assertEqual(progress["normalized_dimensions"], {})
        self.assertEqual(progress["provenance"]["开场"]["scale_status"], "legacy_unverified")

    def test_engine_persists_explicit_scale_for_model_and_rule_scores(self):
        for source in ("model", "rule"):
            with self.subTest(source=source):
                engine = SkillEngine(MagicMock())
                engine._current_session = MagicMock()
                engine._current_session.get_transcript.return_value = []
                engine._current_scenario = {"id": "cold_visit_001"}
                engine._current_learner_id = "learner"
                engine.scorer.score_session = MagicMock(return_value=ScoreResult(
                    total_score=80, dimensions={"开场": 16}, score_source=source, score_status="valid"))
                engine.finish_session()
                stored = json.loads(memory.get_learner_sessions("learner")[0]["score_json"])
                self.assertEqual(stored["scale"], {"dimension_max": 20, "total_max": 100})
                self.assertEqual(stored["score_source"], source)


class TestScoreScalePresentation(ScoreScaleTestCase):
    def test_report_names_raw_scale_normalized_score_and_source(self):
        self.save({"开场": 16})
        report = generate_report("learner")
        self.assertIn("原始分 0–20", report)
        self.assertIn("80 / 100", report)
        self.assertIn("16/20", report)
        self.assertIn("模型评分", report)

    def test_report_does_not_claim_legacy_dimensions_have_maximum_twenty(self):
        memory.save_session("learner", "old", {"total_score": 75, "dimensions": {"开场": 16}}, [])
        report = generate_report("learner")
        self.assertIn("legacy_unverified", report)
        self.assertIn("量纲未验证", report)
        self.assertNotIn("80 / 100", report)
        self.assertNotIn("16/20", report)

    def test_cli_progress_distinguishes_normalized_and_unverified_scores(self):
        from cli.main import progress_report
        memory.save_session("learner", "old", {"dimensions": {"需求挖掘": 18}}, [])
        self.save({"开场": 16})
        engine = SkillEngine(MagicMock())
        output = io.StringIO()
        with patch("cli.main.console", Console(file=output, force_terminal=False, width=150)):
            progress_report(engine, "learner")
        self.assertIn("80/100", output.getvalue())
        self.assertIn("原始 16/20", output.getvalue())
        self.assertIn("原始分 18", output.getvalue())
        self.assertIn("legacy_unverified", output.getvalue())

    def app(self):
        path = Path(__file__).resolve().parents[1] / "demo" / "app.py"
        app = AppTest.from_file(str(path), default_timeout=20)
        app.session_state["learner_id"] = "learner"
        app.session_state["workbench_admin_token"] = "test-admin-token"
        with patch.dict(os.environ, {"ADMIN_API_TOKEN": "test-admin-token"}):
            app.run()
        self.assertFalse(app.exception, app.exception)
        return app

    def test_streamlit_radar_uses_normalized_scores_and_range_zero_to_hundred(self):
        self.save({"开场": 16, "需求挖掘": 14})
        app = self.app()
        charts = app.get("plotly_chart")
        self.assertEqual(len(charts), 1)
        figure = json.loads(charts[0].proto.spec)
        self.assertEqual(figure["layout"]["polar"]["radialaxis"]["range"], [0, 100])
        values = figure["data"][0]["r"]
        if isinstance(values, dict):  # Plotly 6 将 numpy 数组编码为 typed array。
            import numpy as np
            values = np.frombuffer(base64.b64decode(values["bdata"]), dtype=values["dtype"])
        self.assertEqual(set(values), {70, 80})

    def test_streamlit_explains_legacy_scores_and_omits_unverified_radar(self):
        memory.save_session("learner", "old", {"dimensions": {"开场": 16}}, [])
        app = self.app()
        self.assertEqual(len(app.get("plotly_chart")), 0)
        self.assertTrue(any("legacy_unverified" in item.value for item in app.info))


if __name__ == "__main__":
    unittest.main()
