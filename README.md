# AI Sales Agent — MVP

本仓库在销售训练项目上增加了网站文字客服和 Streamlit 管理工作台。默认关闭模型：`MODEL_ENABLED=false`、`LLM_PROVIDER=ollama`。商品查询、管理和人工接管可用；需要模型的咨询明确显示 `DISABLED` 并转人工。规则寒暄、Mock 和自动化测试均不代表真实 AI 推理成功。

## 本地启动

需要 Python 3.12；已有 `.venv` 可复用。首次安装依赖：

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.lock
```

设置私有 `ADMIN_API_TOKEN`，并在两个终端分别启动 API 和工作台。客服数据库路径可设为临时目录，避免触碰已有数据：

```bash
export MODEL_ENABLED=false LLM_PROVIDER=ollama
export ADMIN_API_TOKEN='<由密码管理器生成的随机令牌>'
export SUPPORT_DB_PATH=/tmp/sales-demo-support.sqlite3
export SUPPORT_API_URL=http://127.0.0.1:8080
.venv/bin/python -m uvicorn agent_skill.api_server:app --host 127.0.0.1 --port 8080 --workers 1 --no-access-log
```

```bash
export MODEL_ENABLED=false LLM_PROVIDER=ollama
export ADMIN_API_TOKEN='<与 API 相同的私有令牌>'
export SUPPORT_API_URL=http://127.0.0.1:8080
.venv/bin/streamlit run demo/app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true
```

访客页：<http://127.0.0.1:8080/chat>；管理工作台：<http://127.0.0.1:8501>。只在受信任的本机使用管理工作台，不要公开管理令牌或 Streamlit 端口。

## 演示与验收

1. 在工作台登录，添加合成商品和已审核政策。
2. 访客在 `/chat` 同意提示后开始会话、查看商品并咨询。
3. 确认模型状态显示 `DISABLED`，问题进入人工接管队列；管理员回复后访客可见。
4. 访客明确同意后提交合成需求/联系线索，再撤回同意并删除会话。

本版本已用隔离 SQLite 完成真实 FastAPI、Streamlit 和浏览器闭环；验证了商品/政策管理、访客商品查询、未授权管理请求拒绝、模型关闭提示、人工回复、知情同意、撤回和删除。可在 `tests/browser_smoke.cjs` 运行独立浏览器交互检查：

```bash
.venv/bin/python scripts/run_tests.py --quiet --strict --report-json /tmp/sales-mvp-tests.json
NODE_PATH="$(dirname "$(dirname "$(command -v node)")")/node_modules" SUPPORT_BROWSER_URL=http://127.0.0.1:8080 CHROMIUM_PATH=/usr/bin/chromium node tests/browser_smoke.cjs
```

严格套件离线运行；其 Mock 仅测试 Provider 合约和业务逻辑。浏览器脚本使用真实 HTTP 服务及规则寒暄，不会调用模型。真实模型效果为 **NOT_RUN**。Ollama 尚未安装/启动或完成推理验收；没有可验证的本地模型时，不得将模型问答标记为通过。部署及后续模型配置见 [本地模型说明](docs/LOCAL_MODEL_DEPLOYMENT.md)。
