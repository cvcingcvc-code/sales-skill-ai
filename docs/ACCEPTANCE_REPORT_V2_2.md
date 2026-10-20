# V2.2 检索基准与客户预算约束验收

日期：2026-10-19  
开发分支：`codex/retrieval-budget-v2.2`  
代码与回归提交：`4c22a3248c1aa6365a548d1ddbe99c40e2c96ba6`

金额表达修正：`831989f02632e1d62f4bb78f4d9fa0915c1d3e84`

## 本阶段实现

- 新增 30 条确定性合成检索案例，含商品/知识期望 ID。覆盖相似商品名、两商品比较、排在干扰文档之后的相关政策、无答案、停用/下架资料、关联售后知识和用户诱导错误价格等情形。
- 基准按检索候选计算商品 Recall@6、知识 Recall@8 与无答案误召回；本次结果分别为 27/27、27/27，4 个无答案案例误召回 0 个。基准仅供固定数据集回归，不代表真实访客分布或模型回答质量。
- 明确写出人民币上限时，后端先按商品分价格筛选，再将候选交给模型；推理完成后重新读取现价，若商品或关联知识已超过预算则转人工。边界价格包含在预算内。
- 近似金额、区间、币种不明或没有符合预算的在售商品会在调用模型前转人工；单纯询问商品现价不会被当成预算。商品目录是唯一价格来源。

## 实际验证

| 检查 | 结果 |
| --- | --- |
| `.venv/bin/python scripts/run_tests.py --quiet --strict --report-json /tmp/sales-v2.2-tests-final.json` | PASS，412 项，失败/错误/跳过/预期失败均为 0，网络连接尝试 0 |
| `.venv/bin/python scripts/run_tests.py --pattern test_retrieval_quality.py --quiet --strict` | PASS，26 项 |
| `scripts/check_local_setup.py --json` | `OFFLINE_READY`，依赖和本地配置检查通过，模型执行 `DISABLED` |
| `scripts/demo_workflow.py --mock` | `MOCK_DEMO_COMPLETE`，商品、会话、需求、明确同意留资、人工接管、恢复、撤回和删除 8 项通过；这是合成 Mock 演示 |
| `scripts/verify_live.py --provider ollama --output /tmp/sales-v2.2-live.json` | `BLOCKED`，退出码 2，原因 `disabled`；真实模型未调用 |
| 本地 API + Streamlit 冒烟 | PASS，健康、商品、未授权拒绝、管理授权、Ollama 暂停状态、调用额度范围及 UI 健康检查通过；临时服务已停止 |
| `git diff --check` 与原销售模块文件差异检查 | PASS，无空白错误；原有训练/评分回归文件未修改 |

计数来源是本次严格 runner 输出；安全摘要保存在仓库外 `/tmp/sales-v2.2-tests-final.json`。本机没有执行 DeepSeek 或 Ollama 真实推理验收，不能据此宣称密钥、远端模型、部署或真实客服准确率已验证。

## 阻断与后续

- 真实模型端到端：`NOT_RUN`；模型按用户要求保持关闭。需要未来明确进入模型部署阶段后，在目标设备安装 Ollama、配置实际模型标签并重新运行真实验收。
- 云端配置中已存在旧 DeepSeek 访问要求；本地工作流不需要它。不要为当前测试填写密钥，也不要发布会运行真实推理的配置。
- 下一阶段按路线评估有界上下文打包与客服专用输出上限，再根据检索基准验证，不做无边界重构。
- 远端分支 `codex/retrieval-budget-v2.2` 已推送。`gh run list` 查询返回 HTTP `Forbidden`，所以 GitHub Actions 状态无法确认；Git push 成功不等于 CI 成功。云环境配置草稿已补入 `api.github.com` 以供 Actions 状态查询，草稿尚未发布，不影响当前测试结果。
