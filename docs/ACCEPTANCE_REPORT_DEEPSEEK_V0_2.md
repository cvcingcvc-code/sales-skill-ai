# DeepSeek 在线模型验收记录（V0.2）

- 检查日期：2026-10-20 UTC
- 冻结基线：`demo-v0.1` / `0b7dd4da663c47baeb6adbb64f3f203676af4c18`
- 隔离验证分支：`codex/v0.2-deepseek-validation`
- 总体结果：**BLOCKED**；前置条件不满足，未发起 DeepSeek 请求。

## 前置条件

| 检查项 | 结果 |
| --- | --- |
| Codex 网络策略 | restricted；`api.deepseek.com` 不在有效白名单；策略执行状态 `unknown`，无法确认出网获准 |
| 安全密钥绑定 | 环境状态未报告 DeepSeek secret/runtime variable 绑定 |
| 进程密钥注入 | 只检查变量是否存在，`DEEPSEEK_API_KEY` 未设置；未读取或输出变量值 |
| DeepSeek Provider / 官方当前模型名 | 未检查；按照立即停止条件，不继续检查模型配置或访问官方来源 |

## 验收状态

知识内回答、知识外拒答转人工、模型不可用降级三项场景均为 **BLOCKED / NOT_RUN**。真实请求数为 0；真实响应、模型名和延迟均无；本次调用费用为 0。没有下载本地模型、没有绕过网络限制，也没有修改 Gateway、业务代码或 `demo-v0.1` 标签。

解除阻断后再在此隔离分支继续：由环境配置授权 `api.deepseek.com` 出网并安全注入 `DEEPSEEK_API_KEY`，随后重新执行前置检查。当前不作 DeepSeek 真实推理通过声明。

## 执行环境边界

本次 Codex 执行器为 Linux x86_64 云容器，不是用户的 Windows 本机；报告中的白名单和 `DEEPSEEK_API_KEY` 存在性检查仅反映此云容器。无法读取或验证 Windows 本机的网络、环境变量及其凭据，因此本机条件也为 **BLOCKED / NOT_VERIFIED**。未要求用户粘贴密钥，也未读取任何密钥值。
