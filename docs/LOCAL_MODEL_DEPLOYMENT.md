# 本地模型部署与验收

本文档说明如何在自己的电脑上装好模型、让网站真正用上它，并跑通真实验收。

**已验证的参考环境（v1.0.0-beta.1）：**Windows 11 + Python 3.12.13 + Ollama 0.40.14
+ `qwen2.5:3b`（3.1B / Q4_K_M 量化，1.9 GB）+ NVIDIA GTX 1660 Ti（CUDA 可用，6 GB 显存）。
在该环境下 `scripts/verify_live.py --provider ollama` 返回 `PASS`、退出码 `0`，连续多次结果一致。
**这是一条被验证过的路径，不是对任何设备都成立的性能或可用性保证。**

下面同时提供 Windows、macOS、Linux 的通用路径；具体系统、驱动和模型支持以官方当时文档为准。
如果想先按阶段排查应用、商品录入和人工接管，再尝试 Ollama，请看[本地逐步调试指南](DEBUGGING_GUIDE_ZH.md)。

> **重要：运行本项目不会自动下载任何模型权重，也不会替你安装 Ollama。**
> 模型由你自己按设备条件选择并拉取，步骤见第 2、3 节。

## 1. 先运行应用，不接模型

按 [README](../README.md) 安装 Python 3.12 和锁定依赖。已有 `.env` 不要覆盖；首次部署从 `.env.example` 复制，并保留以下配置：

```dotenv
MODEL_ENABLED=false
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=
```

通过本机安全编辑器配置 `ADMIN_API_TOKEN`，使用密码管理器生成强随机私密值。不要把真实令牌提交到 Git、粘贴到聊天或用会保留历史的命令打印。Ollama 不需要 `DEEPSEEK_API_KEY`。

在仓库根目录运行离线预检：

```bash
# macOS / Linux
.venv/bin/python scripts/check_local_setup.py --json
```

```powershell
# Windows PowerShell
.\.venv\Scripts\python.exe scripts/check_local_setup.py --json
```

`OFFLINE_READY` 只表示 Python、依赖、管理员令牌存在性、配置和数据库路径的静态检查通过。检查器不会连接网络、请求 Ollama、打开数据库或验证回答质量；新建文件/目录仍以实际启动结果为准。`CONFIGURATION_REQUIRED` 会指出固定名称的待补项，不输出凭据或地址。显式开启模型后的 `ENABLED_NOT_VERIFIED` 仍不代表模型可用。

这时商品与知识管理、商品列表查询、会话记录、知情同意留资、人工接管/回复及数据清理可以使用。需要模型选择资料的问答会明确显示 `DISABLED`，并进入人工流程；规则寒暄和人工答复可以工作。销售训练的真实角色扮演和模型评分也须等模型启用后验收。离线 Mock 仅用于自动化开发与合成演示。

启动应用的跨平台命令如下，两条服务命令分别在两个终端运行，工作目录均为仓库根目录：

```bash
# macOS / Linux
.venv/bin/python -m uvicorn agent_skill.api_server:app --host 127.0.0.1 --port 8080 --workers 1 --no-access-log
.venv/bin/python -m streamlit run demo/app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true
```

```powershell
# Windows PowerShell
.\.venv\Scripts\python.exe -m uvicorn agent_skill.api_server:app --host 127.0.0.1 --port 8080 --workers 1 --no-access-log
.\.venv\Scripts\python.exe -m streamlit run demo/app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true
```

## 2. 按实际设备选择试运行模型

先记录操作系统、可用内存、GPU 型号/显存、可用磁盘和期望并发。模型权重、上下文缓存和运行程序都会占用内存，参数量相同也可能因量化方式和上下文长度而有明显差异。

| 设备条件 | 初次试运行建议 | 需要实际测量的限制 |
| --- | --- | --- |
| 大约 8 GB 系统内存，主要使用 CPU | 从小型约 3B 量化模型、短上下文、单会话试起 | 系统剩余内存、加载是否成功、首字延迟；资源紧张时继续缩小模型 |
| 大约 16 GB 或以上系统内存 | 先试 3B，再视内存与速度试约 7B 量化模型 | CPU 吞吐、长上下文占用、多轮对话延迟 |
| 有受支持的独立 GPU，约 6–8 GB 或以上可用显存 | 从 3B / 7B 量化模型实测，核对官方 GPU 支持 | 权重能否完整放入显存、上下文缓存、是否部分回落 CPU |
| Apple Silicon 统一内存 | 按系统可用统一内存从小模型开始 | 操作系统与应用共享内存；不能把总内存全部用于模型 |

这些是试运行起点，不是可运行性或性能保证。没有 GPU 也可以尝试受支持的 CPU 路径，但本项目没有承诺 CPU 响应速度。先验证单用户流程，再决定是否需要更大机器。不要为试运行一次性下载多个大型模型。

