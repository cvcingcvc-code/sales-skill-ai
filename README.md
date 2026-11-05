# AI 店铺智能客服工作台

**当前版本：v1.0.0-beta.1（预发布）** ｜ 许可证：[MIT](LICENSE) ｜ 版本说明：[RELEASE_NOTES_v1.0.0-beta.1](docs/RELEASE_NOTES_v1.0.0-beta.1.md) ｜ 变更日志：[CHANGELOG.md](CHANGELOG.md)

**仓库简介：**面向个体商家、小店铺和小型销售团队的自托管商品销售与客服工作台。商家维护自己的商品、销售政策和平台规则，网站客服依据**已审核资料**答复；资料不足、规则风险或服务故障时转人工。项目同时保留原有销售训练能力。

> **本版本已完成的真实验收：**在 Windows 11 + 本地 Ollama 0.40.14 + `qwen2.5:3b`（3.1B / Q4_K_M 量化）上，`scripts/verify_live.py --provider ollama` 返回 **PASS，退出码 0**，连续多次结果一致。
> **仍未完成：**长期公网运行、真实商家资料下的充分验收、第三方平台接入。下载仓库不等于一键部署，也不会自动替你下载模型。

## 适合谁

- 想整理商品资料、常见问题和售后政策的个体商家或小店铺。
- 希望先在自己电脑上跑通网页文字客服的小团队。
- 需要保留销售新人培训，并逐步评估客服自动化的销售团队。
- 想研究「模型只做资料选择、回复由服务端按受管理目录生成」这种保守 AI 架构的开发者。

## 一分钟看懂它怎么工作

这是本项目与普通「接个大模型就能聊天」方案最重要的区别：

```
顾客提问  →  系统按关键词/预算/商品名检索出候选资料
          →  本地模型只回答一个问题：「该引用哪几条资料？」
          →  服务端校验模型给的 ID 确实来自本次候选资料
          →  服务端用受管理商品记录拼装回复（价格、链接、政策原文）
          →  资料不支持该问题时，转人工
```

**模型不写对客文案。** 价格、库存、链接、售后条款全部来自你录入的受管理记录，由服务端渲染。
这样做的直接后果：

- 模型把价格说错、改价、编造库存——在架构上就做不到，因为回复里的价格不是你模型的输出。
- 模型可以「选错资料」（漏选、误选、多选），但选错会被服务端的 ID 校验挡住并转人工。
- 模型不确定时，会明确要求转人工，而不是编一个答案。

**一个真实业务使用场景：**店主在管理端录入「轻享耳机」的名称、价格 199.00 元、适用场景、购买链接，并上传一份审核过的「耳机保修政策」。顾客在网页问「轻享耳机多少钱？保修多久？」。系统检索出这条商品和这份政策，模型选中它们的 ID，服务端核对 ID 合法后，用目录里的价格和政策原文生成答复。如果顾客问的是资料里根本没写的内容（比如「能不能开票」「什么时候发货」），模型会判定资料无法支撑，转给店主人工回答。

## 已实现的功能

**商品与知识管理（Streamlit 管理端）**

- 商品 CRUD：名称、客观描述、价格（单位为**人民币分**，12.34 元填 `1234`）、商品链接、上/下架状态。
- 知识库：FAQ、商品说明、售后政策，可关联商品；未生效内容不进入访客可见范围。
- 客户会话、人工接管队列、模型用量与预算看板、隐私与审计。

**访客客服（FastAPI + 网页）**

- 匿名会话（凭证只用于访问自己的会话，不是账户系统）、商品目录查询、多轮咨询。
- 知情同意后的留资表单：联系方式**只**通过专用表单保存，必须显式勾选同意，不会从聊天里自动提取。
- 人工接管与人工答复；会话关闭、撤回线索、永久删除等数据管理动作。

**真实 AI 资料选择（本版本新增并完成真实验收）**

- 本地 Ollama Provider（`LLM_PROVIDER=ollama`），也可切换云端 DeepSeek（`LLM_PROVIDER=deepseek`）。
- 模型输出严格限定为 `{"product_ids":[],"knowledge_ids":[],"needs_human":bool}`，服务端做 ID 白名单校验。
- 资料选择调用固定 `temperature=0`，保证同一输入得到同一结论。
- 服务端强制业务规则：价格不可被模型覆盖、预算上限过滤、危险/越权文案拦截、无依据问题转人工。

**销售训练（原工作台能力保留）**

