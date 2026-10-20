# V2 本地运行与云端部署说明

本轮先完成应用，模型保持暂停：不安装 Ollama、不下载权重、不执行真实模型请求，**没有进行真实公网部署**。以下 systemd/Nginx 为部署方需核对的示例，未在生产服务器验证。Windows/macOS/Linux 与以后模型安装另见 [LOCAL_MODEL_DEPLOYMENT.md](LOCAL_MODEL_DEPLOYMENT.md)，业务操作见 [USER_GUIDE.md](USER_GUIDE.md)。

## 准备应用与配置

以下为 macOS/Linux 命令，使用 Python 3.12，在仓库根目录执行；已有环境和本地配置请保留：

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.lock
if [ ! -e .env ]; then cp .env.example .env; fi
```

新模板默认 `MODEL_ENABLED=false`、`LLM_PROVIDER=ollama`、`OLLAMA_MODEL` 为空。旧 `.env` 不会自动覆盖，升级时应手动补上这些设置；为保留旧部署兼容，未设置开关的旧环境仍沿用启用行为。不要把 `.env`、令牌、客户数据库、真实验收内容或权重提交 Git。云环境优先使用 Secret/环境设置；dotenv 不覆盖已注入变量，不转储环境或记录完整上游异常。

| 变量 | 配置说明 |
| --- | --- |
| `MODEL_ENABLED` | 本轮保持 `false`；只接受显式 `true/false`，以后模型部署并确认后才开启 |
| `LLM_PROVIDER` | 新部署为 `ollama`；DeepSeek 为以后显式切换的可选云 Provider |
| `OLLAMA_BASE_URL`、`OLLAMA_MODEL` | 默认本机 `http://127.0.0.1:11434`；模型留空，不安装/下载，不需要云 Key |
| `MODEL_TIMEOUT_SECONDS` | 默认 30 秒；反向代理超时需按实际配置匹配 |
| `MODEL_MAX_INPUT_TOKENS`、`MODEL_MAX_OUTPUT_TOKENS` | 默认 16000/4096；输入按 UTF-8 字节加封装余量保守估算，输出向 Provider 传硬上限 |
| `MODEL_DAILY_CALL_LIMIT`、`MODEL_DAILY_TOKEN_LIMIT` | 客服共享 UTC 日预算，默认 200 次/200000 tokens；0 为禁止调用 |
| `MODEL_DAILY_COST_LIMIT` | 可选每日估算金额上限；留空不启用，填写时须同时配置对应 Provider 有效输入/输出单价 |
| `OLLAMA_INPUT_PRICE_PER_MILLION`、`OLLAMA_OUTPUT_PRICE_PER_MILLION` | 选填本地计价口径，不默认零，不代表真实设备账单 |
| `SUPPORT_CREATE_LIMIT_PER_HOUR`、`SUPPORT_MESSAGE_LIMIT_PER_MINUTE` | 默认每来源每小时 20 次创建、每已授权会话每分钟 20 条消息；0 拒绝该类请求 |
| `SUPPORT_RETENTION_DAYS` | 默认 30 天，管理页预览的初始期限；不自动安装定时清理 |
| `ADMIN_API_TOKEN` | 用密码管理器生成强随机私密令牌，API、工作台服务与浏览器手动登录使用同一值，不打印 |
| `DB_PATH` | 训练数据库，默认 `./data/db/learners.db`；部署时使用私有持久目录 |
| `SUPPORT_DB_PATH` | 客服数据库，默认 `./data/db/support.db`，不能与训练数据库共用文件 |
| `SUPPORT_API_URL` | 工作台连接的 API，默认 `http://127.0.0.1:8080`；不使用含凭据的 URL |
| `DEEPSEEK_API_KEY`、`DEEPSEEK_MODEL`、`DEEPSEEK_BASE_URL` | 以后选择 DeepSeek 时配置；官方地址默认 `https://api.deepseek.com/v1`，客户端追加 `/chat/completions` |
| `DEEPSEEK_INPUT_PRICE_PER_MILLION`、`DEEPSEEK_OUTPUT_PRICE_PER_MILLION` | 以后云端费用估算用同币种、已核对的每百万 token 单价；留空为未知 |

每日预算只覆盖客服，**不覆盖旧 CLI、Streamlit 销售训练和训练 API 的每日总调用**；这些路径仅共享模型开关和单次输入/输出上限。UTC 切日对应北京时间 08:00。调用前保守预留最大输入/输出，失败、崩溃或缺用量保留预留额度；删除会话不重置全局计数。费用是配置币种估算，未知不是零，不是账单或付款功能。

