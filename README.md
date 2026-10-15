# AI Sales Agent V1

在原销售模拟训练项目上增加网站文字客服与 Streamlit管理工作台。保留培训、角色扮演、评分和学员记录；增加商品与知识管理、资料检索、真实商品推荐、会话/需求记录、显式同意联系线索及人工接管。

模型通过统一网关接入 DeepSeek；Ollama接口已预留，但本轮未部署本地服务或模型。模型只选择受管理资料 ID，价格、链接和政策由后端读取渲染，没有付款、退款、库存或订单写入工具。真实模型调用是否通过必须看验收报告，离线 Mock 不代表已验证真实 AI。

## 本地启动

在仓库根目录使用 Python 3.12。已有环境或 `.env` 请保留，不要覆盖：

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.lock
if [ ! -e .env ]; then cp .env.example .env; fi
```

通过本机安全编辑器或云环境的 Secret 设置填写配置，不在终端输出密钥：

| 配置 | 用途 |
| --- | --- |
| `DEEPSEEK_API_KEY` | DeepSeek真实调用必需；无 Key 为 BLOCKED |
| `LLM_PROVIDER=deepseek` | 当前云模型选择 |
| `DEEPSEEK_MODEL`、`DEEPSEEK_BASE_URL` | 账户当前可用模型、API根地址；模板默认 `deepseek-chat`、`https://api.deepseek.com/v1` |
| `ADMIN_API_TOKEN` | 在用户本机使用密码管理器安全生成的强随机私密令牌；管理员API和浏览器登录使用同一值，勿打印到报告 |
| `DB_PATH`、`SUPPORT_DB_PATH` | 分别为训练与客服数据库，默认 `./data/db/learners.db`、`./data/db/support.db`，应保持独立 |
| `SUPPORT_API_URL` | 工作台连接的后端，默认 `http://127.0.0.1:8080` |

网关会在创建时读取模型配置。安全更新密钥、模型或地址后，重启 API 和 Streamlit；云环境设置还需确认已应用到运行实例，再在新进程中执行真实验收。只填写配置不会将 `BLOCKED` 自动变成真实调用通过。

在两个终端分别启动，均绑定回环地址：

```bash
.venv/bin/uvicorn agent_skill.api_server:app --host 127.0.0.1 --port 8080 --workers 1 --no-access-log
```

```bash
.venv/bin/streamlit run demo/app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true
```

访客文字页路径是 `/chat`，本机地址为 `http://127.0.0.1:8080/chat`。管理工作台位于 `http://127.0.0.1:8501`，每个浏览器需手动输入管理员令牌，再切换“销售训练”或“客服工作台”。工作台先录入已审核商品及知识，再进行客服测试；空数据库不会自动生成真实商家资料。云机器请通过私有访问/端口转发打开管理工作台，不要公开8501端口。

原本地训练CLI仍可使用：

```bash
.venv/bin/python -m cli.main
```

## 演示与验证

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/demo_workflow.py --mock
.venv/bin/python scripts/demo_workflow.py
.venv/bin/python scripts/verify_live.py --provider deepseek --output /tmp/sales-live-acceptance.json
```

可选浏览器回归：已安装 Node.js、Playwright 和 Chromium 时，启动本地 API 后执行 `node tests/browser_smoke.cjs`。它使用合成联系方式验证告知、会话、规则寒暄、人工申请、同意留资及新会话重置；不会验证真实模型。默认浏览器路径为 `/usr/bin/chromium`，可用 `CHROMIUM_PATH` 覆盖，目标服务可用 `SUPPORT_BROWSER_URL` 选择。该检查会在目标测试服务留下明确的合成会话和线索。

- `--mock` 明确输出 `MOCK_DEMO_COMPLETE`，使用临时数据库和合成商品，验证商品查询→回复→需求记录→同意线索→人工回复链路，不是模型验收。
- 默认演示尝试真实模型；无 Key或网络失败输出 `BLOCKED`、exit 2，同时仍验证不依赖模型的存储、线索和人工链路。
- `verify_live.py` 真实调用不会使用 Mock；exit 0 为 PASS、1 为 FAIL、2 为 BLOCKED。未配置凭据时立即BLOCKED，后续模型相关检查不会冒充通过。报告和数据库不含真实客户资料，输出报告应留在仓库外。
- 使用 unittest发现 `tests/`；仓库根目录的历史手工 `test_gemini.py` 不属于该测试集，不要以盲跑 pytest替代验收。

## 交付文档与当前限制

| 文档 | 内容 |
| --- | --- |
| [PROJECT_AUDIT.md](docs/PROJECT_AUDIT.md) | 改造前Git、入口、依赖、数据和已确认问题 |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | 实际模块、权限、资料选择、数据和调用流程 |
| [DEPLOYMENT.md](docs/DEPLOYMENT.md) | 本地/云端配置、私有管理及反向代理示例 |
| [ACCEPTANCE_REPORT.md](docs/ACCEPTANCE_REPORT.md) | 本轮真实测试结果、Git提交与阻断项 |
| [ROADMAP.md](docs/ROADMAP.md) | 密钥、访问、商家资料、真实验收及以后Ollama部署 |
| [SECURITY.md](SECURITY.md) | 历史凭据暴露及待核验的撤销/轮换 |

当前检索为关键词规则，不是向量RAG；人工接管完成后不自动恢复AI。费用为可选单价估算，币种沿用配置且可能未知，仅覆盖客服调用。单机SQLite与进程内训练会话按单worker运行；公网TLS、限流、备份及保留/删除策略仍需部署方配置，未进行真实公网部署。原评分维度0–20与历史薄弱项阈值70不一致，本轮保留兼容并明确限制。历史密钥轮换未验证。

[原 README](<README (1).md>) 保留为历史说明，当前启动与验收以本文件及交付文档为准。