- 场景与难度筛选、角色扮演、评分（0–20 维度分 / 0–100 总分）与能力画像。
- 规则评分与真实模型评分在界面上分别标识，规则分数只是启发式匹配。

## 还在开发中的功能

以下能力**未实现或未完成真实验收**，请勿当作可用能力：

- 长期公网运行、生产可用性与生产级运维（HTTPS、反向代理、备份轮换、监控告警均未落地）。
- 真实商家资料下的充分验收：本版本只验证过合成资料链路。
- 与第三方电商平台、社交消息平台、订单系统的接入；自动成交、改订单、付款与退款。
- 任意模型 / 任意 API Key / 所有电商平台即插即用兼容；每个 Provider 需单独实现、配置与验收。
- 完整的平台合规审核。平台规则需要商家自行提供、核对来源并持续更新，单一违禁词表不构成合规保证。
- 云端后端 ↔ 家中电脑模型的私有网络通道。

## 真实模型 / 业务规则 / Mock 测试，三者要分清

| | 是什么 | 怎么识别 |
| --- | --- | --- |
| **真实 AI 模型** | 本机 Ollama 或云端 DeepSeek 返回的资料选择 JSON | 访客答复里 `model_status` 为 `OK`；后台「模型状态」显示 `AVAILABLE` |
| **业务规则** | 服务端自己写的确定逻辑：预算过滤、价格渲染、越权拦截、寒暄回复、转人工判定 | 访客答复里 `model_status` 为 `NOT_RUN` 或 `DISABLED`，`source` 为 `rule` |
| **Mock / 离线测试** | 自动化测试里写死的假模型输出，**从不发真实请求** | 访客答复里 `model_status` 为 `MOCK`，`source` 为 `mock_selection` |

后台状态字段含义：

- `DISABLED` — 你主动关闭了模型调用（`MODEL_ENABLED=false`）。不是连通性证明。
- `BLOCKED` — 配置、凭据、服务、网络或预算阻断了调用。**不是成功回答。**
- `NOT_RUN` — 没有执行真实模型请求。离线测试、Mock 和配置检查都不能替代它。
- `UNVERIFIED` / `configured` — 配置已读入，不等于服务可用。
- `AVAILABLE` — 最近一次真实模型请求成功；这也不保证后续每次都成功。

## 技术栈与架构

| 层 | 选型 |
| --- | --- |
| 后端 API | FastAPI + Uvicorn（单 worker，`--host 127.0.0.1 --port 8080`） |
| 管理端 | Streamlit（`demo/app.py`，`--server.port 8501`） |
| 访客页 | 仓库内静态页 `web/customer.html` + `web/customer.js`，由 FastAPI 托管 |
| 模型接入 | `core/model_gateway.py`：统一 Provider 接口（Ollama / DeepSeek / Gemini / Claude） |
| 客服编排 | `customer_service/service.py`：检索 → 模型选资料 → 服务端校验 → 服务端渲染答复 |
| 持久化 | SQLite（WAL 模式），默认 `data/db/support.db` |
| 依赖 | `requirements.lock`（锁定） / `requirements.txt`（范围） |

架构细节见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

### 目录速览

```
agent_skill/        FastAPI 应用、管理鉴权、客服路由
core/               模型网关、评分引擎、角色扮演、版本常量
customer_service/   客服编排、SQLite 存储、限流与预算
demo/               Streamlit 管理端
web/                访客网页
scripts/            check_local_setup.py / verify_live.py / run_tests.py 等
tests/              离线回归测试（真实调用一律不打网络）
docs/               部署、调试、验收与版本文档
```

## 下载、安装与启动

需要 **Python 3.12**。**以下命令只安装项目依赖，不会自动安装 Ollama，也不会自动下载模型权重。**
模型请按你自己的设备（内存 / 显存 / 磁盘）选择，见 [本地模型部署与验收](docs/LOCAL_MODEL_DEPLOYMENT.md)。

### 1. Windows（PowerShell）

