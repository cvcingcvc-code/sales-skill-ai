# 变更日志

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。`1.0.0-beta.1` 是**预发布**版本：
功能已可下载、可复现、可部署，但尚未经过长期公网运行与真实商家资料的充分验收。

> 历史记录：本文件从 v1.0.0-beta.1 开始建立。此前 `main` 上的开发过程记录在
> [docs/ACCEPTANCE_REPORT*.md](docs/) 与提交历史中。

## [1.0.0-beta.1] — 2026-11-05

首个公开预发布版本：真实 Ollama 本地模型驱动的 AI 商品咨询与客服 MVP。

### Added

- **本地 Ollama 真实模型接入与验收。** 在 Windows + Ollama 0.40.14 + `qwen2.5:3b`
  （3.1B / Q4_K_M 量化）上完成真实端到端验收：`scripts/verify_live.py --provider ollama`
  返回 `PASS`、退出码 `0`，连续多次重跑结果一致。
- **真实商品资料选择闭环。** 顾客提问 → 系统检索候选资料 → 本地模型只输出资料 ID →
  服务端校验 ID 来自本次候选 → 服务端用受管理商品记录渲染答复（价格、链接、政策原文）→
  资料不支持时转人工。全链路可复现，验收脚本只用合成数据。
- **按请求指定模型 temperature 的能力。** 网关新增 `temperature` 参数与
  `supports_request_temperature` 特性位，客服资料选择调用固定 `temperature=0`；
  自由文本生成（销售训练）不传该参数，保留 Provider 默认采样。
- **自动化回归测试。** 新增 7 条离线回归用例，覆盖 temperature 透传、Provider 默认值保留、
  非法 temperature 拒绝、旧注入桩网关兼容性，以及选择提示词必须保留的接地规则。
- **版本单一来源。** 新增 `core/version.py`，`/skill/health` 与 FastAPI 元数据统一报告
  `PROJECT_VERSION`，与 Git Tag 保持一致。
- **开源文档。** 重写 `README.md`（面向未参与开发者的通俗说明）、新增本变更日志、
  新增 `docs/RELEASE_NOTES_v1.0.0-beta.1.md`，并更新本地模型部署说明与工作台使用手册。

### Changed

- **优化模型选择提示词。** 选择提示词现在写明受管理记录各字段的含义
  （`price_cents`/`currency` 即价格、`description` 即规格、`title`/`content` 即政策正文），
  并明确区分「资料已写明 → `needs_human=false`」与「细节资料完全没有写（开票/发货/保修）→
  `needs_human=true`」。修正了真实模型对「资料里明明有价格」也要求转人工的误判。
- **为结构化资料选择设置 `temperature=0`。** 资料选择是分类任务而非创作任务；
  在 Provider 默认采样下，同一输入的选择结论会随机翻转，导致真实验收偶发失败。
  固定温度后结论完全确定。
- **改进 Windows 离线测试兼容性。** 离线网络守卫只放行「`socket.socketpair()` 内部发起、
  目标是 loopback」的连接，且不计入 `network_attempts`；其余出网连接仍全部拦截并计数。
  离线守卫原本在 Windows 上误杀 asyncio 自管道，导致 108 个用例报错。
- **改进 Windows 子进程测试环境。** 测试隔离改为只移除本应用读取的配置变量
  （`MODEL_`/`SUPPORT_`/`DEEPSEEK_`/`OLLAMA_`/`GOOGLE_`/`GEMINI_`/`ANTHROPIC_`/`CLAUDE_`/`LLM_`
  前缀与 `ADMIN_API_TOKEN`/`DB_PATH`/`APP_ENV`/`PYTHON_DOTENV_DISABLED`），
  不再清空整个操作系统环境，Windows `spawn` 出来的 worker 能正常启动。
- 更新 `docs/LOCAL_MODEL_DEPLOYMENT.md`、`docs/USER_GUIDE.md`：反映已验证的本地模型路径，
  补充常见错误排查，并再次说明项目不会自动下载模型权重。

### Fixed

- **修复 Windows socketpair 网络守卫误判。** `socket.socketpair()` 在 Windows 上以真实
  loopback TCP connect 实现，被离线守卫一律拦截 → 108 个用例报
  `Network connections are disabled in offline regression.`。
- **修复 Windows 子进程环境清空问题。** `patch.dict(os.environ, {}, clear=True)` 清空整个
  操作系统环境，导致 Windows `spawn` 出的 worker 无法启动解释器（子进程 exit=1、无输出）。
- **修复真实模型误判需要人工的场景。** 真实模型返回合法 JSON、选中 ID 全部正确，
  但 `needs_human=true` 导致答复走人工接管而非受管理商品目录；提示词与温度两项修复后，
  真实验收由 `FAIL`（退出码 1）变为 `PASS`（退出码 0）。

### Known Issues

- **Windows SQLite 并发相关测试仍存在历史错误。** 约 8–10 个离线用例在 Windows 上间歇性
  失败（`sqlite3.OperationalError: attempt to write a readonly database`、
  `PermissionError: WinError 32` 的 `legacy.db` 文件占用）。已确认是 Windows 上
  SQLite WAL 模式 + 多进程/多线程并发写的平台限制（同一场景改用 `journal_mode=DELETE`
  后通过；带/不带开发工具沙箱均复现）。本版本**刻意不修**：不删断言、不 skip、
  不改产品 WAL 行为。Ubuntu CI 全绿。
- **尚未完成真实商家场景的充分验收。** 当前验收只使用合成商品与合成访客数据；
  真实商家的商品资料、政策文本与真实问法尚未系统验证。
- **不保证生产级公网部署可用性。** 未实现 HTTPS、反向代理限流、备份轮换、监控告警、
  高可用与容量规划；应用当前按单 worker、单机 SQLite 运行。
- 模型输出质量与所选模型强相关。换用其他模型必须重新执行
  `scripts/verify_live.py --provider ollama` 验收，不能沿用本版本的结论。
- 商家需自行完成平台合规审核；项目不保证识别所有违禁表达。

[1.0.0-beta.1]: https://github.com/cvvingcvc-code/sales-skill-ai/releases/tag/v1.0.0-beta.1
