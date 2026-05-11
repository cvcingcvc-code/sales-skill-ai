# 销售神队友 · 企业话术训练 AI

> 用 AI 解决业务员线下推广能力参差不齐的问题

## 一句话介绍

一个可嵌入企业内训流程的 AI Skill：新员工用它学标准话术、练角色扮演、获评分反馈；封装后作为 Agent Skill，可被自动化培训流程直接调用。

## 问题陈述

线下销售团队面临：
- 新员工话术学习周期长（靠老人带，效率低）
- 客户异议处理能力弱（遇到压价、比价就慌）
- 培训效果无法量化（不知道哪个环节最弱）

## 解决方案

三个核心能力模块：
1. **话术知识库** — JSON 格式，可企业定制，涵盖开场/价值/异议/成交，共 36 条
2. **场景模拟对话** — AI 扮演客户（含客户画像、动态配合度），多轮真实对话
3. **评分 & 反馈** — 5 维度打分，引用具体对话片段，给出可执行改进建议

## 技术实现

- **核心模型**：DeepSeek（当前）/ Google Gemini 1.5 Pro（可切换）/ Anthropic Claude（可切换）
- **架构**：Python 后端 + FastAPI Agent Skill 接口 + Streamlit 演示 UI
- **数据**：SQLite 学员进度 + JSON 话术库（可企业定制）

## 使用工具

- Google Cloud · Gemini Enterprise Agent Platform
- Anthropic Claude API
- DeepSeek API
- Python / FastAPI / Streamlit / SQLite

## 快速开始

### 1. 配置 API Key

```bash
cp .env.example .env
# 编辑 .env 填入 API Key
```

当前支持的 Provider（通过 `LLM_PROVIDER` 切换）：

| Provider | 环境变量 | 模型 |
|----------|----------|------|
| deepseek | `DEEPSEEK_API_KEY` | `deepseek-chat` |
| gemini | `GOOGLE_API_KEY` | `gemini-1.5-pro` |
| claude | `ANTHROPIC_API_KEY` | `claude-sonnet-4-20250514` |

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 启动

```bash
# 交互式 CLI
python -m cli.main

# Streamlit Web UI
streamlit run demo/app.py

# FastAPI Agent Skill 接口
uvicorn agent_skill.api_server:app --port 8080
```

## 项目结构

```
sales-skill-ai/
├── core/
│   ├── llm_client.py       # 统一 LLM 调用层（3 provider）
│   ├── roleplay.py         # AI 客户角色扮演引擎
│   ├── scorer.py           # 5 维度话术评分
│   ├── skill_engine.py     # 训练主引擎
│   └── memory.py           # 学员进度记忆（SQLite）
├── data/scripts/
│   ├── objections.json     # 异议处理话术 × 12
│   ├── product_pitch.json  # 产品介绍话术 × 12
│   └── closing.json        # 成交跟进话术 × 12
├── scenarios/
│   ├── scenario_loader.py
│   └── templates/          # 3 种场景 YAML
├── agent_skill/
│   ├── skill_interface.py  # Pydantic I/O 模型
│   └── api_server.py       # FastAPI 服务
├── cli/main.py             # Rich 交互式 CLI
├── demo/app.py             # Streamlit 演示 UI
├── report/generator.py     # 训练报告生成
└── tests/                  # 单元测试
```

## 核心 Prompt 设计

### Prompt 1：客户角色扮演 System Prompt

```
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
```

### Prompt 2：话术评分 Prompt

```
你是一位有 15 年经验的销售培训师。
请分析以下销售对话记录，从销售员的角度给出专业评估。

场景背景：{scenario.name}
销售目标：{scenario.sales_objective}

对话记录：
{transcript}

评分标准（5 维度，每项 0-20 分）：
- 开场：打招呼是否自然，是否快速建立好感
- 需求挖掘：是否问对了问题，是否找到了真实痛点
- 价值传递：是否用 FABE 结构，是否用了具体数据/案例
- 异议处理：是否先共情后解决，是否转化价格异议
- 推进促成：是否明确提出下一步，是否给客户行动理由

针对每个扣分点，必须引用对话中的具体片段，并给出改进后的示范话术。
输出严格 JSON 格式。
```

### Prompt 3：个性化推荐 Prompt

```
学员{learner_name}的最近训练数据：
{recent_scores}

能力短板：{weak_dimensions}

请给出：
1. 一句话点评（鼓励为主，具体指出最关键问题）
2. 本周重点练习方向（不超过2个）
3. 推荐的 3 个练习场景 ID
4. 一条本周的"话术金句"
```

## 处理流程

```
学员输入话术
    │
    ▼
┌─────────────┐    ┌──────────────┐
│ AI 客户扮演  │───▶│ 5 维度评分   │
│ (动态态度)   │    │ (LLM+规则)   │
└─────────────┘    └──────────────┘
                          │
                          ▼
                   ┌──────────────┐
                   │ 个性化推荐    │
                   │ 弱点 → 场景   │
                   │ 弱点 → 话术   │
                   └──────────────┘
                          │
                          ▼
                   ┌──────────────┐
                   │ SQLite 存储   │
                   │ 学员进度追踪  │
                   └──────────────┘
```

## 降级策略

LLM 调用失败时自动 fallback：

```
deepseek → gemini → claude
或 gemini → deepseek → claude
或 claude → deepseek → gemini
```

每个 provider 最多重试 3 次（指数退避 1s/2s/4s），全部失败后抛出异常。

## 当前进展

- [x] 话术知识库（3类，共36条）
- [x] 场景模拟对话（陌拜/回访/异议3种场景，含动态客户态度）
- [x] 评分系统（5维度 + 具体片段引用 + 示范话术）
- [x] CLI 交互界面（Rich，新员工/实战/进度报告）
- [x] FastAPI Agent Skill 接口（evaluate/roleplay/recommend）
- [x] 学员进度记忆（SQLite + 能力画像 + 弱点推荐）
- [x] 训练报告生成（Jinja2 → Markdown）
- [x] 单元测试
- [x] Streamlit 演示 UI（含雷达图）
