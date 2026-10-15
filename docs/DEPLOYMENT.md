# 本地运行与云端部署说明

本轮交付代码及配置说明，**没有进行真实公网部署**。以下服务/代理配置为需部署方核对的示例，未在生产服务器验证；不包含未验证的Docker构建。

## 准备运行环境

使用Python 3.12，在仓库根目录执行；已有虚拟环境和本地配置应保留：

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.lock
if [ ! -e .env ]; then cp .env.example .env; fi
```

不要把 `.env`、凭据、客户数据库、验收中的客户内容或模型权重提交Git。云环境优先使用环境/Secret设置；dotenv不会覆盖已注入的环境变量。不能将会暴露凭据的环境转储、请求头或完整上游异常放入报告。

| 变量 | 配置说明 |
| --- | --- |
| `LLM_PROVIDER` | 本轮真实云验收用 `deepseek`；本地服务部署后才使用 `ollama` |
| `DEEPSEEK_API_KEY` | 通过Secret设置或本机安全编辑器提供，无值明确BLOCKED |
| `DEEPSEEK_MODEL` | 配置账户当前可用模型；模板默认名称不代表账户权限或未来可用性 |
| `DEEPSEEK_BASE_URL` | 官方默认 `https://api.deepseek.com/v1`；客户端追加 `/chat/completions`，不要重复填写完整请求路径 |
| `MODEL_TIMEOUT_SECONDS` | 默认30秒，支持有限范围内正数；反向代理超时须与之匹配 |
| `ADMIN_API_TOKEN` | 用户在本机用密码管理器/可信随机生成器创建并安全保存强随机令牌，不输出到聊天、报告或命令历史 |
| `DB_PATH` | 训练数据库，默认 `./data/db/learners.db` |
| `SUPPORT_DB_PATH` | 独立客服数据库，默认 `./data/db/support.db`；部署时指向持久化私有目录，勿与训练数据库共用文件 |
| `SUPPORT_API_URL` | Streamlit后台连接地址，默认 `http://127.0.0.1:8080`；不要使用含凭据的URL |
| `DEEPSEEK_INPUT_PRICE_PER_MILLION`、`DEEPSEEK_OUTPUT_PRICE_PER_MILLION` | 选填经核对的每百万token单价；留空显示未知。必须使用同一币种，费用仅为估算，系统没有账单币种验证 |
| `OLLAMA_BASE_URL`、`OLLAMA_MODEL` | 可选本地适配器；默认地址 `http://127.0.0.1:11434`，模型需显式配置。本轮未部署服务或权重 |

`.env.example` 还有Gemini/Claude历史兼容配置，本轮不要求额外申请这些Key。配置存在只说明静态检查通过，真实可用性应以一次实际请求为准。历史公开Key的撤销/轮换未验证，应由账户持有人按 [SECURITY.md](../SECURITY.md) 核验，不能仅删除当前文件。

网关在创建时读取环境配置，运行中的 API 不会自动热加载后来修改的 `.env` 或 Secret。配置更新后应先确认云环境已将设置应用到当前实例，再重启 API 和 Streamlit，使用新进程运行下方真实验收脚本；若平台只对新任务注入设置，需在新任务中重试。不要为解决旧进程仍显示 `BLOCKED` 而重复申请密钥。

云环境需要安装依赖时通常需访问 `pypi.org`、`files.pythonhosted.org`；真实DeepSeek请求需访问配置地址，官方默认 `api.deepseek.com:443`。如存在网络允许列表，应在环境设置中追加实际需要的目的地，保留已有条目；保存草稿不代表运行实例已生效。保留TLS/证书校验，不使用关闭验证作为排障方法。

## 本机和私有管理入口

两个终端分别运行，绑定127.0.0.1，API使用单worker：

```bash
.venv/bin/uvicorn agent_skill.api_server:app --host 127.0.0.1 --port 8080 --workers 1 --no-access-log
```

```bash
.venv/bin/streamlit run demo/app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true
```

访客页是 `http://127.0.0.1:8080/chat`。Streamlit在 `http://127.0.0.1:8501`；服务端和每个浏览器分别配置/输入同一个管理员令牌，浏览器不因服务端持有token而自动登录。切换到“客服工作台”管理商品、知识、会话和接管队列；后台经 `SUPPORT_API_URL` 调用有鉴权的API。远端机器的8501应仅通过受信任VPN、SSH隧道或私有平台访问。不要将Streamlit直接暴露公网。

