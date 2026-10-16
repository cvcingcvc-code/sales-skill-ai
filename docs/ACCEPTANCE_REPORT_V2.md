# V2 本地优先工作台验收报告

验收日期：2026-10-16（UTC）。分支：`codex/local-workbench-v2`，基于 V1 提交 `e35fcac3f7b661fcd4649b6048f6a3bb30078d92` 增量开发。原始审计见 [PROJECT_AUDIT.md](PROJECT_AUDIT.md)，V1 历史结果保留在 [ACCEPTANCE_REPORT.md](ACCEPTANCE_REPORT.md)。

**软件离线验收通过；真实模型验收延期。** 按用户要求，当前 `MODEL_ENABLED=false`、`LLM_PROVIDER=ollama`，没有安装 Ollama、下载权重或执行真实模型请求。软件通过不能解释为真实 AI 回答质量已通过，也不表示已经部署公网网站。

## 本轮完成内容

| 阶段 | 交付内容 | 提交 SHA | 验证及下一步 |
| --- | --- | --- | --- |
| 模型与本地配置 | 显式暂停、输入/输出上限、Ollama 回环代理处理、离线预检和隔离测试入口 | `6bfefb9e11511cc5385399be92dba034f8c7ca01` | 全套离线回归覆盖；以后按实际设备安装和启用模型 |
| 客服后端 | 关闭/恢复、撤回/删除、保留预览/清理、元数据审计、SQLite 共享限流与客服每日预算、过期推理写保护 | `2147f76662ec5ebc51fbcc12a4378501e67b21cb` | 全套离线回归覆盖；真实商家确认资料与保留期限 |
| 销售训练 | 显式评分量纲归一化、保留原始分、来源标识、历史兼容 | `a1dd248957cbeaff7f9ab2485274a55b575cb465` | 原 40 项测试不改动；真实角色扮演/模型评分以后验收 |
| 工作台与访客页 | 六个管理页、模型与预算状态、隐私操作、访客关闭/撤回/删除 | `f41ede5f1a5b90eb942ce146e4363fa998ae1791` | AppTest、JavaScript 行为测试及实际 Chromium 检查通过 |
| 交付文档 | 架构、启动/云部署、使用手册、通用本地模型步骤与本报告 | 包含本报告的文档提交，运行 `git log -1 --format=%H -- docs/ACCEPTANCE_REPORT_V2.md` 查看 | 实际电脑尚未确定，提供 Windows/macOS/Linux 通用方案 |

四个代码阶段提交前已完成最终代码状态的完整离线测试。没有覆盖 `master` 或合并其他分支；推送目标为新分支。文档自身提交及远端同步结果以最终交付消息和 Git 记录为准。

## 自动化回归

在仓库根目录执行：

```bash
.venv/bin/python scripts/run_tests.py --quiet
```

实测结果：**356 tests，OK，失败 0，网络连接尝试 0**。其中 V1 的 208 项继续通过，V2 新增 148 项。原始 40 项所在的 `test_llm_client.py`、`test_memory.py`、`test_roleplay.py`、`test_scorer.py` 与原始提交 `31671938256792948c5c1cc57f018254acec7386` 比较无差异。

| 新测试文件 | 项数 | 主要覆盖 |
| --- | ---: | --- |
| `tests/test_model_offline.py` | 26 | 暂停前置拦截、同步/流式、旧训练接口、配置错误、输入/输出边界、本地代理 |
| `tests/test_local_setup.py` | 13 | 无网络预检、缺配置、路径/URL 校验、凭据不输出 |
| `tests/test_data_lifecycle.py` | 21 | 增量迁移、关闭/恢复、删除、撤回、保留条件、审计、并发版本保护 |
| `tests/test_resource_controls.py` | 33 | SQLite 共享限流、并发预留、UTC 切日、保守结算、费用未知、删除不重置预算 |
| `tests/test_workflow_v2.py` | 20 | HTTP 权限、严格确认、预算/429、暂停不预留、人工接管/关闭/删除后的迟到响应 |
| `tests/test_score_scales.py` | 16 | 量纲、归一化、无效/历史分数、CLI/报告/雷达来源 |
| `tests/test_workbench_v2.py` | 17 | Streamlit 管理流程及真实 JavaScript DOM/fetch 行为 |
| `tests/test_demo_v2.py` | 2 | Mock 演示与暂停演示的退出码及生命周期闭环 |

V1 的 63 条客服场景继续执行，覆盖正常咨询、比较、推荐、未知知识、不存在商品、错误价格诱导、售后、人工接管、模型异常、越权及不可信资料。DeepSeek 成功/鉴权/超时/不可用等是 HTTP Mock 合约测试；Ollama 同样是合约测试，均不冒充真实 Provider 验收。

测试入口使用临时数据库、测试凭据和网络禁止器。测试进程显式开启仅供 Mock 合约使用的开关，运行中的应用仍保持暂停。不要以直接运行根目录历史 `test_gemini.py` 或盲目执行全仓库 pytest 代替离线入口。

最后的人工接管竞态也有反向验证：在人工待办快照读取后、后续事务前完成 resolve，旧实现会被两项定点测试捕获；修复为事务内递增 `ai_epoch` 后，成功/异常模型返回都不能重新创建待办或写回过期答复。

