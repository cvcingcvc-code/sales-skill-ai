# 单店销售客服 MVP 阶段记录

日期：2026-10-21
基线：`codex/s2-windows-first-run`，`bd46dbdac487031559b65dc5582128538eecfc83`

## 本轮目标与选择

审计商家商品/知识录入、访客咨询、答复依据、人工接管和 SQLite 持久化；按审计结果只选择真实模型三场景验收（有依据答复、知识外转人工、模型不可用降级）。本轮没有调用模型，也没有修改业务代码。

真实验收为 **BLOCKED**：当前执行环境没有已授权的模型凭据，且 `api.deepseek.com` 不在允许访问列表；Ollama 也未在本轮环境中验证可用。没有使用 Mock、规则答复或 Codex 人工代答填补真实模型证据。Windows runner 的 `MODEL_ENABLED=false` 验收仅证明关闭模型时的应用启动及人工降级，不是 AI 验收。

## 现状核查

| 能力 | 状态 | 代码证据与边界 |
| --- | --- | --- |
| 商品新增、编辑、停用、价格、规格/适用条件 | 部分已有 | 管理 API `GET/POST /admin/products` 在 `agent_skill/support_api.py` 138–144；`Store.upsert_product` 支持按 ID 更新、价格、链接和上下架，在 `customer_service/store.py` 144–183。规格与适用条件没有独立字段，只能放在描述中。Streamlit 商品表单见 `demo/support_admin.py` 92–116。 |
| FAQ、售后政策、发货说明维护及审核/过期/冲突管理 | 部分已有 / 缺失 | `GET/POST /admin/knowledge` 支持 product/faq/policy、关联商品和 active 开关，见 `agent_skill/support_api.py` 146–152、`customer_service/store.py` 185–206。没有发货专用类型、审核状态、来源、过期时间、版本或冲突检测；当前 active 是启用开关，不等于审核。 |
| 顾客检索、答复与依据追溯 | 部分已有 | 访客商品目录、会话和消息接口见 `agent_skill/support_api.py` 97–116。`CustomerService._retrieve_with_budget` 以关键词/中文二元词检索启用商品及知识，见 `customer_service/service.py` 216–253；真实 Gateway 被限制为选择本次候选 ID，后端再校验并从资料渲染答复，见同文件 338–460、478–500。HTTP 回复包含 `products`/`citations`，后台可显示引用（`demo/support_admin.py` 143–170）；会话持久化消息仅存文本与 source，没有独立引用 ID/资料版本快照，不能完整追溯答复当时采用的资料版本。 |
| 缺知识、投诉、价格不确定、模型不可用时人工接管 | 已有（规则路径）/ 未经真实模型验证 | `CustomerService.chat` 会对风险、预算歧义、检索空集及模型异常建立 handoff，见 `customer_service/service.py` 293–324、461–475；模型关闭时明确返回 `DISABLED` 并转人工，见 255–274。管理员队列与回复 API 为 `GET /admin/handoffs`、`POST /admin/handoffs/{id}/resolve`，见 `agent_skill/support_api.py` 199–205。风险判断和关键词检索的覆盖率未通过真实商家场景验收。 |
| SQLite 重启后商品、知识、会话及接管记录 | 已有（结构上）/ 本轮未验证重启 | `Store` 默认写入 `SUPPORT_DB_PATH` 或 `./data/db/support.db`，初始化 products、knowledge、conversations、messages、handoffs 等 SQLite 表，见 `customer_service/store.py` 36–90、133–142。表数据落在文件数据库中；本轮未执行重启持久化验收，故不报告运行 PASS。 |
| 模型真实验收 | 未验证 / BLOCKED | Gateway 的状态将未实际连通配置报告为 `UNVERIFIED`，而不是 PASS，见 `core/model_gateway.py` 231–250。模型调用由 `MODEL_ENABLED` 控制（同文件 68–84）；V2.3 报告明确之前测试未发真实模型请求（`docs/ACCEPTANCE_REPORT_V2_3.md` 15–26）。当前无可授权凭据且网络目标受限，本轮未发送请求。 |

## 已实现、未完成与下一步

- 已实现：管理员商品与 FAQ/政策基本 CRUD、基于管理资料的检索候选、回复引用字段、访客会话、规则/模型错误时转人工、管理员人工回复、SQLite 文件持久化结构。
- 部分实现：商品规格/适用条件依赖自由文本；知识仅有启停而无审核生命周期；回复携带引用但不保存引用版本；关键词检索不等于语义检索。
- 未实现或本轮未验证：完整资料审核/过期/冲突管理、平台规则治理、真实模型三场景、SQLite 重启运行验收、真实商家商品准确性。
- 建议下一步：先由环境所有者配置获授权且可访问的真实 Provider，再在隔离 SQLite 与合成资料上执行三场景验收；若模型仍不可用，维持 BLOCKED。模型验收通过后，再以小阶段补充知识来源、审核状态、更新时间/有效期及答复引用版本追溯。

## 本轮改动与检查

只新增本阶段审计记录；没有修改业务代码、数据库、模型网关、测试、冻结标签或其他分支。没有运行测试或应用验收（本轮选择的真实模型验收因前置条件缺失而 BLOCKED）。工作区原有 `README.md`、`docs/PRODUCT_ROADMAP_ZH.md` 修改及 `docs/STORE_SALES_MVP_REQUIREMENTS_ZH.md` 未覆盖。提交、合并与推送均未执行。

## 后续持久化回归补充（2026-10-21）

为补齐上一节所列“SQLite 重启后持久性未验证”，新增 `tests/test_data_lifecycle.py::test_catalog_conversation_messages_and_handoff_survive_store_reopen`：以隔离文件数据库写入合成商品、知识、访客消息及人工接管回复，再用新 `Store` 实例读取并断言均保留。它验证 Store/SQLite 文件重开，不替代 FastAPI、Streamlit 进程重启或真实浏览器验收。

严格离线全量测试现为 419 项 PASS、0 FAIL、0 ERROR、0 SKIP、0 expected failure，网络尝试为 0；真实模型仍 `NOT_RUN/BLOCKED`。覆盖率工具未安装，因此没有行覆盖率数据。除这一条持久化测试外，本次不改业务实现；真实模型和服务级重启仍是未闭合验收项。