```powershell
git clone https://github.com/cvvingcvc-code/sales-skill-ai.git
cd sales-skill-ai
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

> 如果 `py -3.12` 报 “No suitable Python runtime found”，说明你的 Python 由 uv、conda 或
> Microsoft Store 管理，`py` 启动器没有登记 3.12。改用其中任一即可（只要是 3.12.x）：
> `python3.12 -m venv .venv`、`uv venv --python 3.12 .venv`，或直接用 3.12 解释器完整路径
> `"<python-3.12-path>\python.exe" -m venv .venv`。
> 建好虚拟环境后确认版本：`.venv\Scripts\python.exe --version`。

### 2. macOS / Linux

```bash
git clone https://github.com/cvvingcvc-code/sales-skill-ai.git
cd sales-skill-ai
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
[ -f .env ] || cp .env.example .env
```

### 3. 配置 `.env`

用密码管理器生成一个长随机管理员令牌，填入 `.env` 的 `ADMIN_API_TOKEN`。**`.env` 不要提交 Git、不要贴进聊天或截图。**

先用「模型关闭」把业务跑通：

```dotenv
MODEL_ENABLED=false
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=
ADMIN_API_TOKEN=<你自己的长随机令牌>
```

### 4. 安装 Ollama 并准备模型（不会自动完成）

从 [ollama.com/download](https://ollama.com/download) 安装官方 Ollama（Windows / macOS / Linux 各自的官方包），然后在你**自己的**模型电脑上选择并拉取一个适合自己设备的模型，例如：

```bash
ollama pull qwen2.5:3b      # 3B 量化模型，适合低配设备试运行
ollama list                 # 确认本机已安装的准确标签
```

拉取权重需要联网并占用磁盘与流量，请先确认空间。**项目不会替你下载模型，也不会默认下载任何大型权重。** 设备选择建议见 [本地模型部署与验收](docs/LOCAL_MODEL_DEPLOYMENT.md#2-按实际设备选择试运行模型)。

### 5. 启动两个服务

两个终端，工作目录都是仓库根目录：

```powershell
# 终端 A：后端 API
.\.venv\Scripts\python.exe -m uvicorn agent_skill.api_server:app --host 127.0.0.1 --port 8080 --workers 1 --no-access-log
```

```powershell
# 终端 B：管理端
.\.venv\Scripts\python.exe -m streamlit run demo/app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true
```

macOS / Linux 把 `.\.venv\Scripts\python.exe` 换成 `.venv/bin/python`。

- 访客页：<http://127.0.0.1:8080/chat>
- 管理端：<http://127.0.0.1:8501>（管理员令牌在页面里手动输入，**不要放进 URL**）

> 仅供本机试用。**不要**把 Streamlit 管理端、FastAPI 管理接口或 Ollama 的 11434 端口直接暴露公网。

## 第一次使用

1. 启动两个服务，打开管理端并输入管理员令牌登录。
2. 在「产品与知识库」录入并核对商品：名称、描述、价格（人民币分）、链接、上下架状态。
3. 添加审核过的 FAQ、商品说明、售后政策，可关联商品。**只录入自己有权使用、能够核实的业务事实**，不要把内部操作指令、凭据或客户资料放进知识库。
4. 打开访客 `/chat` 提问，检查答复里的价格和引用是否来自你录的资料。
5. 在「人工接管队列」处理需要人工确认的问题；答复后访客刷新即可看到。
6. 留资必须由访客在专用表单里明确勾选同意后才会保存；不从聊天自动提取。

完整操作说明见 [工作台使用手册](docs/USER_GUIDE.md)。

### 启用真实模型

装好 Ollama、模型已 `pull` 完成后，把 `.env` 改成：

```dotenv
MODEL_ENABLED=true
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen2.5:3b
MODEL_MAX_INPUT_TOKENS=4000
MODEL_MAX_OUTPUT_TOKENS=1024
MODEL_TIMEOUT_SECONDS=60
```

**改完必须重启 API 和 Streamlit 两个进程**，否则仍读旧配置。终端里已设置的环境变量优先于 `.env`。

低配设备先用 `4000 / 1024` 的输入/输出上限。输入上限按网关组装后的 UTF-8 字节数作保守估算（不是精确 token 数），中文资料长时可能提前拒绝；超限就减少检索到的资料或历史长度，不要无限调大上限。

## 运行测试与真实模型验收

### 离线回归测试（不需要网络、不需要模型）

```bash
# macOS / Linux
.venv/bin/python scripts/run_tests.py --strict
```

```powershell
# Windows PowerShell
.\.venv\Scripts\python.exe scripts/run_tests.py --strict
```

脚本会拦截所有出网连接，`network_attempts` 必须为 `0` 才算通过。
**已知问题：**在 Windows 上仍有约 8–10 个历史错误用例，集中在跨进程并发写入 SQLite（WAL 模式）场景
（`sqlite3.OperationalError: attempt to write a readonly database` 与 `legacy.db` 文件占用）。
这些是 Windows 平台限制，不在本版本修复范围内，详见 [CHANGELOG.md](CHANGELOG.md#known-issues)。
Ubuntu CI 当前全绿。

### 真实模型验收（会真的调用你的模型）

```powershell
# Windows PowerShell
.\.venv\Scripts\python.exe scripts\verify_live.py --provider ollama --output "$env:TEMP\sales-local-acceptance.json"
```

```bash
# macOS / Linux
.venv/bin/python scripts/verify_live.py --provider ollama --output /tmp/sales-local-acceptance.json
```

| 退出码 | 状态 | 含义 |
| --- | --- | --- |
| `0` | `PASS` | 当前真实 Provider 与「商品咨询 → 模型输出 → 服务端验证 → 商品目录回复 → 同意留资 → 人工接管」链路全部通过 |
| `1` | `FAIL` | 请求或业务响应不符合合约；**不能用 Mock 改判为通过** |
| `2` | `BLOCKED` | 模型关闭、标签/配置缺失、服务未启动、超时或不可达等前置条件未满足 |

脚本只用合成商品与合成访客数据，临时 SQLite 会被删除，报告里不含凭据。
**一次 PASS 不等于真实商家资料验收通过，也不等于公网生产可用。**

## 适用范围、隐私与安全限制

- **模型权限**：模型只能选择已存在的受管理记录 ID，没有任何改价、付款、退款、订单操作或数据库写入能力。
- **数据留存**：会话与需求摘要保存在本机 SQLite；联系线索只在访客明确同意后保存，可撤回、可删除。数据保留天数、备份轮换与删除责任由部署者承担。
- **请勿**发送或录入密码、密钥、身份证、银行卡等敏感信息；系统会做部分脱敏，但不等于完整合规。
- **管理员令牌**只在本机/私有网络使用，不要写进 URL、聊天、截图、日志或仓库。
- **模型端口**：不要把 Ollama 的 11434 暴露公网，也不要让访客浏览器直连 `localhost`。
- **合规**：平台规则需商家自行提供并持续更新；项目不保证识别所有违禁表达。
- 详见 [SECURITY.md](SECURITY.md)。

## 已知问题

| 问题 | 影响 | 状态 |
| --- | --- | --- |
| Windows 上跨进程并发写 SQLite 的历史错误用例（约 8–10 个） | 仅影响 Windows 离线回归的退出码，不影响产品运行 | 本版本未修复，已如实记录 |
| 真实商家资料下的充分验收 | 目前只验证过合成资料链路 | 待完成 |
| 公网长期运行 | 未做 HTTPS、限流、备份、监控 | 未实现 |
| 小模型能力差异 | 不同模型对资料选择 JSON 的稳定性不同；本项目已固定 `temperature=0` 并收紧提示词，但换模型仍需重新验收 | 需按模型逐一验收 |

## 未来路线图

1. 把「有依据→按目录作答 / 无依据→转人工」做成离线可复现的契约测试，降低换模型时的退化风险。
2. 处理 Windows SQLite WAL 并发限制，把离线回归退出码压到 0。
3. 补充真实商家资料下的端到端验收样本与评估脚本。
4. 设计云端后端 ↔ 私有网络模型的连接方案与运维手册。

详细路线见 [中文产品路线图](docs/PRODUCT_ROADMAP_ZH.md) 与 [docs/ROADMAP.md](docs/ROADMAP.md)。

## 参与方式

欢迎先提 Issue 说明操作系统、部署障碍、真实客服流程或希望验证的问题；贡献代码前先讨论范围。
**不要**提交密钥、客户数据、模型权重或未经授权的商家内容。
当前阶段优先验证「一家店能否在自己的设备上跑通」，不承诺一键部署或所有场景即插即用。

## 详细说明

- [变更日志](CHANGELOG.md) ｜ [本版本发布说明](docs/RELEASE_NOTES_v1.0.0-beta.1.md)
- [部署与本地/云端边界](docs/DEPLOYMENT.md)
- [本地模型部署与真实验收](docs/LOCAL_MODEL_DEPLOYMENT.md)
- [本地逐步调试指南](docs/DEBUGGING_GUIDE_ZH.md)
- [工作台使用手册](docs/USER_GUIDE.md)
- [架构说明](docs/ARCHITECTURE.md)
- [本地 MVP 验收缺口与推进顺序](docs/ACCEPTANCE_GAP_REGISTER_ZH.md)
- [MVP 演示流程与截图](docs/MVP_DEMO_PACKAGE.md)

## License

代码采用 [MIT License](LICENSE)。仓库中的第三方素材、模型与商标不在此授权范围内。
