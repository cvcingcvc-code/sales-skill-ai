# V2.1 质量优化验收

日期：2026-10-19（UTC）。基于 V2 `2f39917448c0bb20c7f05cac16c5b059d3fe8fd8` 增量改进，分支为 `codex/workbench-quality-v2.1`。没有合并或覆盖 `master`、V1 或 V2 分支。

## 完成的改动

- 知识先按问题相关性排序再截取前 8 条；去除型号共有词的干扰，保留稳定排序及上下架约束。
- 完整商品名称/ID确定咨询范围；后续代词继承最近明确的用户型号，助手描述中顺带提到的型号不能扩大范围。
- 后端重读资料后校验知识关联商品，拒绝错配政策；引用标明适用商品，保留通用政策、仅知识回复及双商品比较。
- 新增无网络 `ModelGateway.validate_request()`，客服在预留每日额度前使用与实际调用相同的消息及系统说明校验。过长请求转人工，不发 HTTP、不占日调用/token额度；实际发出后的失败仍保守计数。本轮不裁剪上下文。
- 新增 GitHub Actions，安装锁定依赖后运行 Python 3.12、Node 22 下的严格离线回归。官方 Actions 的 Node 24 版本经官方仓库标签及 `action.yml` 核对后固定为 SHA。
- runner 拒绝零项测试；`--strict` 拒绝跳过及预期失败；保留旧 `--fail-on-skip` 的跳过检查语义。JSON 只保存固定状态和计数，写入失败为非零退出，不上传客户资料或凭据。

## 实际验证结果

```bash
.venv/bin/python scripts/run_tests.py --quiet --strict --report-json /tmp/sales-quality-v2-1-final.json
```

**406 项通过，失败 0、异常 0、跳过 0、预期失败 0、意外成功 0，网络守卫记录连接尝试 0，退出码 0。** 该结果是离线 Mock 合约及软件回归，不是模型准确率或真实模型验收。

原 V2 的 356 项继续通过，本轮新增 50 项：

| 文件 | 数量 | 检验重点 |
| --- | ---: | --- |
| `tests/test_retrieval_quality.py` | 20 | 第九篇相关资料、跨商品错配、关联变化、比较/通用政策、型号前缀/重叠、代词与旁及型号 |
| `tests/test_model_preflight.py` | 13 | 四种 Provider 共用校验、UTF-8/系统消息边界、暂停与无效配置、不创建客户端/调用模型、不改变最近状态 |
| `tests/test_preflight_budget.py` | 4 | 超长商品/知识/历史均无 HTTP、无额度消耗；正常请求仍预留并结算 |
| `tests/test_offline_runner.py` | 13 | 零项假绿、跳过、预期失败、失败/异常、被捕获的外连尝试、安全报告、写入失败及旧参数兼容 |

原 40 项测试的四个文件与原始提交 `31671938256792948c5c1cc57f018254acec7386` 比较无变化。Python compileall、JavaScript 语法、工作流 YAML/触发器/只读权限/固定 Action 引用检查及 `git diff --check` 通过。

关键回归有修复前后对照：

- 长商品、知识、历史的三条回归在修复前均失败：无 HTTP 请求仍占用一次及 4128 token；修复后均为零预算占用。
- 资料排序/关联的初始 18 项测试在旧实现捕获 11 个失败和 1 个错误；修复后通过。独立复核新增两项旁及型号测试，修复前均失败，改为继承用户范围后通过。
- runner 的零项发现、严格跳过、严格预期失败等故障场景使用临时受控测试验证；不能靠把失败标为 `expectedFailure` 使 CI 通过。

## 演示及模型边界

`scripts/demo_workflow.py --mock` 实测为 `MOCK_DEMO_COMPLETE`、退出码 0，商品→资料回复→需求/会话→同意留资→人工回复→恢复→撤回→关闭/删除八项检查通过；仅使用临时合成资料。新增政策标签可见，不是真实商品或真实 AI 通过。

离线预检为 `OFFLINE_READY`，运行模型为 `DISABLED`。独立 `verify_live.py --provider ollama` 实测 `BLOCKED`、原因 `disabled`、退出码 2，真实模型请求未发出，后续真实链路为 `NOT_RUN`。没有安装 Ollama、下载权重、配置新模型密钥或公开部署。

## 提交与 GitHub 验证

| 阶段 | 提交 |
| --- | --- |
| 检索/关联及预算前预检 | `c29868be46d0cb2d49f4b64aa2a1f7e862dc2664` |
| 离线 runner 与 GitHub 工作流 | `f6c348cc71537fb9886fa2296e7b84e3b1a5f8c0` |
| 独立复核后的范围及严格门槛修订、固定 Actions | `ad122681613d96fa523f196a86861101640dedab` |
| 优化路线、开发同步流程与本报告 | 运行 `git log -1 --format=%H -- docs/ACCEPTANCE_REPORT_V2_1.md` 查看本报告提交 |

每阶段通过相关测试后提交并推送开发分支，没有强推。首次工作流提交 `f6c348c` 的远端 [GitHub Actions run](https://github.com/cvcingcvc-code/sales-skill-ai/actions/runs/39202564770) 已核对为 **Success**，总耗时 1m 4s；这只证明该提交。包含最终修订/本报告的提交必须核对自己的 [Actions 结果](https://github.com/cvcingcvc-code/sales-skill-ai/actions?query=branch%3Acodex%2Fworkbench-quality-v2.1)，最终交付消息记录其状态，不能沿用早先提交的成功。

## 下一步

见 [OPTIMIZATION_PLAN.md](OPTIMIZATION_PLAN.md)：先建立 30+ 固定检索基准，再做明确客户预算金额约束、有界上下文和客服小 JSON 输出，最后确定设备并进行真实本地模型测试。SQLite 一致备份恢复、查询规模和公网小流量门槛也有明确进入条件。当前未实现客户金额约束、上下文裁剪、向量检索或生产上线，不把这些计划算已交付。

后续授权开发按 [DEVELOPMENT.md](DEVELOPMENT.md)完成“测试→明确暂存→提交→推送→核对远端 CI”，自动同步不包含主分支合并或生产部署。
