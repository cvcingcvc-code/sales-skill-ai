# 本地逐步调试指南

这份指南针对 `codex/s2-windows-first-run` 开发分支中的客服 MVP，按“先让应用稳定运行，再接本地模型”的顺序推进。GitHub 默认 `master` 是较早的销售训练版本；先从仓库首页给出的命令克隆开发分支，再照本指南操作。一次只处理一个阶段；前一阶段没有通过时，先停下来记录错误，不要同时改数据库、模型配置和业务代码。建议先用合成商品，不要把真实客户资料或凭据放进调试数据。

## 调试前先保护现有数据

在仓库根目录检查分支和工作区。不要用 `git reset --hard`、`git clean` 或覆盖 `.env` 来解决启动问题。

```bash
git branch --show-current
git status --short --branch
git rev-parse HEAD
```

第一次设置本机环境时，从 `.env.example` 复制 `.env`；已有 `.env` 时不要覆盖。用本机受保护的编辑器设置一个长随机 `ADMIN_API_TOKEN`。令牌不要写进命令历史、截图、问题报告或 Git。

调试时建议将客服数据库指向单独文件，避免碰已有商品和会话。Windows PowerShell：

```powershell
$env:SUPPORT_DB_PATH = ".\data\db\support-debug.db"
$env:MODEL_ENABLED = "false"
$env:LLM_PROVIDER = "ollama"
```

macOS/Linux：

```bash
export SUPPORT_DB_PATH="./data/db/support-debug.db"
export MODEL_ENABLED="false"
export LLM_PROVIDER="ollama"
```

API 和 Streamlit 两个终端都要设置相同的 `SUPPORT_DB_PATH`、`MODEL_ENABLED` 和 `LLM_PROVIDER`。每个新终端都需要重新设置。数据库路径相对于启动命令所在的仓库目录；不要把调试路径指向已有生产或个人数据库。

## 阶段 1：检查 Python、依赖和静态配置

按 [README](../README.md) 的系统对应步骤安装 Python 3.12 和锁定依赖。随后运行本机预检：

```powershell
# Windows PowerShell
.\.venv\Scripts\python.exe scripts\check_local_setup.py --json
```

```bash
# macOS / Linux
.venv/bin/python scripts/check_local_setup.py --json
```

预期状态是 `OFFLINE_READY`。`CONFIGURATION_REQUIRED` 表示某项静态配置或依赖还没准备好；根据输出的固定检查项逐项处理。该脚本不会连接网络或 Ollama，也不会创建/打开数据库，所以 `OFFLINE_READY` 只通过静态预检，不代表两个服务能启动或模型可用。

检查失败时，先核对 Python 是否为 3.12 或更新版本、依赖是否从仓库锁文件安装、`MODEL_ENABLED` 是否明确为 `true`/`false`、管理员令牌是否存在，以及 `DB_PATH` 与 `SUPPORT_DB_PATH` 是否为不同且可写的位置。预检不会输出这些变量的值。

## 阶段 2：先启动模型关闭的应用

保持 `MODEL_ENABLED=false`。在仓库根目录的第一个终端启动 API：

```powershell
# Windows PowerShell
\.venv\Scripts\python.exe -m uvicorn agent_skill.api_server:app --host 127.0.0.1 --port 8080 --workers 1 --no-access-log
```

```bash
# macOS / Linux
.venv/bin/python -m uvicorn agent_skill.api_server:app --host 127.0.0.1 --port 8080 --workers 1 --no-access-log
```

第二个终端设置同一组调试环境变量后启动 Streamlit：

```powershell
# Windows PowerShell
\.venv\Scripts\python.exe -m streamlit run demo/app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true
```

```bash
# macOS / Linux
.venv/bin/python -m streamlit run demo/app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true
```

预期访问地址：

- 访客客服：<http://127.0.0.1:8080/chat>
- 管理工作台：<http://127.0.0.1:8501>
- API 健康状态：<http://127.0.0.1:8080/skill/health>

如果端口占用，先确认是否已有你启动的实例；不要直接结束不认识的进程。可改用空闲端口，但 API 端口变化时还要将 `SUPPORT_API_URL` 改成新的地址并重启 Streamlit。管理员登录失败时，检查两个服务是否读取同一个 `ADMIN_API_TOKEN`，但不要打印或分享令牌。

## 阶段 3：验证商品、知识和人工接管

在管理工作台“产品与知识库”中建立少量虚构资料，例如：

当前后台没有独立的资料审核状态；录入者需要在启用前自行核对内容。商品/知识的启用开关不代表系统完成了审核。

- 商品：`演示数字资料包 A`，价格使用人民币分，描述写清包含内容和适用条件。
- FAQ：如何下载、支持什么软件、更新期限。
- 售后政策：明确哪些情况由人工核实；不要写入没有依据的退款承诺。