## 当前实例实测

| 检查 | 最终结果 | 边界 |
| --- | --- | --- |
| 锁定依赖及数据库初始化脚本再次执行 | PASS，77 个包兼容，现有记录保留 | 未改变锁文件，无重复安装循环 |
| `scripts/check_local_setup.py --json` | `OFFLINE_READY`，`model_execution=DISABLED`，`model_verification=NOT_RUN` | 静态配置检查，无网络或模型请求 |
| 重启当前 API | PASS，健康版本 `2.0.0`；场景、商品、访客页面、管理鉴权、暂停状态及预算范围符合预期 | 单 worker、私有回环地址；不是公网验收 |
| `node tests/browser_smoke.cjs` | PASS | 实际 Chromium：告知→建会话→规则寒暄→人工→同意留资→关闭→撤回→删除→重置；仅合成资料，无模型调用 |
| 实际 Chromium 管理页面 | PASS | 浏览器输入本地令牌后登录、六个管理页、`DISABLED` 状态及隐私操作可见；凭据未输出 |
| 语法/差异检查 | PASS | Python compileall、JavaScript 语法、`git diff --check`；原 40 项文件比较无差异 |

当前验证 API 使用 8080，最新管理工作台使用 8502（8501 有既存服务，故选用空闲端口）。文档默认示例仍为 8080/8501；端口冲突时选择空闲端口，并将 `SUPPORT_API_URL` 指向实际 API。未停止无法确认归属的服务。

## 完整演示与模型状态

```bash
.venv/bin/python scripts/demo_workflow.py --mock
.venv/bin/python scripts/demo_workflow.py
.venv/bin/python scripts/verify_live.py --provider ollama --output /tmp/sales-v2-model-paused-final.json
```

- Mock 演示：`MOCK_DEMO_COMPLETE`，退出码 0。商品→受管理资料答复→会话/需求→同意留资→人工回复→明确恢复 AI→撤回→关闭/删除，八项检查通过。演示数据为临时合成商品，不是真实 AI 或在售库存。
- 当前暂停演示：`DISABLED`，退出码 2。商品、记录、同意留资和人工/生命周期八项独立检查通过，依赖模型的答复明确说明暂停并转人工。
- 独立真实验收入口：`BLOCKED`，原因 `disabled`，退出码 2；`real_model_request=BLOCKED`，模型答复及后续真实链路 `NOT_RUN`。没有调用 Ollama 或回退 DeepSeek。

## 限制与后续人工步骤

1. 设备尚未确定；按 [LOCAL_MODEL_DEPLOYMENT.md](LOCAL_MODEL_DEPLOYMENT.md)在以后选定的电脑安装官方 Ollama、下载一个合适模型、确认准确标签，最后显式启用并重启。先执行真实 Provider 合成验收，再用已审核的商家商品和政策验收。当前不需要 DeepSeek Key。
2. 每日次数/token/可选估算金额预算仅覆盖客服网关路径，`scope=customer_service`；旧销售训练只受模型暂停及单次输入/输出限制，未纳入客服每日预算。缺单价/usage 显示未知或保守预留，估算金额不是账单。
3. 新明确量纲的 0–20 分按 ×5 归一化，雷达仅展示明确量纲。为保留原测试/旧接口行为，无量纲且 `20 < score <= 100` 的历史分数仍保留旧 70 阈值弱项判断；它们继续标记 `legacy_unverified`，不代表量纲已核实，不进入归一化雷达。未知 0–20 分不作弱项判断，历史数据未批量重写。
4. 数据删除移除当前应用记录，审计只保留固定操作元数据与哈希对象标识；预算日聚合保留且不含正文。删除不等于备份/WAL/磁盘法证清除，实际保留期限、备份轮换与访问策略由部署方制定；未安装自动清理任务。
5. 当前仍为关键词检索、单管理员令牌、单机 SQLite/单 API worker。未建设支付、退款、库存/订单执行、外呼、复杂前端、RBAC、多机或多智能体。真实商家资料、模型回答质量、并发性能和公网部署尚未验收。
6. 历史公开凭据是否已经撤销/轮换未验证，见 [SECURITY.md](../SECURITY.md)；以后启用对应云账户前由账户持有人核验。真实密钥、客户资料、数据库和模型权重没有纳入本轮提交。

云环境设置草稿已保存完整 `install_script`、V2 `start_skill` 和暂停/本地 Provider 的非秘密变量建议；安装脚本已在当前实例再次成功执行。草稿保存没有应用运行设置或发布环境，发布及新任务恢复未验证。设置页若仍保留旧的 DeepSeek Key 声明或云 Provider 建议，本地方案不依赖它，应在发布前移除过时要求并核对本地建议；本工具不能删除旧秘密声明。需要复用此环境时，在环境设置中检查并保存草稿，再发布。

使用流程见 [USER_GUIDE.md](USER_GUIDE.md)，部署边界见 [DEPLOYMENT.md](DEPLOYMENT.md)。本轮完成软件范围后停止，下一项必需工作是实际本地模型与业务验收。
