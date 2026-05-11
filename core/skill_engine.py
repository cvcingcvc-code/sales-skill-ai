"""
话术 Skill 主引擎 — 串联角色扮演 → 评分 → 记忆存储的完整训练流程。

用法:
    engine = SkillEngine()
    session = engine.start_roleplay("cold_visit_001", "learner_001")
    reply, done = engine.respond("你好王总...")
    if done:
        result = engine.finish_session()
"""

from typing import Optional
from core.llm_client import LLMClient
from core.roleplay import RoleplaySession
from core.scorer import Scorer, ScoreResult
from core import memory
from scenarios.scenario_loader import load_scenario, list_scenarios


class SkillEngine:
    """销售话术训练主引擎。

    三个核心模式：
    - 新员工模式：浏览话术库 + AI 出题测验
    - 实战演练模式：场景角色扮演 + 评分反馈
    - Agent Skill 模式：标准化输入输出（供外部调用）
    """

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm = llm_client or LLMClient()
        self.scorer = Scorer(self.llm)
        self._current_session: Optional[RoleplaySession] = None
        self._current_scenario: Optional[dict] = None
        self._current_learner_id: Optional[str] = None

        # 确保数据库表存在
        memory.init_db()

    # ------------------------------------------------------------------
    # 新员工模式
    # ------------------------------------------------------------------

    def get_scripts_by_category(self, category: str) -> list[dict]:
        """按类别获取话术库。category: objections / product_pitch / closing"""
        import json
        from pathlib import Path

        scripts_dir = Path(__file__).parent.parent / "data" / "scripts"
        filepath = scripts_dir / f"{category}.json"
        if not filepath.exists():
            return []

        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("items", [])

    def generate_quiz(self, category: str, count: int = 3) -> list[dict]:
        """AI 基于话术库出题。返回 [{"question": ..., "answer_hint": ...}]。"""
        scripts = self.get_scripts_by_category(category)
        if not scripts:
            return []

        # 随机挑选几条作为出题素材
        import random
        samples = random.sample(scripts, min(count, len(scripts)))
        scripts_text = "\n\n".join(
            f"话术ID: {s['id']}\n场景: {s.get('scenario', s.get('context', ''))}\n要点: {', '.join(s.get('key_points', []))}"
            for s in samples
        )

        prompt = f"""基于以下话术库内容，出 {count} 道情景测试题。

话术库摘要：
{scripts_text}

每道题应描述一个具体的销售场景，让学员选择最佳应对话术。输出 JSON 数组：
[{{"question": "场景描述...", "options": ["A. ...", "B. ...", "C. ...", "D. ..."], "correct": "A", "explanation": "解释为什么A最好"}}]

只输出 JSON 数组，不要其他内容。"""

        raw = self.llm.chat(
            messages=[{"role": "user", "content": prompt}],
            system="你是销售培训出题专家，出的题目专业且有区分度。只输出合法JSON。",
        )

        import json as _json
        import re
        match = re.search(r'\[[\s\S]*\]', raw.strip())
        if match:
            try:
                return _json.loads(match.group(0))
            except _json.JSONDecodeError:
                pass
        return [{"question": "出题异常", "options": [], "correct": "", "explanation": raw}]

    # ------------------------------------------------------------------
    # 实战演练模式
    # ------------------------------------------------------------------

    def list_available_scenarios(self, difficulty: Optional[str] = None) -> list[dict]:
        """列出可用场景。"""
        return list_scenarios(difficulty)

    def start_roleplay(self, scenario_id: str, learner_id: str) -> str:
        """启动角色扮演 → 返回客户开场白。"""
        scenario = load_scenario(scenario_id)
        if not scenario:
            raise ValueError(f"场景不存在: {scenario_id}")

        # 确保学员存在
        memory.create_learner(learner_id, learner_id)

        self._current_scenario = scenario
        self._current_learner_id = learner_id
        self._current_session = RoleplaySession(scenario, learner_id, self.llm)

        return self._current_session.start()

    def respond(self, sales_message: str) -> tuple[str, bool]:
        """学员输入话术 → (客户回复, 是否结束)。"""
        if not self._current_session:
            raise RuntimeError("没有活跃的会话，请先调用 start_roleplay()")
        return self._current_session.respond(sales_message)

    def finish_session(self) -> ScoreResult:
        """结束当前会话，评分并保存。"""
        if not self._current_session:
            raise RuntimeError("没有活跃的会话")

        if not self._current_session.is_finished:
            self._current_session.force_end()

        transcript = self._current_session.get_transcript()
        scenario = self._current_scenario or {}

        result = self.scorer.score_session(transcript, scenario)

        # 保存到数据库
        memory.save_session(
            learner_id=self._current_learner_id or "anonymous",
            scenario_id=scenario.get("id", "unknown"),
            score_result={
                "total_score": result.total_score,
                "dimensions": result.dimensions,
                "strengths": result.strengths,
                "weaknesses": result.weaknesses,
                "suggestions": result.suggestions,
            },
            transcript=transcript,
        )

        return result

    def force_end_session(self) -> ScoreResult:
        """强制结束并评分。"""
        return self.finish_session()

    @property
    def is_in_session(self) -> bool:
        return self._current_session is not None and not self._current_session.is_finished

    @property
    def current_round(self) -> int:
        return self._current_session.round if self._current_session else 0

    # ------------------------------------------------------------------
    # 进度查询
    # ------------------------------------------------------------------

    def get_progress_report(self, learner_id: str) -> dict:
        """获取学员进度与能力画像。"""
        learner = memory.get_learner(learner_id)
        progress = memory.get_learner_progress(learner_id)
        weak = memory.get_weak_areas(learner_id)
        recommended = memory.get_recommended_scenarios(learner_id)

        return {
            "learner": learner,
            "progress": progress,
            "weak_areas": weak,
            "recommended_scenarios": recommended,
        }

    # ------------------------------------------------------------------
    # Agent Skill 接口
    # ------------------------------------------------------------------

    def evaluate_text(self, sales_text: str, scenario_id: str) -> ScoreResult:
        """直接评估一段话术（无角色扮演上下文）。

        用于 Agent Skill 的 evaluate action。
        """
        scenario = load_scenario(scenario_id) or {"name": "独立评估"}
        fake_transcript = [
            {"role": "customer", "content": "（客户表达了顾虑）"},
            {"role": "sales", "content": sales_text},
        ]
        return self.scorer.score_session(fake_transcript, scenario)

    def recommend_for_learner(self, learner_id: str) -> dict:
        """基于学员弱点给出个性化推荐。"""
        return self.get_progress_report(learner_id)