商品应填写真实名称、准确描述、整数分价格、有效购买链接和上下架状态；知识可为商品文档、FAQ或售后政策。第一版仅支持CNY。资料须人工审核，文本里出现指令也不会赋予模型后台权限，但后端会引用已管理文本本身，错误商家资料不能自动修正。

## 运行与验证

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/demo_workflow.py --mock
.venv/bin/python scripts/demo_workflow.py
.venv/bin/python scripts/verify_live.py --provider deepseek --output /tmp/sales-live-acceptance.json
```

Mock演示显式标为 `MOCK_DEMO_COMPLETE`；脚本创建临时隔离数据库和合成商品，不污染商家记录，不是真实模型通过。默认演示无Key/网络失败exit 2、BLOCKED，同时继续验证商品、会话、需求、同意线索和人工回复等独立链路。真实验收脚本不替代为Mock，凭据缺失时exit 2、BLOCKED，exit 1表示验收失败，exit 0才表示合成资料上的真实Provider及闭环通过。临时数据库随后删除；报告写到仓库外。该验收不等于真实商品和真实网站的生产验收。

测试入口是unittest的 `tests/`；根目录历史 `test_gemini.py` 是手工联网脚本，不应盲跑pytest。

## 云端服务与公网边界示例

建议一个API进程、私有Streamlit、反向代理对公网提供同源访客页面。旧训练会话存进程内，重启会丢失未完成训练；客服SQLite数据要放持久化磁盘。多个APIworker/多机运行不在本轮验证范围。

以下systemd示例假定部署目录 `/srv/sales-skill-ai`、服务用户 `sales-agent` 已由部署方准备。`/etc/ai-sales-agent.env` 应仅让必要服务主体读取，内含上面的配置，数据库路径建议使用绝对路径。示例不会创建用户、写入Secret或启动生产服务：

```ini
[Unit]
Description=AI Sales Agent API
After=network-online.target
Wants=network-online.target

[Service]
User=sales-agent
WorkingDirectory=/srv/sales-skill-ai
EnvironmentFile=/etc/ai-sales-agent.env
ExecStart=/srv/sales-skill-ai/.venv/bin/uvicorn agent_skill.api_server:app --host 127.0.0.1 --port 8080 --workers 1 --no-access-log
Restart=on-failure
UMask=0077

[Install]
WantedBy=multi-user.target
```

下面Nginx示例仅允许公网 `/chat`、`/support/` 和 `/static/customer.js`。域名、TLS证书路径、限流和超时需要部署方实际配置；证书文件应来自受信任的签发流程。禁止将 `/admin`、`/skill`、`/docs`、`/openapi.json` 或8501一起代理到公网。

```nginx
# 位于 nginx 的 http 配置块内；额度需根据实际流量调整。
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
        proxy_read_timeout 75s;
    }
    location / { return 404; }
}
```

服务器防火墙只允许公网访问代理TLS端口；8080/8501/Ollama端口保持私有。以上限流依赖代理真实来源IP配置，不构成已验证的抗滥用方案；共享出口、可信代理和调用费用预算需部署方按实际访问规模配置。HTTP→HTTPS、证书续期及TLS策略也由部署方完成。

## 数据与运行责任

- SQLite启用WAL；备份应使用SQLite在线备份或停服务一致性快照，不能在写入中仅复制主 `.db` 文件而忽略WAL。分别备份训练与客服数据库，并验证恢复。
- 数据库及备份包含对话、需求和经同意提交的联系方式，须限制文件权限及备份访问。访客token hash不替代磁盘/备份保护；当前没有自动加密数据库。
- 部署方应明确对话和线索的保存期限、隐私告知、删除/导出受理、备份保留及日志策略。本轮没有自动清理或删除API，人工维护需同时处理关联消息、线索、接管和usage，并保留必要同意记录。
- 联系线索表单必须明确同意；常规聊天只提供有限模式脱敏，无法保证识别全部敏感信息。不得要求访客发送凭据、证件或银行卡。
- 投诉、未知知识、特殊优惠、模型异常及主动申请进入人工队列；AI暂停，管理员回复后不会自动恢复AI。网站通过“查看人工回复”刷新，未实现实时推送。
- 没有支付、退款、改价或订单执行工具。任何真实交易和客户资料修改由业务后端与人工权限流程处理，不能以客服文字当作执行结果。
- 用量和估算仅反映客服调用；未返回usage/未配置单价时显示未知。费用币种由配置决定，部分未知时不能把已知小计当作总成本。

上线前仍需实际凭据/网络、真实模型验收、商家资料确认和以上部署配置。本文件不声称已经完成这些操作。
