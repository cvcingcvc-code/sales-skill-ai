# v1.0.0-beta.1 发布说明

**发布日期：** 2026-11-05
**版本类型：** 预发布（Pre-release）——不是生产稳定版
**仓库：** https://github.com/cvcingcvc-code/sales-skill-ai
**变更日志：** [CHANGELOG.md](../CHANGELOG.md)
**安装与使用：** [README.md](../README.md)

---

## 一、这个版本是什么

首个公开可下载的预发布版本：**真实本地 Ollama 模型驱动的 AI 商品咨询与客服 MVP**。

核心特征是「模型只选资料、答复由服务端生成」：

```
顾客提问
  → 系统检索候选商品与政策资料
  → 本地模型只回答：该引用哪几条资料？   ← 模型唯一职责
  → 服务端校验模型给的 ID 确实来自本次候选资料
  → 服务端用受管理商品记录渲染答复（价格、链接、政策原文）
  → 资料不支持该问题 → 转人工
```

**模型不生成对客文案。** 这带来三个直接后果：

1. 价格、库存、链接、售后条款无法被模型编造或改写——回复里的每个事实都来自你录入的记录。
2. 模型选错资料会被服务端的 ID 白名单校验挡住，并降级为转人工。
3. 模型不确定时明确要求转人工，而不是硬凑一个答案。

## 二、本版本新增的功能

| 功能 | 说明 |
| --- | --- |
| 本地 Ollama 真实模型接入 | `LLM_PROVIDER=ollama`，通过 `core/model_gateway.py` 统一接入；也可切换云端 DeepSeek |
| 真实商品资料选择闭环 | 顾客提问 → 模型选资料 → 服务端校验 → 服务端渲染答复 → 留资 → 人工接管，全链路可复现 |
| 结构化选择的确定性 | 资料选择调用固定 `temperature=0`；网关新增按请求指定 temperature 的能力 |
| 接地的选择提示词 | 提示词写明受管理记录各字段含义，并区分「资料已写明」与「资料完全没写」 |
| 自动化回归测试 | 新增 7 条离线用例覆盖上述改动；Windows 离线回归从 108 个错误降到 8–10 个历史错误 |
| 版本单一来源 | `core/version.py` 的 `PROJECT_VERSION` 同时驱动 `/skill/health` 与 FastAPI 元数据 |

## 三、环境要求

| 项 | 要求 |
| --- | --- |
| Python | 3.12（本项目在 3.12.13 上验证） |
| 操作系统 | Windows / macOS / Linux（本项目在 Windows 11 上完成真实验收） |
| 依赖 | `requirements.lock`（锁定，81 个包，`pip check` 通过） |
| 数据库 | SQLite（WAL 模式），默认 `data/db/support.db` |
| 模型服务 | 官方 Ollama（本项目在 0.40.14 上验收） |
| 模型权重 | **需自行选择并下载**；本项目不自动下载任何模型 |
| 硬件 | 无最低要求。3B 量化模型可在 8 GB 内存的 CPU 机器上试运行；有 6–8 GB 显存的独立 GPU 体验更好 |

**已验证的具体组合：**Windows 11 + Python 3.12.13 + Ollama 0.40.14 + `qwen2.5:3b`
（3.1B / Q4_K_M / 1.9 GB）+ NVIDIA GeForce GTX 1660 Ti（CUDA，6 GB 显存）。
这是**一条被验证过的路径**，不是对任何设备的性能保证。

## 四、安装与启动（速查）

