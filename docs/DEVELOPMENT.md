# 开发、验证与 GitHub 同步

沿用当前隔离 checkout，不额外创建 Git worktree。保留用户既有改动，先检查工作区、当前分支、HEAD 和远端；不要在文档中假定开发分支名称。

只有当前任务明确要求同步 GitHub 时，才在阶段测试通过后提交并推送当前开发分支；不合并主分支或覆盖其他分支。

## 本地开发前提

使用 Python 3.12、仓库锁定依赖和 Node 22。安装及本地服务启动见 [README](../README.md)；已有虚拟环境和 `.env` 不覆盖，依赖安装保持 TLS/证书校验。修改依赖确有必要时才调整声明与锁文件，不反复安装来掩盖代码问题。

运行服务继续保持 `MODEL_ENABLED=false`、`LLM_PROVIDER=ollama`，不安装 Ollama 或下载模型。离线回归在隔离测试进程中设置 Mock 合约所需配置，不会修改运行服务的模型开关，也不证明本地/云模型可用。真实模型验收为 `NOT_RUN`，主动暂停的真实检查为 `BLOCKED / disabled`。

## 推荐验证入口

仓库根目录运行：

```bash
.venv/bin/python scripts/run_tests.py --quiet
```

完整阶段交付采用严格模式并把安全计数保存在仓库外：

```bash
.venv/bin/python scripts/run_tests.py --quiet --strict --report-json /tmp/sales-offline-summary.json
```

定向回归可以使用 `--pattern`，例如：

```bash
.venv/bin/python scripts/run_tests.py --pattern test_preflight_budget.py --quiet --strict
```

也可以用测试组快捷筛选，避免逐个记测试文件名：

```bash
# 客服 API、知识检索、生命周期和资源控制
.venv/bin/python scripts/run_tests.py --group support --quiet --strict
# 销售训练和评分
.venv/bin/python scripts/run_tests.py --group training --quiet --strict
# Provider/Gateway 合约；全部是离线测试，不会调用真实模型
.venv/bin/python scripts/run_tests.py --group models --quiet --strict
# 管理端与访客页面回归
.venv/bin/python scripts/run_tests.py --group ui --quiet --strict
# 同时验证多个相关组，重复模块会自动去重
.venv/bin/python scripts/run_tests.py --group support --group models --quiet --strict
```

首次启动前的静态配置检查单独运行：

```bash
.venv/bin/python scripts/check_local_setup.py --json
```

Windows PowerShell 将 `.venv/bin/python` 替换为 `\.venv\Scripts\python.exe`。配置预检不连接模型；所有测试组都运行隔离的 Mock/规则回归，不等同真机或真实模型验收。

runner 使用临时数据库、固定测试配置和禁止 socket 外连的防护；不读取真实客户库作为测试样本。直接 unittest 可能继承 `.env` 中的暂停开关而改变旧 Mock 测试前提，因此优先使用上述入口。根目录 `test_gemini.py` 是历史手工联网脚本，不属于离线回归集，不盲跑它或把它当作真实验收通过。

| 参数/行为 | 合约 |
| --- | --- |
| `--pattern` | 控制 `tests/` 的 unittest 文件发现模式；没有匹配测试仍为失败 |
| `--group` | 运行客服、训练、模型合约或 UI 相关测试组；可以重复指定组合组，不可与自定义 `--pattern` 混用 |
| `--quiet` | 减少常规输出，不改变测试判定 |
| `--fail-on-skip` | 保留的可选跳过检查，只因跳过项失败，不改变预期失败的默认语义 |
| `--strict` | 交付与 CI 门槛；跳过项和预期失败均返回失败，不允许用标记掩盖未通过项 |
| `--report-json PATH` | 写固定字段的安全摘要；报告写入失败同样返回非零，不输出异常正文 |
| 退出码 | 0 才表示本次离线回归通过；零测试、失败、异常、严格模式跳过/预期失败、意外成功、外连尝试或报告写失败均非零 |

JSON 仅包含固定字段：`scope="offline_mock_regression"`、`model_validation="NOT_RUN"`、`status`、`strict`、`fail_on_skip`、`run`、`fail`、`error`、`skipped`、`expected_failures`、`unexpected_successes`、`network_attempts`。不包含测试名称、异常正文、文件路径、请求内容或凭据。报告中的 PASS 只针对本次离线软件验证，不能转写成真实模型 PASS。

