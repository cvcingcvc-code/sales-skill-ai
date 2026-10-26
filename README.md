# AI 店铺智能客服工作台

**仓库简介：**面向个体商家、小店铺和小型销售团队的自托管商品销售与客服工作台。目标是让商家维护自己的商品、销售政策和平台规则，由网站客服依据已审核资料答复；资料不足、规则风险或服务故障时转人工。项目也保留原有销售训练能力。

> **当前状态：早期 MVP，模型默认关闭。** 商品管理、访客文字会话和人工接管已有可运行演示；自动 AI 客服、真实模型回答效果和长期公网运行尚未完成验收。下载仓库不等于一键部署。

## 适合谁

- 想整理商品资料、常见问题和售后政策的个体商家或小店铺。
- 希望先在自己电脑上试跑网页文字客服的小团队。
- 需要保留销售新人培训，并逐步评估客服自动化的销售团队。

例如，店主录入商品价格、适用条件、发货说明、售后政策及适用的平台规则，顾客从网页询问商品。目标流程是系统根据已审核资料答复，并在资料缺失、规则命中或投诉等情形下阻止不确定回复并转人工。**商品与知识维护、知识逐条审核已可操作，平台规则自动拦截仍是需求目标；模型尚未通过真实端到端验收之前，自动 AI 回复不应被视作可用能力。**

## 当前已有内容