```powershell
git clone https://github.com/cvvingcvc-code/sales-skill-ai.git
cd sales-skill-ai
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

1. 在 `.env` 填入自己的长随机 `ADMIN_API_TOKEN`。
2. 从 [ollama.com/download](https://ollama.com/download) 安装官方 Ollama，
   再按自己的设备 `ollama pull qwen2.5:3b`（或别的标签），用 `ollama list` 核对。
3. `.env` 设置 `MODEL_ENABLED=true`、`LLM_PROVIDER=ollama`、`OLLAMA_BASE_URL`、`OLLAMA_MODEL`。
4. 两个终端分别启动：

```powershell
.\.venv\Scripts\python.exe -m uvicorn agent_skill.api_server:app --host 127.0.0.1 --port 8080 --workers 1 --no-access-log
.\.venv\Scripts\python.exe -m streamlit run demo/app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true
```

5. 访客页 <http://127.0.0.1:8080/chat>；管理端 <http://127.0.0.1:8501>。

完整说明（含 macOS/Linux、排查表）见 [README](../README.md) 与
[本地模型部署与验收](LOCAL_MODEL_DEPLOYMENT.md)。

## 五、测试与验收证据

### 真实模型验收

```
python scripts/verify_live.py --provider ollama
→ status = PASS, 退出码 0
   provider_configuration = PASS
   real_model_request     = PASS    ← 真实模型确实被调用
   managed_product_reply  = PASS    ← 答复来自受管理商品目录
   consented_lead         = PASS    ← 留资需访客显式同意
   human_handoff_reply    = PASS
   target = {host: 127.0.0.1, model: qwen2.5:3b}
```

连续重跑 5 次，5/5 全部 PASS（`temperature=0` 后结论确定）。
验收只使用合成商品与合成访客数据，临时 SQLite 会被删除，报告不含凭据。

### 离线回归

| 项 | 结果 |
| --- | --- |
| 用例数 | 431（Ubuntu CI） |
| 失败（fail） | 0 |
| 跳过（skipped） | 0 |
| 出网尝试（network_attempts） | 0 |
| 错误（error） | Windows 上 8–10 个，见下方已知问题 |

Ubuntu CI 两个 `regression` 作业均通过。

### Windows 历史错误（如实记录，未修复）

约 8–10 个离线用例在 Windows 上间歇性失败：

- 8 × `sqlite3.OperationalError: attempt to write a readonly database`
- 2 × `PermissionError: WinError 32]`（`legacy.db` 被占用无法删除）

已确认是 **Windows 上 SQLite WAL 模式 + 多进程/多线程并发写** 的平台限制：
同一场景改用 `journal_mode=DELETE` 后 8/8 通过；带/不带开发工具沙箱均复现。
本版本**刻意不修**——不删断言、不 skip、不改产品 WAL 行为。
用独立 worktree 跑基线对照（基线 424 用例 / 错误 8–9，本版本 431 用例 / 错误 8–10），
**失败用例的并集完全一致，没有任何新增失败**。

## 六、已知限制

1. **Windows SQLite WAL 并发限制**（如上）。仅影响 Windows 离线回归退出码，不影响产品运行。
2. **尚未用真实商家资料完成充分验收。** 当前验收只使用合成资料链路。
3. **不保证生产级公网部署可用性。** 未实现 HTTPS、反向代理限流、备份轮换、监控告警、高可用；
   应用当前按单 worker、单机 SQLite 运行。
4. **模型能力差异。** 资料选择质量与所选模型强相关。换用其他模型必须重新执行
   `verify_live.py --provider ollama` 验收，不能沿用本版本的结论。
5. **合规由商家负责。** 平台规则需商家自行提供并持续更新，项目不保证识别所有违禁表达。
6. **模型权重需自行获取。** 本项目不自动下载模型，也不会替你安装 Ollama。

## 七、后续计划

1. 把「有依据 → 按目录作答 / 无依据 → 转人工」做成离线可复现的契约测试，降低换模型时的退化风险。
2. 处理 Windows SQLite WAL 并发限制，把离线回归退出码压到 0。
3. 补充真实商家资料下的端到端验收样本与评估脚本。
4. 设计云端后端 ↔ 私有网络模型的连接方案与运维手册。

## 八、许可与素材

代码采用 [MIT License](../LICENSE)。仓库中的第三方素材、模型与商标不在此授权范围内。
本仓库**不包含**任何模型权重、Ollama 安装包或真实商家/客户数据。
