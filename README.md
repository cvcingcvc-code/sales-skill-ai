# AI Sales Agent V2

在原销售模拟训练项目上增量建设网站文字客服与 Streamlit 管理工作台，保留培训、场景、难度筛选、角色扮演、评分和学员记录。客服具备商品/知识管理、受管理资料检索、会话与需求记录、知情同意留资、人工接管及隐私管理。

本轮先完成软件闭环，**新部署默认 `MODEL_ENABLED=false`、`LLM_PROVIDER=ollama`，不安装 Ollama、不下载模型、不执行真实推理**。DeepSeek 保留为以后显式切换的云端 Provider。模型暂停时，商品列表、规则寒暄、人工答复和数据管理可用；依赖模型的问答显示 `DISABLED` 并转人工。真实模型效果为 `NOT_RUN`；运行真实验收脚本时主动暂停应报告 `BLOCKED / disabled`，不能记为 PASS。

模型只选择受管理资料 ID，价格、链接和政策由后端校验并渲染，没有付款、退款、库存或订单写入工具。Mock 演示与自动化测试不能证明真实 AI 可用。

## 本地启动

以下为 macOS/Linux 命令，在仓库根目录使用 Python 3.12。已有环境或 `.env` 请保留，不要覆盖：

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.lock
if [ ! -e .env ]; then cp .env.example .env; fi
```

通过本机安全编辑器配置以下项目，不在终端、聊天或报告输出令牌：

| 配置 | 用途 |
| --- | --- |
| `MODEL_ENABLED=false` | 本轮保持暂停；只有以后确认模型已部署才显式开启 |
| `LLM_PROVIDER=ollama` | 本地优先；Ollama 不需要云端 API Key |
| `OLLAMA_BASE_URL`、`OLLAMA_MODEL` | 默认本机 `http://127.0.0.1:11434`；当前模型留空，不自动下载 |
| `ADMIN_API_TOKEN` | 用密码管理器生成强随机私密令牌；API、工作台服务及浏览器手动登录使用同一值 |
| `DB_PATH`、`SUPPORT_DB_PATH` | 独立训练/客服数据库，默认 `./data/db/learners.db`、`./data/db/support.db` |
| `SUPPORT_API_URL` | 工作台连接的后端，默认 `http://127.0.0.1:8080` |

从旧 `.env` 升级时，需手动补上暂停开关与本地 Provider；为兼容旧部署，完全未设置开关的旧进程仍保持原行为。网关创建时读取配置，更新后重启 API 和 Streamlit；云设置还需确认已注入运行实例。

先做离线配置预检：

```bash
.venv/bin/python scripts/check_local_setup.py --json
```

`OFFLINE_READY` 只证明静态前提通过，不连接模型、不验证数据库实际创建或回答质量。然后在两个终端分别启动，均绑定回环地址；以下进程显式保持模型暂停：

```bash
MODEL_ENABLED=false LLM_PROVIDER=ollama .venv/bin/python -m uvicorn agent_skill.api_server:app --host 127.0.0.1 --port 8080 --workers 1 --no-access-log
```

```bash
MODEL_ENABLED=false LLM_PROVIDER=ollama .venv/bin/python -m streamlit run demo/app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true
```

访客页为 `http://127.0.0.1:8080/chat`；工作台默认 `http://127.0.0.1:8501`。端口占用时选择空闲端口，不结束已有服务；若 API 改端口，同步修改 `SUPPORT_API_URL` 并重启工作台。本任务的新 UI 暂使用 `8502`，这是当前演示端口，通用默认仍为 `8501`。远端管理工作台仅通过私有访问/端口转发打开，不公开 Streamlit 端口。

每个浏览器手动输入管理员令牌后，可切换“销售训练”与“客服工作台”。客服工作台有六个页面：产品与知识库、AI 客服测试、客户会话、人工接管队列、模型状态和调用成本、隐私与审计。先录入已审核商品与政策，空数据库不会自动生成真实商家资料。原 CLI 仍可使用 `.venv/bin/python -m cli.main`；真实角色扮演和模型评分须等模型启用后验收。

Windows 以及以后选择设备、安装本地模型的步骤见 [LOCAL_MODEL_DEPLOYMENT.md](docs/LOCAL_MODEL_DEPLOYMENT.md)；业务操作见 [USER_GUIDE.md](docs/USER_GUIDE.md)。