- 原销售训练工作台与评分流程仍在仓库中；规则评分需与真实模型评分区分。
- Streamlit 管理端可维护商品、FAQ、商品文档和售后政策，并处理会话与人工接管。
- 知识保存后先待审核；管理员核对已保存版本并确认后才可参与客服检索。正文、标题、来源、类型或关联商品修改后须重新审核，启用开关单独控制是否使用。
- 可填写内部资料来源，例如文档名称、版本或页码；来源仅供后台和历史快照追溯，不发送给模型或访客，也不会自动抓取来源内容。
- 通过资料选择器生成的新答复会保存当时选中资料的快照，管理员可在“客户会话”查看；之后改价、改政策不会覆盖历史依据。旧答复不补造快照，Mock 仍明确标记，详见[历史依据查看说明](docs/USER_GUIDE.md#查看历史答复依据)。
- FastAPI 提供访客文字会话、商品查询、同意后留资、人工回复及会话数据管理；SQLite 用于本地持久化。
- 代码包含 Ollama 与 DeepSeek Provider 配置路径，但配置存在不代表模型可连通或答复通过验收。
- 已完成使用虚构通信套餐的真实 HTTP、浏览器和隔离 SQLite 演示；模型关闭，回复通过人工接管发送。

演示截图（均为虚构数据）：

- [管理端商品与知识库](docs/demo-assets/00-admin-telco-catalog.png)
- [访客咨询与人工回复：套餐推荐](docs/demo-assets/01-recommendation.png)
- [访客咨询与人工回复：资费问题](docs/demo-assets/02-billing.png)
- [访客咨询与人工回复：知识不足转人工](docs/demo-assets/03-knowledge_gap.png)
- [历史答复依据：离线夹具展示，非真实模型](docs/demo-assets/04-answer-evidence-mock.png)
- [模型关闭时的访客页面：DISABLED 转人工](docs/demo-assets/05-model-disabled-handoff.png)
- [商家维护知识来源并审核](docs/demo-assets/06-knowledge-source-reviewed.png)
- [历史答复保留原来源：离线夹具，非真实模型](docs/demo-assets/07-knowledge-source-history-mock.png)
- [演示流程及证据说明](docs/MVP_DEMO_PACKAGE.md)

## 当前边界

以下能力目前**未实现或未完成真实验收**：

- 网站自动请求已启用模型、依据商家资料回答真实访客，并可靠处理未覆盖问题。Codex 仅是开发/任务执行工具；此前演示由 Codex 辅助人工拟稿，再由管理员发送，不是网站自动调用 Codex。
- 在用户自己的 Windows、macOS、Linux 电脑上完成真实模型端到端安装验收；本地部署说明是操作指引，不是通过证明。
- 长期公网部署、生产可用性和生产级运维。
- 与第三方电商平台、社交消息平台或订单系统的接入；自动交易、改订单、付款和退款。
- 任意模型、任意 API Key 或所有电商平台的即插即用兼容。
- 完整的平台合规审核或对所有违禁表达的识别保证；平台规则需要商家提供、核对来源并持续更新，单一违禁词表不能保证合规。

## 下载、安装与启动

需要 Python 3.12。以下命令只安装项目依赖，不会自动安装 Ollama 或下载模型。首次运行的 `.env`、令牌和数据库应留在本机，不要提交 Git。

### Windows（PowerShell）

1. 安装 Git 与 Python 3.12，打开 PowerShell。
2. 克隆项目并进入目录：

```powershell
git clone --branch codex/s2-windows-first-run --single-branch https://github.com/cvcingcvc-code/sales-skill-ai.git
cd sales-skill-ai
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

3. 在本机受保护的编辑器中配置 `.env`。首次保持 `MODEL_ENABLED=false`，本地先用 `ADMIN_API_TOKEN` 设置长且随机的私有管理员令牌。
4. 在两个 PowerShell 窗口中、都位于项目目录时分别启动：

```powershell
$env:MODEL_ENABLED="false"
$env:LLM_PROVIDER="ollama"
.\.venv\Scripts\python.exe -m uvicorn agent_skill.api_server:app --host 127.0.0.1 --port 8080 --workers 1 --no-access-log
```

```powershell
$env:MODEL_ENABLED="false"
$env:LLM_PROVIDER="ollama"
$env:SUPPORT_API_URL="http://127.0.0.1:8080"
.\.venv\Scripts\python.exe -m streamlit run demo/app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true
```

访客页面：<http://127.0.0.1:8080/chat>；管理员工作台：<http://127.0.0.1:8501>。管理员令牌在本机登录页面手动输入，不要放进 URL、聊天、截图或命令分享。仅供本机试用；不要把 Streamlit 管理端、FastAPI 管理接口或 Ollama 端口直接暴露公网。

### macOS / Linux

依赖安装与启动命令见 [本机与部署说明](docs/DEPLOYMENT.md)；Ollama 安装、环境变量和真实模型验收见[本地模型说明](docs/LOCAL_MODEL_DEPLOYMENT.md)。Windows、macOS、Linux 均需结合自己的设备确认安装包、系统权限、模型资源和网络条件。

## 首次使用

1. 启动 FastAPI 和 Streamlit，浏览器打开管理端并登录。
2. 在“产品与知识库”录入并核对商品名称、价格、描述、链接和上下架状态。
3. 保存 FAQ、商品说明和售后/经营政策后，从“选择知识”加载记录，展开核对已保存版本，勾选确认并点“审核通过所选知识”。知识还需启用，关联商品需在售，才会进入客服检索。旧资料首次升级会保留内容并标为待审核，需要管理员逐条确认。
4. 打开访客 `/chat` 查询商品并提交问题；从后台“客户会话”或“人工接管队列”处理需要人工确认的问题。
5. 只在取得访客明确同意后记录联系信息。数据保留、撤回、删除及备份责任见[工作台使用手册](docs/USER_GUIDE.md)和[部署说明](docs/DEPLOYMENT.md)。

## 模型连接方式与真实验收

模型由后端通过 `core/model_gateway.py` 调用；API Key 不应发送到访客浏览器。新安装默认 `MODEL_ENABLED=false`、`LLM_PROVIDER=ollama`，需显式配置并重启服务。

**本地 Ollama：** 在运行后端的同一台电脑（或经安全配置的私有网络）安装并启动官方 Ollama，确认模型已在本机安装后，在 `.env` 配置 `LLM_PROVIDER=ollama`、`OLLAMA_BASE_URL`、`OLLAMA_MODEL`。Ollama 不需要 `DEEPSEEK_API_KEY`。项目不会替用户自动安装 Ollama 或下载模型。

**云端 DeepSeek：** 由商家自行向服务商申请并保管密钥；在后端私密环境设置 `LLM_PROVIDER=deepseek`、`DEEPSEEK_API_KEY`，并按账户当前可用设置 `DEEPSEEK_MODEL`、`DEEPSEEK_BASE_URL`（模板默认地址为 `https://api.deepseek.com/v1`）。模型名称、账户权限和服务状态须以服务商当前信息核对。

示例启动命令显式把 `MODEL_ENABLED` 设为 `false`，因此会覆盖 `.env` 中的同名值。只有在模型服务、模型名和凭据已准备好后，才在两个服务终端的新进程中改为 `true`，同时将 `LLM_PROVIDER` 设为所选 Provider，再重启并按真实验收脚本验证。`.env` 只会补充未由当前进程环境设置的值。`.env.example` 中使用的变量名均按项目当前配置定义。不要把 `.env`、真实 API Key 或管理员令牌提交 Git、截图、日志或报告；不要把密钥粘贴到聊天，也不要将密钥交给访客浏览器。若历史密钥曾公开，按 [安全说明](SECURITY.md)立即撤销并轮换。任何新增 Provider 均需单独实现、配置和验收，不能据此宣称任意模型兼容。

状态含义：

- `DISABLED`：管理员主动关闭了模型调用；访客依赖模型的问题应转人工。
- `BLOCKED`：配置、凭据、服务、网络或预算等前置条件阻止了调用；不是成功回答。
- `NOT_RUN`：没有执行真实模型请求；离线测试、Mock 和配置检查不能替代。
- `UNVERIFIED` / `configured`：配置已读入，不等于服务可用。真实结果只能由当前环境中的真实 Provider 请求确认。

连接后还需在真实隔离环境运行验收脚本，例如：

```powershell
# Windows PowerShell；先在当前会话安全配置 Provider、模型和凭据
.\.venv\Scripts\python.exe scripts\verify_live.py --provider ollama --output "$env:TEMP\sales-local-acceptance.json"
# 云端 Provider 使用 --provider deepseek，报告同样写入本机临时目录
```

macOS/Linux 命令、设备要求和报告状态详见[本地模型部署与验收说明](docs/LOCAL_MODEL_DEPLOYMENT.md)。脚本退出码 `0` 为真实验收 PASS、`1` 为 FAIL、`2` 为 BLOCKED；一次合成问答通过不等于真实商家、公网生产或长期稳定性验收通过。

## 长期目标与参与方式

产品暂定名称为 **AI 店铺智能客服工作台**。长期目标是让普通商家在自己的电脑或受控服务器部署，自行维护商品、经营政策及经审核的平台规则，通过本地模型或商家授权的云端 API 支持售前客服，并保留清晰的人工接管、隐私和成本边界。产品需求和验收场景见[单店商品销售与客服 MVP 需求](docs/STORE_SALES_MVP_REQUIREMENTS_ZH.md)，阶段安排见[中文产品路线图](docs/PRODUCT_ROADMAP_ZH.md)。

欢迎先提交 Issue 说明操作系统、部署障碍、真实客服流程或希望验证的问题；贡献代码前请先讨论范围，避免提交密钥、客户数据、模型权重或未经授权的商家内容。当前阶段优先验证“一家店能否在自己的设备上跑通”，不承诺一键部署或所有场景即插即用。

## 详细说明

- [部署与本地/云端边界](docs/DEPLOYMENT.md)
- [本地逐步调试指南：先跑通应用，再验收 Ollama](docs/DEBUGGING_GUIDE_ZH.md)
- [本地 MVP 验收缺口与推进顺序](docs/ACCEPTANCE_GAP_REGISTER_ZH.md)
- [Windows、macOS、Linux 本地模型设置与真实验收](docs/LOCAL_MODEL_DEPLOYMENT.md)
- [管理员、访客和会话操作手册](docs/USER_GUIDE.md)
- [MVP 演示流程、截图和 Codex 辅助回复证据](docs/MVP_DEMO_PACKAGE.md)
- [架构说明](docs/ARCHITECTURE.md)