修改时先运行能检验风险的定向测试，再完成阶段所需回归。测试数据使用合成资料和临时数据库。涉及检索时检验候选及对应关系，涉及预算时同时断言 HTTP 次数、共享额度和正常结算，避免只断言界面文案。

## GitHub Actions 的职责与边界

[offline-regression.yml](../.github/workflows/offline-regression.yml) 在 push、pull request 和手动 `workflow_dispatch` 时验证代码；不替开发者暂存、提交、推送或发布应用。

工作流使用 Python 3.12、Node 22，按 `requirements.lock` 安装后执行 `pip check`，再运行严格离线回归。依赖安装阶段可以访问包源，“离线”指测试不得连接外部服务。没有真实模型密钥、服务安装、模型下载或生产部署步骤；Node 就绪也不代表已运行真实浏览器或模型验收。

工作流权限为 `contents: read`，checkout 不保留写凭据。测试步骤失败时仍尝试保存仅含固定计数的 JSON artifact，保留 7 天；保留摘要不将失败改成成功。不上传 `.env`、数据库、原始异常、客户资料或模型权重。

本地测试通过不等于远端 CI 通过。推送后应核对 GitHub 上的分支、提交 SHA、对应 workflow run 及完成状态：有同一 SHA 的成功结果才能报告 CI 通过；等待中标记 PENDING，未取得证据标记未验证，失败则记录失败并继续修复。不能以 `git push` 退出码代替 CI 结果，也不能把前一个提交的成功用于当前提交。

## 每阶段同步顺序

1. 检查 `git status`、当前分支、HEAD 和远端。保留原有工作，不混入无关修改；若当前在主分支，先使用隔离开发分支，不直接推送主分支。
2. 完成一个独立改动及必要回归。若失败，定位原因、做有依据的修正，再运行受影响检查；不跳过失败项来制造绿色结果。记录运行命令、退出码、安全计数及实际阻断项。
3. 检查相关差异和敏感文件风险，明确列出本阶段文件后再 `git add -- <逐项文件路径>`。不使用无差别暂存把已有用户修改、数据库或本地配置一起提交；暂存后再次核对文件清单与差异。
4. 提交描述该阶段最终行为的 Git commit，记录完整 SHA。只提交已经验证的阶段成果，不伪造测试/CI状态，不修改用户既有提交。
5. 推送当前隔离开发分支到已核对的 GitHub 远端；首次设置该分支 upstream，后续沿用。远端产生非快进冲突时先读取差异、保留双方成果，再处理并重测，不使用强推覆盖远端。
6. 核对远端分支指向该提交，再检查同一提交的 CI。能取得结果时报告运行链接和状态；若权限/网络等阻断，只报告已完成的本地成果、提交和具体失败，不把“准备推送”说成已同步。

本轮同步完成后，交付记录应包括分支、各阶段提交 SHA、实际测试命令/退出码、安全摘要、push 结果、远端 CI 链接/状态以及未执行的真实模型验收。测试数量和 SHA 必须来自实际执行，不提前填入计划。

## 不随自动同步执行的操作

- 不 `force push`、覆盖其他未合并分支、自动合并 `master/main` 或改写用户提交历史。
- 不把 CI 触发当成发布授权，不部署生产、公开后台或执行真实客户交易。
- 不提交或上传 `.env`、令牌、密钥、真实客户资料/数据库、运行日志、备份和大型模型权重；只提交不含凭据的模板与合成测试数据。
- 不因为缺少显式 token 环境变量就认定 GitHub 无认证。优先复用平台 Git HTTPS 代理及已有认证，诊断实际读写结果，不打印认证值。

真实模型、硬件和上线验证是另一个明确阶段，操作入口见 [LOCAL_MODEL_DEPLOYMENT.md](LOCAL_MODEL_DEPLOYMENT.md)、[DEPLOYMENT.md](DEPLOYMENT.md)。本轮优化的收益与验收目标见 [OPTIMIZATION_PLAN.md](OPTIMIZATION_PLAN.md)。