## 演示与验证

```bash
.venv/bin/python scripts/run_tests.py --quiet
.venv/bin/python scripts/demo_workflow.py --mock
MODEL_ENABLED=false .venv/bin/python scripts/demo_workflow.py
MODEL_ENABLED=false .venv/bin/python scripts/verify_live.py --provider ollama --output /tmp/sales-local-acceptance.json
```

- `run_tests.py` 使用临时数据库、隔离配置和禁止外连的测试进程，验证 Mock Provider 合约及业务回归；不会启用运行服务的模型。优先使用此入口，直接 unittest 可能继承 `.env` 中的暂停设置，使原测试的 Mock 合约前提不一致。
- `--mock` 明确输出 `MOCK_DEMO_COMPLETE`，验证商品查询→回复→需求→同意留资→人工回复→明确恢复 AI→撤回留资→关闭/删除链路，不是真实模型验收。
- 默认演示使用当前配置；暂停时为 `DISABLED`、退出码 2，模型缺失或故障为 `BLOCKED`，仍检查独立人工链路。
- `verify_live.py` 不使用 Mock；退出码 0 为 PASS、1 为 FAIL、2 为 BLOCKED。当前暂停时原因是 `disabled`，不发出模型请求。以后按本地部署文档完成配置并显式开启后再真实验收，报告留在仓库外。
- 根目录 `test_gemini.py` 为历史手工联网脚本，不属于离线测试入口，不要盲跑 pytest 触发它。

可选浏览器回归：已有 Node.js、Playwright 和 Chromium 时，对隔离的测试 API 执行 `node tests/browser_smoke.cjs`。用 `SUPPORT_BROWSER_URL` 指向测试服务、`CHROMIUM_PATH` 指向浏览器；它验证告知、规则寒暄、人工、同意留资、关闭、撤回、删除及新会话重置，不验证模型。

## 功能边界与文档

访客可关闭会话、确认撤回联系线索和确认删除本会话；管理员可在人工待办全部处理后明确恢复 AI，关闭后的会话需新建。隐私页先预览再确认清理，只处理已关闭、最后活动超期且没有待办的会话；保留无消息正文的操作审计，不默认安装定时清理任务。

SQLite 提供共享请求限流和客服 UTC 每日次数/token/可选金额预算；单次输入/输出也有上限。每日预算不覆盖旧销售训练，不能当作全应用费用上限。无用量/单价显示未知，费用只为估算。评分保留原始 0–20 分并按显式元数据归一为百分制；不猜测未知旧量纲，旧手工高分的兼容阈值例外见架构文档。

| 文档 | 内容 |
| --- | --- |
| [PROJECT_AUDIT.md](docs/PROJECT_AUDIT.md) | 改造前审计与历史问题 |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | 当前模块、权限、状态、预算与评分边界 |
| [USER_GUIDE.md](docs/USER_GUIDE.md) | 管理、访客、人工、隐私操作 |
| [LOCAL_MODEL_DEPLOYMENT.md](docs/LOCAL_MODEL_DEPLOYMENT.md) | Windows/macOS/Linux、本地优先预检与以后接模型 |
| [DEPLOYMENT.md](docs/DEPLOYMENT.md) | 本地/云端配置及私有管理、代理示例 |
| [ACCEPTANCE_REPORT_V2.md](docs/ACCEPTANCE_REPORT_V2.md) | 本轮结果、Git 提交与阻断项 |
| [ACCEPTANCE_REPORT.md](docs/ACCEPTANCE_REPORT.md) | V1 历史验收记录 |
| [ROADMAP.md](docs/ROADMAP.md) | 本轮范围及以后真实模型/业务验收 |
| [SECURITY.md](SECURITY.md) | 历史凭据暴露及待核验的撤销/轮换 |

当前为单机 SQLite、单 API worker、关键词检索，未接入向量 RAG、多租户账户或分布式训练会话。公网 TLS、代理保护、备份与恢复仍需部署方配置；未进行真实公网部署、真实商家资料验收或本地模型回答效果验收。应用删除不保证历史备份和磁盘法证清除。DeepSeek 以后可选启用，历史密钥轮换未验证。

[原 README](<README (1).md>) 保留为历史说明，当前启动以本文件及 V2 文档为准。
