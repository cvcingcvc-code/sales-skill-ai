"""
话术评分引擎 — LLM + 规则混合打分。

评分 Prompt 设计（核心）:
---
你是一位有 15 年经验的销售培训师。
请分析以下销售对话记录，从销售员的角度给出专业评估。

场景背景：{scenario.name}
销售目标：{scenario.sales_objective}

对话记录：
{transcript}

请严格按以下 JSON 格式输出评分（不要有任何其他文字）：
{score_schema}

评分标准：
- 开场（0-20分）：打招呼是否自然，是否快速建立好感，是否消除戒心
- 需求挖掘（0-20分）：是否问了对的问题，是否真的在听，是否找到了真实痛点
- 价值传递（0-20分）：是否用 FABE 结构，是否和客户需求对接，是否用了具体数字/案例
- 异议处理（0-20分）：是否先共情后解决，是否把价格异议转化为价值对话
- 推进促成（0-20分）：是否明确提出下一步，是否给客户行动理由

针对每个扣分点，必须引用对话中的具体片段，并给出改进后的示范话术。
---
"""

import json
import re
from dataclasses import dataclass, field, asdict
from typing import Optional
from core.llm_client import LLMClient


@dataclass
class ScoreResult:
    total_score: int = 0
    dimensions: dict[str, int] = field(default_factory=dict)
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    suggestions: list[dict] = field(default_factory=list)
    recommended_practice: list[str] = field(default_factory=list)
    raw_response: str = ""