`.env.example` 中 Gemini/Claude 为历史兼容配置，本轮无需申请 Key。以后使用 DeepSeek 前，账户持有人须按 [SECURITY.md](../SECURITY.md) 核验历史公开凭据撤销/轮换；本地方案无需先完成云 Key 配置。

模型配置由网关创建时读取，不会热加载后来修改的 `.env` 或 Secret。更新后先确认云实例已注入配置，再重启 API 和 Streamlit；平台只向新任务注入时在新任务重试。状态页面的 `configured` 只证明静态配置，不能代替真实请求。

安装 Python 依赖通常需要 `pypi.org` 和 `files.pythonhosted.org`。当前暂停阶段不需连模型目标；以后 DeepSeek 需访问配置域名（官方默认 `api.deepseek.com:443`），本地 Ollama 由后端经本机/安全私网访问。如有允许列表，应追加目的地并保留原条目，确认运行状态，不能只凭草稿认定生效。保留 TLS/证书校验。

本任务已保存云环境安装/启动草稿，保持模型暂停和本地 Provider；保存不等于应用或发布。复用前在环境设置核对并保存，再发布。旧配置若仍显示 DeepSeek Key 声明或云 Provider 建议，可在设置中移除过时要求、选择本地建议；本地流程不需要该 Key。新任务恢复仍需重新启动服务和验证。

## 本机与私有管理入口

先运行无网络静态预检：

```bash
.venv/bin/python scripts/check_local_setup.py --json
```

`OFFLINE_READY` 不连接模型、不打开数据库，也不证明实际创建权限或回答质量。修正静态待补项后，在两个终端分别运行，绑定回环地址、API 单 worker：

```bash
MODEL_ENABLED=false LLM_PROVIDER=ollama .venv/bin/python -m uvicorn agent_skill.api_server:app --host 127.0.0.1 --port 8080 --workers 1 --no-access-log
```

```bash
MODEL_ENABLED=false LLM_PROVIDER=ollama .venv/bin/python -m streamlit run demo/app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true
```

访客页为 `http://127.0.0.1:8080/chat`，后台默认 `http://127.0.0.1:8501`。端口已占用时选择空闲端口并更新浏览器地址；API 端口改变还须同步 `SUPPORT_API_URL` 并重启工作台，不应结束未知的已有服务。本任务新 UI 暂用 `8502`，通用示例仍使用 `8501`。

浏览器需手动输入管理员令牌，不能因服务端已有 token 自动登录。工作台保留销售训练，另有六个客服页：产品与知识库、AI 客服测试、客户会话、人工接管队列、模型状态和调用成本、隐私与审计。远端后台仅通过受信任 VPN、SSH 隧道或私有平台访问，不将 Streamlit 直接公开。

先录入真实且已审核的商品、价格（人民币分）、链接、上下架与知识文档/FAQ/政策。只支持 CNY；空库不会生成真实商品。资料仍视为不可信输入，模型不获后台写权限；错误资料需人工修正。

## 离线验证与以后真实验收

```bash
.venv/bin/python scripts/run_tests.py --quiet
.venv/bin/python scripts/demo_workflow.py --mock
MODEL_ENABLED=false .venv/bin/python scripts/demo_workflow.py
MODEL_ENABLED=false .venv/bin/python scripts/verify_live.py --provider ollama --output /tmp/sales-local-acceptance.json
```

首选 `run_tests.py`：隔离临时数据库及测试配置，禁止外连，只测试 Mock 合约和业务回归。它不会开启运行服务的模型；直接 unittest 继承暂停 `.env` 时可能改变原测试前提。根目录 `test_gemini.py` 是历史手工联网脚本，不属于该入口。

Mock 演示显式标为 `MOCK_DEMO_COMPLETE`，用临时数据库与合成资料验证业务、人工、恢复、撤回、关闭及删除。默认演示保持暂停时为 `DISABLED`、退出码 2，独立业务检查仍继续。真实验收不回退到 Mock，暂停时是 `BLOCKED / disabled`、不发模型请求；真实效果为 `NOT_RUN`。以后按本地部署文档选择设备与模型、显式开启并重启后再运行；退出码 0 是 PASS、1 是 FAIL、2 是 BLOCKED。报告留在仓库外，合成资料通过也不等于真实商家/公网生产验收。

