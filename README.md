# AI 店铺智能客服工作台

这是一个面向个体商家、小店铺和小型销售团队的自托管商品客服项目。商家整理商品、常见问题和售后政策，顾客从网页咨询；资料不足、投诉或模型不可用时，应转给人工处理。仓库也保留销售新人培训与模拟对话能力。

> **版本说明：** GitHub 默认 `master` 目前还是较早的销售训练版本，不包含下方所述的店铺客服工作台。要下载当前可运行的店铺客服开发版本，请按下面命令显式克隆 `codex/s2-windows-first-run` 分支。该版本已通过 Windows runner 的安装和“模型关闭时转人工”验收；真实 Ollama/DeepSeek 客服推理尚未通过验收。

```powershell
git clone --branch codex/s2-windows-first-run --single-branch https://github.com/cvcingcvc-code/sales-skill-ai.git
cd sales-skill-ai
```

## 这个开发版本包含什么

- Streamlit 管理端：维护商品、FAQ、商品说明和售后政策，查看会话并处理人工接管。
- FastAPI 访客客服页：查询上架商品、发起多轮文字咨询、在明确同意后留资，并请求人工处理。
- SQLite 本地数据存储；Ollama 与 DeepSeek Provider 配置路径。
- 原有销售训练、模拟对话与评分功能。

这是一套早期 MVP。模型 Provider 配置存在不代表真实 AI 已通过验收；此前模型关闭时的 HTTP/浏览器演示，以及 Codex 辅助人工回复，都不代表网站已自动调用模型成功。当前不要用它自动承诺退款、优惠、付款、订单修改或发货。

## Windows 本地启动

需要 Git 和 Python 3.12。依赖锁文件、启动入口和下面的命令均位于上述开发分支。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

在本机受保护的编辑器中为 `.env` 设置强随机 `ADMIN_API_TOKEN`；不要把密钥或令牌发到聊天、截图、日志或 Git。先保持模型关闭。在两个 PowerShell 窗口中都设置相同的隔离客服数据库和模型开关，再分别启动服务：

窗口一（FastAPI）：

```powershell
$env:SUPPORT_DB_PATH = ".\data\db\support-debug.db"
$env:MODEL_ENABLED = "false"
$env:LLM_PROVIDER = "ollama"
.\.venv\Scripts\python.exe -m uvicorn agent_skill.api_server:app --host 127.0.0.1 --port 8080 --workers 1 --no-access-log
```

窗口二（Streamlit）：

```powershell
$env:SUPPORT_DB_PATH = ".\data\db\support-debug.db"
$env:MODEL_ENABLED = "false"
$env:LLM_PROVIDER = "ollama"
$env:SUPPORT_API_URL = "http://127.0.0.1:8080"
.\.venv\Scripts\python.exe -m streamlit run demo/app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true
```

访客页为 <http://127.0.0.1:8080/chat>，管理端为 <http://127.0.0.1:8501>。管理员令牌只在本机登录页输入。不要把管理端、管理 API 或 Ollama 端口直接暴露到公网。

## 建议的调试顺序

先验证两个网页能打开、管理员能登录、商品和知识能保存并读回；然后用访客页提问，确认模型关闭时明确显示 `DISABLED` 并进入人工队列。完成这一步后，再按设备资源评估安装 Ollama，单独运行真实模型验收。逐步检查项和常见状态见[本地逐步调试指南](docs/DEBUGGING_GUIDE_ZH.md)。

`OFFLINE_READY` 只代表静态配置检查通过；`DISABLED` 表示模型主动关闭；`BLOCKED` 表示凭据、网络、服务或资源条件阻止了调用；`NOT_RUN` 表示没有发起真实请求。上述状态都不能记为真实 AI `PASS`。模型尚未在你的电脑上验收前，人工确认和接管仍是必要流程。

## 详细资料

- [当前开发分支的产品说明与截图](https://github.com/cvcingcvc-code/sales-skill-ai/blob/codex/s2-windows-first-run/README.md)
- [本地模型部署与验收](https://github.com/cvcingcvc-code/sales-skill-ai/blob/codex/s2-windows-first-run/docs/LOCAL_MODEL_DEPLOYMENT.md)
- [用户操作手册](https://github.com/cvcingcvc-code/sales-skill-ai/blob/codex/s2-windows-first-run/docs/USER_GUIDE.md)
- [部署边界与安全要求](https://github.com/cvcingcvc-code/sales-skill-ai/blob/codex/s2-windows-first-run/docs/DEPLOYMENT.md)
- [中文产品路线图](https://github.com/cvcingcvc-code/sales-skill-ai/blob/docs/self-hosted-store-assistant-roadmap/docs/PRODUCT_ROADMAP_ZH.md)

支持范围、真实模型状态和未实现能力以所克隆分支中的文档及验收记录为准。下载不代表一键部署、长期公网运行、真实模型验收或兼容任意平台。
