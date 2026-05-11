"""
场景模拟对话引擎 —— AI 扮演客户，学员扮演销售。

客户 System Prompt 设计（核心）:
---
你是一位{name}，{occupation}。
你的性格特点：{personality}
你目前的顾虑：{pain_points}
你对推销员的初始态度：{initial_attitude}

规则：
- 你不知道对方在练习，你真实扮演这位客户
- 根据对方的话术质量自然地调整态度（好的话术让你有所触动，差的话术让你更抵触）
- 对话要自然，不要使用销售培训教材里的模板化语言
- 当以下情况发生时结束对话：
  a) 客户明确拒绝并挂断
  b) 客户同意下一步行动（留联系方式/预约见面）
  c) 对话超过 15 轮
- 对话结束时在最后一行输出：[RESULT: success|reject|followup]
---
"""

from typing import Optional
from core.llm_client import LLMClient

ATTITUDE_MAP = {
    "cold": "对方态度冷淡、对推销高度警惕，不想浪费时间。除非对方展现出真正的价值和诚意，否则你会尽快结束对话。",
    "neutral": "你持观望态度，不排斥也不热情。如果对方表现出专业、了解你的业务、提出了有针对性的建议，你会愿意多聊几句。",
    "warm": "你确实有需求，但在选供应商。你愿意听取介绍，不过会提出具体的顾虑和问题，考验对方的专业度。",
}


