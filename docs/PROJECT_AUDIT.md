# AI Sales Agent V1：改造前审计

审计日期：2026-10-15。先检查现有实现，再进行增量改造；本报告记录原始基线，不将计划写成已实现功能。

## Git 与验证基线

- 仓库：`github.com/cvcingcvc-code/sales-skill-ai`（origin，HTTPS）。
- 原分支：`work`；原 HEAD 与 `origin/master` 均为 `31671938256792948c5c1cc57f018254acec7386`。
- 开始时跟踪和未跟踪文件均干净；保留原 checkout、提交和已有虚拟环境。
- 本轮开发分支：`codex/ai-sales-agent-v1`，不覆盖 master 或其他分支。
- 原始测试命令：`.venv/bin/python -m unittest discover -s tests -v`，40 项通过；未调用真实模型。
- Python 3.12。原 requirements 缺少直接导入的 `httpx`、`plotly`；此前仅由环境补装，没有仓库锁文件或 CI。
- 目前没有 DeepSeek、Google、Anthropic API Key 或管理员 token。真实模型验收 **BLOCKED**。先前访问 DeepSeek 的代理 CONNECT 返回 403；当前运行配置没有该目的地，不能把配置草稿视作已生效。

## 目录、入口与职责

| 模块 | 原有职责 | 复用方式 |
| --- | --- | --- |
| `core/llm_client.py` | DeepSeek/Gemini/Claude、重试和自动 fallback | 保留训练兼容接口，引入统一 ModelProvider 网关 |
| `core/roleplay.py` | 客户人设、多轮训练、结束标签 | 保留销售模拟，与真实客服会话分离 |
| `core/scorer.py` | 模型 JSON 评分、难度系数、话术推荐 | 明确模型/规则来源，拒绝把解析失败视作成功 |
| `core/skill_engine.py` | 学习、模拟、评分、进度编排 | 保留旧训练流程 |
| `core/memory.py` | SQLite `learners/sessions/skills`，WAL、参数化 SQL | 保留学员数据；客服用独立表和数据访问层 |
| `scenarios/` | 3 个 YAML 场景及难度过滤 | 原加载器增量修复输入边界 |
| `data/scripts/` | 3 类销售话术 JSON | 继续用于培训，不当作商品或售后政策 |
| `agent_skill/api_server.py` | FastAPI 训练接口、内存会话 | 加鉴权和会话隔离，挂载客服路由 |
| `cli/main.py` | 命令行培训、演练、进度 | 保留本地交互，补充评分来源与失败提示 |
| `demo/app.py` | Streamlit 培训演示、知识学习、雷达报告 | 修复筛选，增加必要管理入口 |
| `report/generator.py` | Jinja2 Markdown 训练报告 | 保留，展示评分来源 |
| `tests/` | 40 项 unittest（9 模型、8 存储、13 对话、10 评分） | 全部保留并新增回归测试 |
| `test_gemini.py` | 真实模型连通性手工脚本 | 不属于 pytest fixtures；凭据缺失不能通过 |

## 已确认的问题

### 模型与结果真实性

1. DeepSeek 的 Chat API 地址在 `_call_deepseek`、`_deepseek_stream` 两处硬编码；模型虽然可用环境变量覆盖，Gemini/Claude 默认名称已老旧。
2. 缺少密钥仍发送空 Bearer 并重试三次；日志和 API 暴露底层异常字符串，存在敏感信息泄漏风险。
3. 流式生成器开始迭代后的异常绕过外层重试；fallback 的实际 provider、token、费用没有明确展示。
4. `Scorer._parse_score_response` 非 JSON 时构造 50 分、每维 50 分，随后仍进行难度加成和存档，API 返回成功。它不是有效模型评分，也不是独立规则评分。
5. 合法 JSON 缺少严格 schema、类型和范围检查。原空销售文本的 0 分可以解释为规则结果；原难度系数只是模型分数校准。

### 难度筛选

`demo/app.py` 先调用无参数 `engine.list_available_scenarios()` 建立全部选项，随后读取 `diff`，却从未使用该值。底层加载器、引擎、CLI 和 API 已支持难度参数。修复应先读取选择，再筛选并处理空列表，无需重写场景系统。

### 权限、数据与可靠性

- API 无鉴权；任意 learner_id 可读取进度，仅凭 8 位 session_id 就能续聊并替换学员归属。
- 多个会话共享可变全局 engine，存在串话风险；异步端点直接执行同步模型请求会阻塞事件循环。
- `scenario_loader` 文件名 fallback 和话术分类路径拼接缺少路径边界检查。
- 用户文本直接混入训练 prompt。新增客服必须将消息/文档视为不可信数据，不能赋予其系统指令或工具权限。
- 每维评分满分 20，薄弱项默认阈值却是 70，雷达图按 100 展示；量纲不一致。
- SQLite 外键声明未启用校验；报告参数 scenario_id 未实际使用，部分场景元数据始终为“未知”。

### 密钥检查

当前跟踪文件扫描未发现真实密钥字面量；测试中的 fake/test key 仅用于 Mock。未读取或展示历史凭据值。`SECURITY.md` 已记录历史 `.env` 暴露，旧密钥撤销和历史清理状态未知；删除当前文件不等于撤销。真实部署需由账户持有人确认轮换，继续沿用忽略本地 `.env` 的策略。

## 当前没有实现的能力

商品上下架、商品知识/FAQ/政策管理、网站访客会话、客服检索、真实商品推荐、人工接管、知情同意线索、后台权限、模型调用统计与费用展示。训练话术库不能充当这些能力已经存在的证据。

## 本轮最小改造方案

1. 增加配置化 ModelProvider、DeepSeek 官方 Chat API、Ollama HTTP Provider 和类型化安全错误；保留旧 LLMClient facade，新增请求/鉴权/超时/不可用合约测试。
2. 修复筛选；增加评分 source/status，无效评分不得存档或展示为正常模型结果；不删除或篡改原 40 项测试。
3. 新增独立客服存储和服务层：受管理商品、知识、访客会话、消息、接管、同意线索。客户能力 token 只哈希落库；后台单独鉴权。
4. 模型只选择经过检索的真实 ID；后端校验引用、从数据库渲染价格和链接。模型没有价格、库存、订单、退款或客户资料写工具。投诉、优惠、缺失知识、异常均转人工。
5. 继续使用 Streamlit：产品知识、客服测试、会话、人工队列、模型状态和实际 usage/配置单价估算。无单价显示未知，不伪造成本。
6. 自动化场景不少于 30 条；区分 Mock 合约测试、规则/知识响应、真实模型验收。缺 Key/网络故障记录 BLOCKED，离线链路不冒充真实 AI。

## 范围边界

本轮不部署真实公网服务、不训练/微调、不安装大型模型、不开发拨号或外呼、不自动执行付款退款、不引入多智能体产品架构。Ollama 未部署时明确不可用。网站上线前还需实际密钥/模型验收、TLS/反向代理、限流及备份/数据保留策略。