先确认管理员页面能读回已保存资料，再打开访客 `/chat` 查询商品列表并提问。对于已有资料的问题，系统在 `MODEL_ENABLED=false` 时不会生成 AI 回答，应清楚显示 `DISABLED` 并转人工；管理后台应能看到接管请求并发送人工回复，访客刷新后应看到该回复。

故意询问资料没有覆盖的内容时，系统可能在检索后直接转人工，并标记 `NOT_CALLED`，因为没有充分资料时无需调用模型。这证明的是知识缺口保护与人工路由，不是模型拒答能力。商品、知识和会话数据写入 SQLite；如果要检查重启持久化，可正常停止两个服务后用相同环境变量重新启动，再从后台检查记录。不要删除数据库来“重置”调试，另建新的隔离数据库文件即可。

## 阶段 4：确认 Ollama 环境，不急着下载模型

先按[本地模型部署与验收说明](https://github.com/cvcingcvc-code/sales-skill-ai/blob/codex/s2-windows-first-run/docs/LOCAL_MODEL_DEPLOYMENT.md)评估设备和安装权限。本指南不替你下载权重。确认安装后，在本机查看服务和已安装模型：

```powershell
# Windows PowerShell
ollama --version
ollama list
Invoke-RestMethod http://127.0.0.1:11434/api/tags
```

```bash
# macOS / Linux
ollama --version
ollama list
curl --fail http://127.0.0.1:11434/api/tags
```

如果命令不存在、服务未启动、模型列表为空或磁盘/内存不足，先停在这里。不要通过反复换模型或修改 Gateway 来绕过环境问题。以 `ollama list` 中实际存在的准确模型标签填写 `.env` 的 `OLLAMA_MODEL`；API Key 不需要提供给 Ollama。

## 阶段 5：单独开启并执行真实验收

只有在 Ollama 已运行、模型已由你决定并安装、标签准确、设备可承受时，才在 `.env` 配置 `LLM_PROVIDER=ollama`、`OLLAMA_BASE_URL=http://127.0.0.1:11434` 和 `OLLAMA_MODEL`。然后将 API 与 Streamlit 两个终端的 `MODEL_ENABLED` 都设为 `true`，重启两个服务。进程环境变量优先于 `.env`，旧终端也可能保留旧值。

先在后台“模型状态和调用成本”查看状态。`configured` 或 `UNVERIFIED` 仅表示配置被读入，不能当作连通成功。随后运行仓库的真实 Provider 验收脚本；报告放在仓库外：

```powershell
# Windows PowerShell
\.venv\Scripts\python.exe scripts\verify_live.py --provider ollama --output "$env:TEMP\sales-local-acceptance.json"
```

```bash
# macOS / Linux
.venv/bin/python scripts/verify_live.py --provider ollama --output /tmp/sales-local-acceptance.json
```

该脚本使用临时 SQLite 和合成商品，发起真实商品知识请求，并检查留资与人工回复链路。退出码 `0` 是脚本覆盖范围内的 `PASS`，`1` 是 `FAIL`，`2` 是 `BLOCKED`。脚本目前不会证明真实商家资料效果、长期稳定性，也不会单独验收完整的“知识外模型拒答”与“服务中断时访客降级”场景；这些应在受控的访客/管理界面继续逐项演练并记录实际模型名、回复、延迟和状态。不得将 Mock、规则回复或人工回复写成真实模型通过。

## 常见状态怎么理解

| 状态 | 含义 | 调试动作 |
| --- | --- | --- |
| `DISABLED` | 管理员关闭了模型；没有产生模型回答 | 继续验证商品查看和人工接管；不要记为 AI PASS |
| `BLOCKED` | 配置、凭据、网络、服务或资源预算阻止真实调用 | 按错误码检查前置条件，保存脱敏结果；不要改用 Mock 冒充成功 |
| `NOT_RUN` / `NOT_CALLED` | 没有执行真实模型请求，或该问题被检索/规则提前处理 | 明确记录未运行；该项不能证明模型能力 |
| `UNVERIFIED` | 配置存在，但尚未用真实调用确认连通性 | 运行对应 Provider 的真实验收 |
| `MOCK` | 自动化测试使用模拟模型 | 仅作离线回归证据，不是模型验收 |
| `PASS` | 已执行检查范围内的验证并通过 | 记录测试范围、实际 Provider 和模型名，不扩展解释为生产可用 |

## 记录问题与寻求帮助

每次只记录一个问题：操作系统与 Python 版本、当前分支/HEAD、执行的命令（移除令牌和敏感路径）、完整错误码、页面显示状态、模型是否实际被调用。不要附 `.env`、环境变量转储、管理员令牌、API Key、真实顾客聊天或数据库文件。日志应先脱敏，再放入 Issue；可以附上合成资料、复现步骤和脱敏截图。

本指南不声称本地模型已经在你的电脑上安装或验收。自动 AI 客服的可用范围必须由你的实际环境完成真实 Provider 请求后再决定；此前的模型关闭演示和 Codex 辅助人工回复都不算模型通过。