class RoleplaySession:
    """多轮角色扮演对话会话。

    用法:
        client = LLMClient()
        session = RoleplaySession(scenario, "learner_001", client)
        first_msg = session.start()
        while not session.is_finished:
            reply, done = session.respond(user_input)
    """

    MAX_ROUNDS = 15

    def __init__(self, scenario: dict, learner_id: str, llm_client: Optional[LLMClient] = None):
        self.scenario = scenario
        self.learner_id = learner_id
        self.llm = llm_client or LLMClient()
        self.transcript: list[dict] = []
        self.round = 0
        self.is_finished = False
        self.result: Optional[str] = None  # "success" | "reject" | "followup"

        profile = scenario.get("customer_profile", {})
        self.customer_name = profile.get("name", "客户")
        self._system_prompt = self._build_system_prompt()

    # ------------------------------------------------------------------
    # 公开方法
    # ------------------------------------------------------------------

    def start(self) -> str:
        """返回客户的开场白（AI 生成）。"""
        prompt = self._build_opening_prompt()
        opening = self.llm.chat(
            messages=[{"role": "user", "content": prompt}],
            system=self._system_prompt,
        )
        self.transcript.append({"role": "customer", "content": opening})
        self.round = 1
        return opening

    def respond(self, sales_message: str) -> tuple[str, bool]:
        """学员输入话术 → 返回 (客户AI回复, 对话是否结束)。

        如果客户回复末尾包含 [RESULT: ...]，自动解析并标记结束。
        """
        self.transcript.append({"role": "sales", "content": sales_message})
        self.round += 1

        # 构建对话历史用于 LLM 上下文
        history = self._format_history()
        user_prompt = (
            f"{history}\n\n"
            f"现在销售员说：「{sales_message}」\n\n"
            f"请以{self.customer_name}的身份回复。"
            f"这是第{self.round}轮对话（最多{self.MAX_ROUNDS}轮）。"
            f"记住：根据对方话术质量动态调整态度。"
        )

        reply = self.llm.chat(
            messages=[{"role": "user", "content": user_prompt}],
            system=self._system_prompt,
        )

        # 检查结束标记
        done, result = self._parse_result(reply)
        if done or self.round >= self.MAX_ROUNDS:
            self.is_finished = True
            self.result = result or "followup" if self.round >= self.MAX_ROUNDS else result

        clean_reply = self._strip_result_tag(reply)
        self.transcript.append({"role": "customer", "content": clean_reply})
        return clean_reply, self.is_finished

    def get_transcript(self) -> list[dict]:
        """返回完整对话记录。"""
        return list(self.transcript)

    def force_end(self) -> str:
        """强制结束对话（用于超时或主动退出场景）。"""
        self.is_finished = True
        self.result = "followup"
        end_msg = f"[对话结束 — 已进行 {self.round} 轮]"
        self.transcript.append({"role": "system", "content": end_msg})
        return end_msg

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _build_system_prompt(self) -> str:
        profile = self.scenario.get("customer_profile", {})
        pain_points = profile.get("pain_points", [])
        pains_str = "\n".join(f"  - {p}" for p in pain_points) if pain_points else "  （无明显顾虑）"
        attitude = profile.get("initial_attitude", "neutral")
        attitude_desc = ATTITUDE_MAP.get(attitude, ATTITUDE_MAP["neutral"])

        return (
            f"你是一位真实客户，正在和一位销售员对话。你不在演戏，你不知道这是练习。\n\n"
            f"## 你的身份\n"
            f"- 姓名：{profile.get('name', '客户')}\n"
            f"- 年龄：{profile.get('age', '未知')}\n"
            f"- 职业：{profile.get('occupation', '未知')}\n"
            f"- 性格：{profile.get('personality', '普通人')}\n\n"
            f"## 你的顾虑\n{pains_str}\n\n"
            f"## 你的初始态度\n{attitude_desc}\n\n"
            f"## 你的预算\n{profile.get('budget_hint', '不主动透露预算')}\n\n"
            f"## 对话规则\n"
            f"1. 根据销售员的话术质量动态调整态度：\n"
            f"   - 如果对方开场自然、问对问题、展现专业 → 你逐渐软化，愿意交流\n"
            f"   - 如果对方背诵话术、忽视你的顾虑、强行推销 → 你更加抵触\n"
            f"2. 对话自然真实，不要用销售教科书里的套话\n"
            f"3. 不要一来就说'我需要考虑'——给出具体的反馈和顾虑\n"
            f"4. 在以下情况结束对话，并在最后一行的末尾输出对应标记：\n"
            f"   a) 你明确拒绝并挂断 → 输出 [RESULT: reject]\n"
            f"   b) 你同意下一步行动（留联系方式/预约见面/试用）→ 输出 [RESULT: success]\n"
            f"   c) 对话超过{self.MAX_ROUNDS}轮仍未结果 → 输出 [RESULT: followup]\n"
            f"   d) 你愿意继续了解但需要时间（如接收资料）→ 输出 [RESULT: followup]\n"
            f"5. [RESULT: ...] 标记必须出现在回复的最后一行，不要在其他地方使用方括号标签。"
        )

    def _build_opening_prompt(self) -> str:
        attitude = self.scenario.get("customer_profile", {}).get("initial_attitude", "neutral")
        prompts = {
            "cold": "你正在忙工作，一个陌生销售突然找上门/打来电话。你很不耐烦，想尽快打发走。请以客户身份说开场白。",
            "neutral": "一位销售联系了你/来拜访你。你没特别的兴趣但也并不排斥，想看看对方要说什么。请以客户身份说开场白。",
            "warm": "你知道这家公司的大概业务，虽然不了解细节但确实有相关需求。销售联系你时，你愿意了解一下但会提出实际问题。请以客户身份说开场白。",
        }
        return prompts.get(attitude, prompts["neutral"])

    def _format_history(self) -> str:
        lines = ["## 当前对话记录"]
        for msg in self.transcript:
            role_label = f"客户-{self.customer_name}" if msg["role"] == "customer" else "销售员"
            lines.append(f"[{role_label}]: {msg['content']}")
        return "\n".join(lines)

    def _parse_result(self, reply: str) -> tuple[bool, Optional[str]]:
        """解析回复末尾的 [RESULT: ...] 标记。"""
        line = reply.strip().split("\n")[-1].strip()
        if line.startswith("[RESULT:") and line.endswith("]"):
            result = line[len("[RESULT:"):-1].strip()
            if result in ("success", "reject", "followup"):
                return True, result
        return False, None

    def _strip_result_tag(self, reply: str) -> str:
        """移除回复中的 [RESULT: ...] 标记行。"""
        lines = reply.strip().split("\n")
        if lines and lines[-1].strip().startswith("[RESULT:"):
            return "\n".join(lines[:-1]).strip()
        return reply
