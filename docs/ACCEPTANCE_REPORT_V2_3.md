# V2.3 有界上下文与客服输出限制验收

日期：2026-10-20

开发分支：`codex/retrieval-budget-v2.2`
功能提交：`d2aa20ff027fc2e7f9a97b1b1b01d967b9b9a6ad`

## 本阶段实现

- 客服请求先用现有网关无网络 `validate_request` 估算完整输入；过长时逐条移除最旧历史，再移除低排序的完整知识候选和商品候选，不裁切文本。移除商品时同步移除关联知识，避免形成失配候选。
- 当前客户问题、系统信任边界和首选商品/知识候选保留为核心证据。核心证据仍超限时转人工，模型调用次数和 token 预留保持不变。
- 新增 `SUPPORT_MODEL_MAX_OUTPUT_TOKENS`，默认 512。客服 JSON 资料选择器按该上限生成，且不会超过 `MODEL_MAX_OUTPUT_TOKENS`；客服额度也按同一个有效输出上限预留。销售训练仍使用既有 Provider 通用上限。
- `.env.example` 与用户指南已记录新设置和默认值。

## 实际验证

| 检查 | 结果 |
| --- | --- |
| `test_preflight_budget.py` 严格回归 | PASS，8 项：超长核心商品/政策不调用模型、不扣额度；旧历史及低优先级整条资料可移除；客服输出参数、额度预留与 Provider 上限一致；无效配置在预留前失败 |
| `test_resource_controls.py` 严格回归 | PASS，33 项：调用、token、失败预留、金额及 UTC 边界 |
| `test_model_gateway.py` 严格回归 | PASS，33 项 |
| `.venv/bin/python scripts/run_tests.py --quiet --strict --report-json /tmp/sales-v2.3-tests.json` | PASS，416 项，失败/错误/跳过/预期失败均为 0，网络连接尝试 0 |
| GitHub Actions 离线回归（功能提交 `38539025d8dc5ae5f032994d6ae4be24b046ddd0`） | PASS，运行 [39355654570](https://github.com/cvcingcvc-code/sales-skill-ai/actions/runs/39355654570) |
| 原销售训练/评分回归文件差异检查 | PASS，原有 `test_llm_client.py`、`test_memory.py`、`test_roleplay.py`、`test_scorer.py` 未修改；全量套件包含其回归 |

安全摘要位于仓库外 `/tmp/sales-v2.3-tests.json`。测试是隔离 Mock 与合成数据；本阶段没有发出真实模型请求，结果不代表 Ollama/DeepSeek 可用或客服真实效果已验证。模型维持关闭。

## 下一步与限制

- 路线 1 至 3 已完成。下一步是在硬件和模型版本确定后评估 JSON schema 与真实 Ollama 行为；用户设备仍未确定，本轮不安装或下载模型。
- 超大“核心候选”无法打包时采用保守转人工；系统不静默删掉最高排序证据后继续猜测。
- 当前分支已推送到 GitHub，功能提交与验收文档提交均在远端；该功能提交的 GitHub Actions 离线回归通过。验收文档的本次补记会随独立提交同步。