class Scorer:
    """话术评分器，结合 LLM 分析和规则校准。

    用法:
        scorer = Scorer()
        result = scorer.score_session(transcript, scenario)
    """

    # 标准 5 维度
    STANDARD_DIMENSIONS = ["开场", "需求挖掘", "价值传递", "异议处理", "推进促成"]

    # 不同难度对总分的校准系数（让 beginner 场景更容易得高分）
    DIFFICULTY_BONUS = {
        "beginner": 1.15,
        "intermediate": 1.0,
        "hard": 0.85,
    }

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm = llm_client or LLMClient()

    def score_session(self, transcript: list[dict], scenario: dict) -> ScoreResult:
        """对一次训练会话进行评分。

        Args:
            transcript: [{"role": "customer/sales", "content": "..."}, ...]
            scenario: 场景定义 dict

        Returns:
            ScoreResult with total_score, dimensions, strengths, weaknesses, suggestions
        """
        # 只提取销售员的发言
        sales_lines = [m for m in transcript if m.get("role") == "sales"]
        customer_lines = [m for m in transcript if m.get("role") == "customer"]

        if not sales_lines:
            return ScoreResult(
                total_score=0,
                dimensions={d: 0 for d in self.STANDARD_DIMENSIONS},
                weaknesses=["未检测到销售话术"],
            )

        # 构建对话文本
        transcript_text = self._format_transcript(transcript)

        # 构建评分 prompt
        eval_dims = scenario.get("evaluation_dimensions", self.STANDARD_DIMENSIONS)
        prompt = self._build_scoring_prompt(transcript_text, scenario, eval_dims)

        # 调用 LLM 评分
        raw = self.llm.chat(
            messages=[{"role": "user", "content": prompt}],
            system="你是一位有15年经验的资深销售培训师。你的评分严格但建设性。你总是输出合法JSON。",
        )

        result = self._parse_score_response(raw, eval_dims)

        # 难度校准
        difficulty = scenario.get("difficulty", "intermediate")
        bonus = self.DIFFICULTY_BONUS.get(difficulty, 1.0)
        result.total_score = min(100, round(result.total_score * bonus))

        # 推荐练习话术
        result.recommended_practice = self._recommend_scripts(result.weaknesses)

        return result

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _format_transcript(self, transcript: list[dict]) -> str:
        lines = []
        for i, msg in enumerate(transcript, 1):
            role = "客户" if msg["role"] == "customer" else "销售员"
            lines.append(f"[第{i}轮 · {role}]: {msg['content']}")
        return "\n\n".join(lines)

    def _build_scoring_prompt(self, transcript_text: str, scenario: dict, eval_dims: list[str]) -> str:
        dim_names = "、".join(eval_dims)
        dim_scores_schema = {d: 0 for d in eval_dims}

        schema = {
            "total_score": 0,
            "dimensions": dim_scores_schema,
            "strengths": ["优点1", "优点2"],
            "weaknesses": ["弱点1", "弱点2"],
            "suggestions": [
                {
                    "issue": "描述具体问题（必须引用对话中的原句）",
                    "better_approach": "更好的处理方式",
                    "example": "示范话术示例",
                }
            ],
        }

        return f"""请分析以下销售对话记录，从销售员的角度给出专业评估。

场景名称：{scenario.get('name', '未知场景')}
场景难度：{scenario.get('difficulty', 'intermediate')}
销售目标：{scenario.get('sales_objective', '未指定')}

对话记录：
{transcript_text}

请严格按以下 JSON 格式输出评分（不要有任何 markdown 代码块标记，只输出纯 JSON）：

{json.dumps(schema, ensure_ascii=False, indent=2)}

评分标准（5 大维度，每项 0-20 分，总分 0-100）：
- {eval_dims[0] if len(eval_dims) > 0 else '开场'}（0-20分）：开场是否自然、破冰
- {eval_dims[1] if len(eval_dims) > 1 else '需求挖掘'}（0-20分）：是否问对了问题、是否在倾听
- {eval_dims[2] if len(eval_dims) > 2 else '价值传递'}（0-20分）：是否用 FABE 结构、是否有数据/案例支撑
- {eval_dims[3] if len(eval_dims) > 3 else '异议处理'}（0-20分）：是否先共情后解决
- {eval_dims[4] if len(eval_dims) > 4 else '推进促成'}（0-20分）：是否有明确的下一步推进

要求：
1. dimensions 的 key 必须使用上述维度名称：{dim_names}
2. 每个 suggestion 必须引用对话中的具体片段
3. example 必须给出可直接使用的示范话术
4. 评分严格但建设性，以帮助学员成长为出发点
5. 只输出 JSON，不要有任何解释文字或 markdown 代码块标记"""

    def _parse_score_response(self, raw: str, eval_dims: list[str]) -> ScoreResult:
        """解析 LLM 返回的 JSON 评分。"""
        # 尝试提取 JSON（处理可能被 markdown 包裹的情况）
        json_str = raw.strip()
        # 移除可能的 ```json ... ``` 包裹
        match = re.search(r'\{[\s\S]*\}', json_str)
        if match:
            json_str = match.group(0)

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            # 解析失败时返回基础结果
            return ScoreResult(
                total_score=50,
                dimensions={d: 50 for d in eval_dims},
                strengths=["（评分解析异常，请查看原始回复）"],
                weaknesses=["评分 JSON 解析失败"],
                suggestions=[{"issue": "评分系统异常", "better_approach": "请查看原始回复", "example": raw[:200]}],
                raw_response=raw,
            )

        return ScoreResult(
            total_score=data.get("total_score", 0),
            dimensions=data.get("dimensions", {}),
            strengths=data.get("strengths", []),
            weaknesses=data.get("weaknesses", []),
            suggestions=data.get("suggestions", []),
            raw_response=raw,
        )

    def _recommend_scripts(self, weaknesses: list[str]) -> list[str]:
        """根据弱点推荐话术库 ID。"""
        keyword_map = {
            "价格": ["obj_001", "obj_002"],
            "异议": ["obj_001", "obj_002", "obj_003", "obj_006"],
            "开场": ["pitch_001", "pitch_002"],
            "价值": ["pitch_003", "pitch_005", "pitch_006"],
            "竞品": ["obj_002", "pitch_004"],
            "拒绝": ["obj_003", "obj_004", "obj_011"],
            "成交": ["close_001", "close_002", "close_005"],
            "推进": ["close_001", "close_004", "close_008"],
            "需求": ["pitch_007"],
        }

        ids = set()
        for w in weaknesses:
            for kw, script_ids in keyword_map.items():
                if kw in w:
                    ids.update(script_ids)
        return list(ids)[:5]
