"""标准化 Skill I/O 定义 — Pydantic 模型。

用于外部 Agent 调用：
- evaluate: 评估一段话术文本
- roleplay: 获取客户对话响应
- recommend: 获取学员个性化推荐
"""

from typing import Optional
from pydantic import BaseModel, Field, ConfigDict


class SkillInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    action: str = Field(..., description="操作类型: evaluate | roleplay | recommend")
    learner_id: Optional[str] = Field(None, min_length=1, max_length=100, description="学员 ID")
    sales_text: Optional[str] = Field(None, max_length=10000, description="evaluate 时必填：要评估的话术文本")
    scenario_id: Optional[str] = Field(None, max_length=100, description="roleplay 或 evaluate 时必填")
    customer_message: Optional[str] = Field(None, max_length=2000, description="roleplay 时：客户当前消息")
    session_id: Optional[str] = Field(None, max_length=100, description="roleplay 时：会话 ID（用于继续对话）")


class RoleplayResult(BaseModel):
    customer_reply: str = Field("", description="客户 AI 的回复")
    is_finished: bool = Field(False, description="对话是否结束")
    result: Optional[str] = Field(None, description="success | reject | followup")


class EvaluateResult(BaseModel):
    total_score: int = 0
    dimensions: dict[str, int] = Field(default_factory=dict)
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    suggestions: list[dict] = Field(default_factory=list)
    recommended_practice: list[str] = Field(default_factory=list)


class RecommendResult(BaseModel):
    learner_id: str = ""
    weak_areas: list[str] = Field(default_factory=list)
    recommended_scenarios: list[str] = Field(default_factory=list)
    dimensions: dict[str, int] = Field(default_factory=dict)
    total_sessions: int = 0


class SkillOutput(BaseModel):
    success: bool = True
    action: str = ""
    result: dict = Field(default_factory=dict)
    error: Optional[str] = None