可选浏览器检查使用已有 Node.js、Playwright、Chromium，指向隔离测试服务后执行 `node tests/browser_smoke.cjs`；可用 `SUPPORT_BROWSER_URL/CHROMIUM_PATH` 指定目标。它验证访客页面和隐私操作，不调用真实模型。

## 云端服务与公网边界示例

当前建议单 API 进程、私有 Streamlit、反向代理提供同源访客页。训练会话在进程内，重启丢失未完成训练；SQLite 放持久化磁盘。多机/多 worker 不在本轮部署验证范围。以后若云端应用连接个人电脑模型，只走受保护私网，不把 Ollama 裸露公网；电脑断电或离线时按不可用转人工。

systemd 示例假定 `/srv/sales-skill-ai`、服务用户 `sales-agent` 已由部署方准备。`/etc/ai-sales-agent.env` 仅必要主体可读，需包含 `MODEL_ENABLED=false`、`LLM_PROVIDER=ollama`、管理员令牌及绝对数据库路径。下面不会创建用户、写 Secret 或启动生产服务：

```ini
[Unit]
Description=AI Sales Agent API
After=network-online.target
Wants=network-online.target

[Service]
User=sales-agent
WorkingDirectory=/srv/sales-skill-ai
EnvironmentFile=/etc/ai-sales-agent.env
ExecStart=/srv/sales-skill-ai/.venv/bin/uvicorn agent_skill.api_server:app --host 127.0.0.1 --port 8080 --workers 1 --no-access-log --proxy-headers --forwarded-allow-ips=127.0.0.1
Restart=on-failure
UMask=0077

[Install]
WantedBy=multi-user.target
```

以下 Nginx 示例只公开 `/chat`、`/support/` 和客户脚本。域名、证书、超时、来源配置及额度需部署方核对，不能把 `/admin`、`/skill`、`/docs`、`/openapi.json` 或 Streamlit 端口一起代理公网。代理覆盖客户端自带的转发头；上方 Uvicorn 仅信任回环代理，若代理在别处需精确调整可信地址，不使用通配信任。

```nginx
# 位于 nginx 的 http 块内；额度按实际流量调整。
limit_req_zone $binary_remote_addr zone=sales_support:10m rate=2r/s;

server {
    listen 443 ssl;
    server_name support.example.com;
    ssl_certificate /etc/letsencrypt/live/support.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/support.example.com/privkey.pem;
    client_max_body_size 16k;

    location = /chat {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
    }
    location = /static/customer.js {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
    }
    location ^~ /support/ {
        limit_req zone=sales_support burst=10 nodelay;
        limit_req_status 429;
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header Authorization "";
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 75s;
    }
    location / { return 404; }
}
```

防火墙只让公网访问代理 TLS 端口；API、Streamlit、Ollama 保持私有。应用 SQLite 限流与代理限流互补，仍需根据真实来源、共享出口、连接数、请求体及实际规模设计保护。HTTP→HTTPS、证书续期、TLS 策略和代理运行配置仍由部署方完成，以上示例未经生产验证。

## 数据生命周期与运维

- 访客可关闭自己的会话，关闭后不可再发消息/留资/接管；仍可查看记录、确认撤回线索和确认删除。管理员处理完全部待办后才能显式恢复人工会话的 AI；关闭会话不能恢复，需新建。
- 撤回只删除线索；确认删除会话同时移除消息、线索、接管、用量及关联预算预留详情，保留固定事件/角色/哈希对象标识/时间审计。全局预算计数不随删除重置。
- 隐私页默认先预览，再明确确认清理。只清理已关闭、最后活动超出期限且没有待办的会话；执行时重新核对条件。没有默认定时任务，实际期限、审计保留、删除/导出受理仍由部署方制定。
- 应用删除不保证备份、WAL 或磁盘法证清除。数据库和备份含对话、需求与经同意联系方式，应限制访问并制定备份轮换及删除政策；数据库未自动加密。
- SQLite 备份使用在线备份或停服务一致快照，不能写入中只复制主 `.db` 忽略 WAL。训练与客服库分别备份，验证恢复；schema 增量升级前保留可恢复快照。
- 聊天仅有限模式脱敏，无法识别所有敏感信息；不要求访客发送凭据、证件或银行卡。联系方式通过明确同意表单收集。
- 未知资料、投诉、退款、特殊优惠、模型异常进入人工；人工答复不自动恢复 AI，网站手动刷新人工回复。没有付款、退款、改价或订单执行工具，真实交易仍走商家权限与人工流程。

上线前仍需真实模型/业务验收、商家资料审核及上述部署条件。本文件不声称这些事项已经通过。