## 3. 以后安装官方 Ollama

官网：[https://ollama.com/download](https://ollama.com/download)，官方文档：[https://docs.ollama.com](https://docs.ollama.com)。

- Windows：使用官网当前 Windows 安装包，完成后新开 PowerShell 确认命令可用。
- macOS：使用官网当前 macOS 安装包，并确认系统版本和芯片受支持。
- Linux：按[官方 Linux 说明](https://docs.ollama.com/linux)选择支持的安装路径，先阅读安装步骤和需要的系统权限；GPU 用户另核对驱动支持。不需要盲目执行未经阅读的远程安装脚本。

本项目不替用户执行安装或模型下载。安装时保留 TLS、签名和完整性校验，不使用来历不明的安装包。
Windows 用户请从官网下载官方安装包，并核对数字签名（发布者应为 `Ollama Inc.`）。

以后在模型电脑上确认服务：

```bash
ollama --version
ollama list
# 如果应用/系统服务尚未启动 Ollama，再执行；已监听时无需重复启动。
ollama serve
```

`ollama serve` 会占用当前终端，后续命令在另一个终端执行。某些安装方式会自动启动后台服务，应使用该系统的官方管理方式。

选择当前官方模型库中确实存在且符合用途和许可的标签。例如以下标签仅作为小模型试运行示例，不代表最新或对当前设备的性能承诺：

```bash
ollama pull qwen2.5:3b
ollama list
```

第一次 `pull` 需要联网并下载权重，确认磁盘空间和流量后再运行。以本机 `ollama list` 的准确名称为准，填入 `OLLAMA_MODEL`。安装服务和下载权重以后，应用仍先保持 `MODEL_ENABLED=false`。

> **踩坑提示：手动放入的 blob 会被启动清理。** Ollama 服务启动时会删除没有 manifest 引用的
> blob（日志出现 `total unused blobs removed: N`）。如果你出于离线等原因手动把权重文件放进
> 模型目录，**必须紧接着执行一次 `ollama pull <标签>`** 让 Ollama 自己写好 manifest，
> 否则下一次启动时大权重可能已被删除。

## 4. 配置、启用和重启

先在 `.env` 配置已安装标签和地址。例如本机列表确实包含上述示例时：

```dotenv
MODEL_ENABLED=false
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen2.5:3b
MODEL_MAX_INPUT_TOKENS=4000
MODEL_MAX_OUTPUT_TOKENS=1024
MODEL_TIMEOUT_SECONDS=60
```

小机器先用 `4000 / 1024` 的输入/输出上限。输入按网关组装后的 UTF-8 字节数作保守 token 上界检查，会把系统说明、历史、商品与政策一起计算；它不是 Ollama 分词器的精确 token 数，中文资料较长时可能提前拒绝。输出上限传给 Provider。实际模型的上下文还须容纳输入与输出，并留出协议开销；缩短上下文也能降低内存占用。超限时减少检索资料、历史或输出长度，不能靠无限增大上限解决小机器内存问题。

检查配置后，将 `MODEL_ENABLED=true`，重启 API 和 Streamlit，使新进程读取配置。已有终端/系统服务设置的环境变量优先于 `.env`；只改文件而不重启，或父进程仍保留旧变量，均可能继续使用旧配置。

## 5. 最后才做真实验收

运行时需能从后端访问所配置的 Ollama，脚本不会自动下载模型，也不会回退到云模型：

```bash
# macOS / Linux；报告放在仓库外。
.venv/bin/python scripts/verify_live.py --provider ollama --output /tmp/sales-local-acceptance.json
```

```powershell
# Windows PowerShell；报告放在临时目录。
.\.venv\Scripts\python.exe scripts/verify_live.py --provider ollama --output "$env:TEMP\sales-local-acceptance.json"
```

| 结果 | 含义与处理 |
| --- | --- |
| `PASS`，退出码 0 | 当前真实 Provider 与合成商品→答复→同意线索→人工回复链路通过；还须用审核过的真实商家资料验收 |
| `FAIL`，退出码 1 | 请求或业务响应不符合合约，检查可复现输入及配置；不能改用 Mock 算真实通过 |
| `BLOCKED`，退出码 2 | 模型关闭、标签/配置缺失、服务未启动、超时或连接不可达等前置条件未满足 |

小模型需要正确输出资料选择 JSON；能生成聊天文字不代表通过本项目合约。模型不支持当前输出要求时，保留失败记录，选择合适模型或单独调整提示词并补回归测试。真实模型通过前，验收报告仍保留“未验收”，不能把离线测试数量当作真实 AI 效果。

**关于模型输出稳定性。** 资料选择是分类任务，不是创作任务。项目对这次调用固定 `temperature=0`，
保证同一输入得到同一结论；Provider 默认采样下结论会随机翻转。若你换用其他模型后出现
“同一问题有时转人工、有时按目录作答”，先确认请求确实带上了 `temperature=0`，
再考虑模型是否太小、是否支持中文 JSON 输出。

## 6. 网站与模型的连接方式

最简单的本地方式是：浏览器→本机 API→同一电脑的 Ollama。访客页是 `http://127.0.0.1:8080/chat`，工作台是 `http://127.0.0.1:8501`；启动命令见 [README](../README.md)。浏览器只访问应用 API，不能直接请求 Ollama。

以后网站部署在云端、模型仍跑家中电脑时，云后端需要通过经过访问控制的私有网络连接模型电脑，并另行验证连接、访问权限和可用性。不能把 `11434` 模型端口直接暴露公网，也不能让访客浏览器连接自己机器的 `localhost`。本轮未建设或验收这条云到本机通道。模型电脑关机、休眠或网络断开时，AI 可能不可用，应用应明确报错并转人工；需要稳定服务时再设计常在线主机与运维方式。

应用暂按单 worker、单机 SQLite 运行。公开访问前还要完成 HTTPS、私有后台、代理限流、数据库备份和保留策略，见 [DEPLOYMENT.md](DEPLOYMENT.md)。Ollama API 无云端调用费不等于计算成本为零；设备、电力和运维成本由部署者评估。

如以后临时改用 DeepSeek，显式切换 `LLM_PROVIDER=deepseek`，安全设置相应密钥/当前模型/API 根地址、启用后重启，再用 `--provider deepseek` 验收。Provider 不会在失败后自动互相切换。

## 7. 常见错误排查

先看后台「模型状态」和验收脚本的退出码，再对照下表。**不要**用切 Mock 的方式绕过失败。

| 现象 | 常见原因 | 处理 |
| --- | --- | --- |
| 验收退出码 `2`，`reason=disabled` | `.env` 里 `MODEL_ENABLED` 仍是 `false`，或终端里已设置同名环境变量 | 改 `.env` 为 `true`，并在**新终端**里确认没有残留的 `MODEL_ENABLED=false`；`.env` 只补充进程环境里没有的变量 |
| 验收退出码 `2`，`reason=unavailable` | Ollama 服务没启动、端口不对、或被系统代理劫持 | `ollama list` 确认服务在跑；核对 `OLLAMA_BASE_URL`；确认 `NO_PROXY`/`no_proxy` 包含 `127.0.0.1`，否则请求会被代理接管并返回 502 |
| 验收退出码 `2`，`reason=configuration` | `OLLAMA_MODEL` 为空或与 `ollama list` 中的标签不一致 | 把 `.env` 的 `OLLAMA_MODEL` 填成 `ollama list` 里**完全一致**的标签（区分大小写） |
| 验收退出码 `1`，`reason=managed_product_reply_not_verified` | 模型选出的 ID 不合法、或模型判定需要人工 | 看后台会话里模型原始输出；确认模型支持中文 JSON 输出、且资料里确实写明了用户问的那件事；换更大的模型重试。**不要**改成写死返回值 |
| 访客答复里 `model_status` 是 `DISABLED` | 模型被主动关闭 | 这是预期状态，不是故障；改 `.env` 并重启两个服务 |
| 访客答复里 `model_status` 是 `MOCK` | 处于离线 Mock 测试或合成演示 | 真实调用不会出现 `MOCK`；`MOCK` 不能用来宣称真实模型可用 |
| 答复里没有价格，或价格与后台不一致 | 该商品被下架、模型选错了记录、或价格被当成不可回答的字段 | 先在后台核对商品状态与价格（单位是人民币分）；再确认模型选中的 ID 是不是这条商品 |
| 模型一直要求转人工 | 资料里没有写用户问的那件事 | 补录可核实的事实（价格、规格、保修政策等），或确认这是模型的正确行为——无依据就应转人工 |
| `scripts/run_tests.py` 报 `Network connections are disabled` | 离线守卫在工作 | 这是**预期**行为：离线回归不允许任何出网。`network_attempts` 必须为 `0` |
| Windows 上离线回归有 8–10 个错误用例 | SQLite WAL 并发写的 Windows 平台限制 | 本版本刻意不修，见 [CHANGELOG.md](../CHANGELOG.md#known-issues)；这是测试环境问题，不影响产品运行 |
| `pip install -r requirements.lock` 报缺包 | Python 版本不是 3.12 | 确认 `python --version` 为 3.12.x，重新建虚拟环境 |
| Streamlit 页面能开但管理端登录失败 | API 与工作台读到的 `ADMIN_API_TOKEN` 不一致，或令牌没配 | 两个服务用同一份 `.env` 重新启动；令牌只在本机输入，不要放进 URL |
