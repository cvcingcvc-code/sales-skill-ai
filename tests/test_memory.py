"""Memory 模块单元测试 — 测试 SQLite CRUD 操作。"""

import os
import sys
import json
import unittest
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 使用临时数据库
os.environ["DB_PATH"] = os.path.join(tempfile.gettempdir(), "test_learners.db")

from core import memory


class TestMemory(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        memory.init_db()

    def setUp(self):
        # 清理数据
        conn = memory._get_conn()
        conn.execute("DELETE FROM sessions")
        conn.execute("DELETE FROM skills")
        conn.execute("DELETE FROM learners")
        conn.commit()
        conn.close()

    # ---- 学员管理 ----

    def test_create_and_get_learner(self):
        memory.create_learner("u1", "张三", "ABC公司")
        learner = memory.get_learner("u1")
        self.assertEqual(learner["name"], "张三")
        self.assertEqual(learner["company"], "ABC公司")

    def test_create_duplicate_learner(self):
        memory.create_learner("u1", "张三", "")
        memory.create_learner("u1", "张三改", "")  # 不报错，IGNORE
        learner = memory.get_learner("u1")
        self.assertEqual(learner["name"], "张三")  # 保留第一条

    def test_list_learners(self):
        memory.create_learner("a", "A", "")
        memory.create_learner("b", "B", "")
        learners = memory.list_learners()
        self.assertEqual(len(learners), 2)

    def test_get_nonexistent_learner(self):
        self.assertIsNone(memory.get_learner("ghost"))

    # ---- 会话记录 ----

    def test_save_and_get_sessions(self):
        memory.create_learner("u1", "测试", "")
        score = {
            "total_score": 85,
            "dimensions": {"开场": 16, "需求挖掘": 14},
            "strengths": ["开场自然"],
            "weaknesses": ["价格处理弱"],
            "suggestions": [{"issue": "test", "better_approach": "test2", "example": "test3"}],
        }
        transcript = [
            {"role": "customer", "content": "你好"},
            {"role": "sales", "content": "您好王总"},
        ]

        sid = memory.save_session("u1", "cold_visit_001", score, transcript)

        sessions = memory.get_learner_sessions("u1")
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["id"], sid)
        self.assertEqual(sessions[0]["scenario_id"], "cold_visit_001")

        # 验证 JSON 存储
        stored_score = json.loads(sessions[0]["score_json"])
        self.assertEqual(stored_score["total_score"], 85)

    # ---- 能力画像 ----

    def test_learner_progress(self):
        memory.create_learner("u1", "测试", "")
        memory.save_session("u1", "cold_visit_001", {
            "total_score": 80,
            "dimensions": {"开场破冰": 16, "需求挖掘": 12},
        }, [])
        memory.save_session("u1", "follow_up_001", {
            "total_score": 90,
            "dimensions": {"开场破冰": 18, "需求挖掘": 15},
        }, [])

        progress = memory.get_learner_progress("u1")
        self.assertEqual(progress["total_sessions"], 2)
        self.assertEqual(len(progress["recent_scores"]), 2)
        # 最新分数覆盖
        self.assertEqual(progress["dimensions"]["开场破冰"], 18)
        self.assertEqual(progress["dimensions"]["需求挖掘"], 15)

    def test_weak_areas(self):
        memory.create_learner("u1", "测试", "")
        memory.save_session("u1", "s1", {
            "total_score": 60,
            "dimensions": {"开场破冰": 65, "需求挖掘": 80},
        }, [])

        weak = memory.get_weak_areas("u1", threshold=70)
        self.assertIn("开场破冰", weak)
        self.assertNotIn("需求挖掘", weak)

    def test_recommended_scenarios(self):
        memory.create_learner("u1", "测试", "")
        memory.save_session("u1", "s1", {
            "total_score": 50,
            "dimensions": {"异议处理": 40},
        }, [])

        recs = memory.get_recommended_scenarios("u1")
        self.assertIn("objection_001", recs)


if __name__ == "__main__":
    unittest.main()
