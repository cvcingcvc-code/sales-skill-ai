"""
FastAPI Agent Skill 接口 — 将话术训练能力暴露为 HTTP API。

启动: uvicorn agent_skill.api_server:app --host 0.0.0.0 --port 8080

端点:
  POST /skill/invoke      — 主调用接口
  GET  /skill/health      — 健康检查
  GET  /skill/scenarios   — 列出可用场景
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException
from agent_skill.skill_interface import (
    SkillInput, SkillOutput, RoleplayResult,
    EvaluateResult, RecommendResult,
)
from core.skill_engine import SkillEngine

app = FastAPI(
    title="销售神队友 · Agent Skill",
    description="企业话术训练 AI — Agent Skill HTTP 接口",
    version="1.0.0",
)

engine = SkillEngine()

# 内存中缓存的活跃会话
_sessions: dict[str, dict] = {}


@app.get("/skill/health")
async def health():
    return {"status": "ok", "service": "sales-skill-ai", "version": "1.0.0"}


@app.get("/skill/scenarios")
async def list_scenarios(difficulty: str = None):
    scenarios = engine.list_available_scenarios(difficulty)
    return {"scenarios": scenarios, "count": len(scenarios)}


@app.post("/skill/invoke", response_model=SkillOutput)
async def invoke(input_data: SkillInput):
    action = input_data.action
    learner_id = input_data.learner_id or "api_user"

    try:
        if action == "evaluate":
            if not input_data.sales_text:
                raise HTTPException(400, "evaluate 操作需要 sales_text")
            scenario_id = input_data.scenario_id or "cold_visit_001"
            result = engine.evaluate_text(input_data.sales_text, scenario_id)
            return SkillOutput(
                success=True,
                action=action,
                result={
                    "total_score": result.total_score,
                    "dimensions": result.dimensions,
                    "strengths": result.strengths,
                    "weaknesses": result.weaknesses,
                    "suggestions": result.suggestions,
                    "recommended_practice": result.recommended_practice,
                },
            )

        elif action == "roleplay":
            scenario_id = input_data.scenario_id or "cold_visit_001"

            if input_data.session_id and input_data.session_id in _sessions:
                # 继续已有会话
                session_data = _sessions[input_data.session_id]
                engine._current_session = session_data["session"]
                engine._current_scenario = session_data["scenario"]
                engine._current_learner_id = learner_id

                reply, done = engine.respond(input_data.customer_message or "你好")

                if done:
                    score_result = engine.finish_session()
                    del _sessions[input_data.session_id]
                    return SkillOutput(
                        success=True,
                        action=action,
                        result={
                            "customer_reply": reply,
                            "is_finished": True,
                            "result": engine._current_session.result if engine._current_session else "followup",
                            "score": {
                                "total_score": score_result.total_score,
                                "dimensions": score_result.dimensions,
                            },
                        },
                    )

                return SkillOutput(
                    success=True,
                    action=action,
                    result={
                        "customer_reply": reply,
                        "is_finished": False,
                        "result": None,
                    },
                )
            else:
                # 新会话
                import uuid
                opening = engine.start_roleplay(scenario_id, learner_id)
                sid = str(uuid.uuid4())[:8]
                _sessions[sid] = {
                    "session": engine._current_session,
                    "scenario": engine._current_scenario,
                }
                return SkillOutput(
                    success=True,
                    action=action,
                    result={
                        "customer_reply": opening,
                        "is_finished": False,
                        "result": None,
                        "session_id": sid,
                    },
                )

        elif action == "recommend":
            report = engine.recommend_for_learner(learner_id)
            return SkillOutput(
                success=True,
                action=action,
                result={
                    "learner_id": learner_id,
                    "weak_areas": report.get("weak_areas", []),
                    "recommended_scenarios": report.get("recommended_scenarios", []),
                    "dimensions": report.get("progress", {}).get("dimensions", {}),
                    "total_sessions": report.get("progress", {}).get("total_sessions", 0),
                },
            )

        else:
            raise HTTPException(400, f"未知 action: {action}")

    except HTTPException:
        raise
    except Exception as e:
        return SkillOutput(success=False, action=action, result={}, error=str(e))
