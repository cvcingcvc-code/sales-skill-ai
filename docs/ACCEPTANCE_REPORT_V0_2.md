# V0.2 Ollama 验收记录

- 验收时间：2026-10-20 UTC
- 冻结基线：`demo-v0.1`，提交 `0b7dd4da663c47baeb6adbb64f3f203676af4c18`
- 隔离开发分支：`codex/v0.2-ollama-validation`
- 总体结论：**BLOCKED**。本环境没有 Ollama 服务或已安装模型，无法完成任何真实模型推理；没有安装 Ollama、下载权重或使用 Mock/规则回复替代真实结果。

## 环境检查

| 项目 | 检查结果 |
| --- | --- |
| CPU | AMD EPYC 9V74；5 个可用 vCPU |
| 内存 | 总计 34,906,016 kB；检查时可用 33,900,260 kB；无 Swap |
| 工作盘 | `/workspace`：32 GB 总量，30 GB 可用 |
| 临时盘 | `/tmp`：17 GB 可用 |
| GPU 探测 | 无 `nvidia-smi`，未确认 GPU |
| Ollama CLI | `ollama` 不存在（command not found） |
| Ollama API | `http://127.0.0.1:11434` 不可连接；`GET /api/tags` 返回 curl 连接拒绝 |
| 已安装模型 | 无法查询；服务未运行 |

现有 Model Gateway 包含 Ollama HTTP Provider，模型名来自 `OLLAMA_MODEL`，调用地址来自 `OLLAMA_BASE_URL`。仓库中的 Ollama Provider 自动化覆盖使用离线传输夹具，不能作为本次真实模型证据，因此不计作模型验收通过。

## 咨询场景结果

| 场景 | 状态 | 结果 |
| --- | --- | --- |
| 有明确商品/政策知识的正常回答 | **BLOCKED** | 未调用模型：没有可连接的 Ollama 服务及已安装模型，无法记录真实模型名、回复或推理耗时。 |
| 知识库外问题拒绝编造并转人工 | **BLOCKED** | 未调用模型：没有真实推理条件，不能证明拒答行为来自 Ollama；不将规则路由结果算作通过。 |
| 模型不可用时降级转人工 | **PASS（仅降级路径）** | 使用隔离 SQLite、合成商品“演示保温杯”和合成保修政策，经生产 `ModelGateway`/客服服务尝试真实 Ollama Provider 请求。连接在到达服务端前被拒，网关返回 `unavailable`，客服响应 `model_status=BLOCKED`、`source=human_handoff`、`handoff_reason=model_unavailable`。这只证明不可用时的降级，不证明模型推理成功。 |

## 真实请求尝试记录

- Provider：`ollama`
- 实际已加载模型：**无；无法取得模型名**。
- Gateway 探测标签：`v0.2-unavailable-probe`，只是为了触发网关 HTTP 路径设置的探测标签，不表示模型存在或已加载。
- 目标：`http://127.0.0.1:11434/api/chat`
- 合成请求内容：用户询问“演示保温杯的保修期是多久？”，上下文包含合成商品“演示保温杯”和合成政策“演示保温杯保修政策”。
- 传输结果：在 Ollama 服务端返回 HTTP 响应前连接被拒；没有模型响应正文。
- 网关错误码：`unavailable`；耗时：约 `72.71 ms`（本次连接失败路径的端到端耗时，不是推理耗时）。
- 客服层响应：`{"model_status":"BLOCKED","source":"human_handoff","handoff_reason":"model_unavailable","error_code":"unavailable"}`
- 错误诊断：`ollama: command not found`；连接检查报告 `Failed to connect to 127.0.0.1 port 11434`。没有输出密钥、认证头或真实客户数据。
- 场景判定：降级行为 **PASS**；真实回答场景 **BLOCKED**；V0.2 整体验收 **BLOCKED**。

## 后续解阻条件

在目标运行设备上由用户安装/启动 Ollama，并根据该设备资源选择且确认模型后，将已安装模型的准确标签设为 `OLLAMA_MODEL`，以 `MODEL_ENABLED=true` 启用网关。确认 `/api/tags` 返回该模型后，再对合成商品和政策逐项执行三种真实咨询，保存真实返回、模型标签和推理耗时。当前阶段未下载任何模型，也未修改网关或扩展功能。
