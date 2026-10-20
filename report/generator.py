"""训练报告生成器 — 基于 Jinja2 模板生成 Markdown 报告。"""

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader

from core import memory
from scenarios.scenario_loader import load_scenario

_TEMPLATES_DIR = Path(__file__).parent / "templates"


def generate_report(
    learner_id: str,
    scenario_id: Optional[str] = None,
    output_path: Optional[str] = None,
) -> str:
    """生成学员训练报告（Markdown 格式）。

    Args:
        learner_id: 学员 ID
        scenario_id: 指定场景 ID（可选，取最近一次）
        output_path: 输出文件路径（可选，不提供则返回字符串）

    Returns:
        Markdown 格式的报告文本
    """
    env = Environment(loader=FileSystemLoader(str(_TEMPLATES_DIR)))
    template = env.get_template("report.md.j2")

    learner = memory.get_learner(learner_id) or {"name": learner_id, "company": ""}
    progress = memory.get_learner_progress(learner_id)
    weak_areas = memory.get_weak_areas(learner_id)
    recommended_scenarios = memory.get_recommended_scenarios(learner_id)

    # 获取最近一次会话详情
    sessions = memory.get_learner_sessions(learner_id, limit=-1 if scenario_id else 1)
    latest = next((session for session in sessions
                   if not scenario_id or session.get("scenario_id") == scenario_id), None)
    scenario = load_scenario(latest["scenario_id"]) if latest else None
    scenario = scenario or {}

    import json
    score_data = json.loads(latest["score_json"]) if latest and latest.get("score_json") else {}

    # 解析结果状态
    result_status = "未完成"
    if latest:
        transcript = json.loads(latest["transcript_json"]) if latest.get("transcript_json") else []
        for msg in reversed(transcript):
            if msg.get("role") == "customer" and "RESULT:" in msg.get("content", ""):
                result_status = msg["content"]
                break
        else:
            result_status = "对话结束"

    ctx = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "learner": learner,
        "progress": progress,
        "scenario_name": scenario.get("name", latest.get("scenario_id", "未知") if latest else "无"),
        "difficulty": scenario.get("difficulty", "未知"),
        "sales_objective": scenario.get("sales_objective", "未知"),
        "result_status": result_status,
        "score_source": score_data.get("score_source", "legacy_invalid"),
        "score_status": score_data.get("score_status", "invalid"),
        "score_scale": memory.score_scale_info(score_data),
        "score_error": score_data.get("error") or ("历史评分未记录来源，不能作为已验证模型评分。" if score_data and not score_data.get("score_status") else ""),
        "calibration": score_data.get("calibration", {}),
        "total_score": score_data.get("total_score", "N/A"),
        "dimensions": score_data.get("dimensions", {}),
        "strengths": score_data.get("strengths", []),
        "weaknesses": score_data.get("weaknesses", []),
        "suggestions": score_data.get("suggestions", []),
        "recommended_practice": score_data.get("recommended_practice", []),
        "weak_areas": weak_areas,
        "recommended_scenarios": recommended_scenarios,
    }

    report_text = template.render(**ctx)

    if output_path:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(report_text)

    return report_text
