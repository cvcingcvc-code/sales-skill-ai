"""Streamlit 客服管理界面；所有授权与数据操作由 FastAPI 后端执行。"""

import os
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
import streamlit as st


class SupportAPIError(RuntimeError):
    """Only safe, public error messages may cross the UI boundary."""


class SupportAPI:
    def __init__(self, base_url: str, admin_token: str = ""):
        parts = urlsplit(base_url)
        if parts.scheme not in {"http", "https"} or not parts.netloc or parts.username or parts.password:
            raise SupportAPIError("BLOCKED：SUPPORT_API_URL 必须是有效的 HTTP(S) 服务地址。")
        self.base_url = base_url.rstrip("/")
        self.admin_token = admin_token

    def request(self, method: str, path: str, body: dict | None = None,
                session_token: str = "", admin: bool = False) -> Any:
        headers = {}
        if admin:
            if not self.admin_token:
                raise SupportAPIError("BLOCKED：管理接口需要 ADMIN_API_TOKEN。")
            headers["Authorization"] = f"Bearer {self.admin_token}"
        if session_token:
            headers["X-Session-Token"] = session_token
        try:
            # Never follow a redirect carrying credentials to a different service.
            response = httpx.request(method, self.base_url + path, json=body, headers=headers,
                                     timeout=45, follow_redirects=False)
            response.raise_for_status()
            return response.json()
        except httpx.TimeoutException:
            raise SupportAPIError("BLOCKED：客服 API 请求超时，请检查服务状态后重试。") from None
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            if code in {401, 403}:
                message = "BLOCKED：访问未获授权，请检查对应的管理员或会话凭证。"
            elif code in {502, 503, 504}:
                message = "BLOCKED：模型或客服服务暂不可用，请检查配置和网络。"
            elif code == 404:
                message = "请求的记录不存在或已不可访问。"
            elif code in {400, 409, 422}:
                message = "请求未保存，请检查必填字段、数据格式与记录状态。"
            elif 300 <= code < 400:
                message = "BLOCKED：API 返回重定向，请配置服务的直接地址。"
            else:
                message = "BLOCKED：客服 API 未完成请求，请检查服务日志。"
            raise SupportAPIError(message) from None
        except (httpx.RequestError, ValueError):
            raise SupportAPIError("BLOCKED：无法连接客服 API，或服务返回了无效数据。") from None


def _call(api: SupportAPI, method: str, path: str, **kwargs):
    try:
        return api.request(method, path, **kwargs)
    except SupportAPIError as exc:
        st.error(str(exc))
        return None


def _rows(data: Any, *keys: str) -> list[dict]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in (*keys, "items"):
            if isinstance(data.get(key), list):
                return [item for item in data[key] if isinstance(item, dict)]
    return []


def _table(rows: list[dict], fields: tuple[str, ...], empty: str = "暂无记录"):
    if not rows:
        st.info(empty)
        return
    # Explicit field allowlist prevents tokens or internal metadata from being rendered.
    st.dataframe([{key: row.get(key) for key in fields} for row in rows], hide_index=True,
                 use_container_width=True)


def _id(value: str) -> str:
    return quote(str(value), safe="")


def _managed_catalog(api: SupportAPI, authorized: bool):
    st.subheader("产品与知识库")
    if not authorized:
        return
    products = _call(api, "GET", "/admin/products", admin=True)
    _table(_rows(products, "products"), ("id", "name", "description", "price_cents", "currency", "url", "active"))
    with st.form("support_product_form", clear_on_submit=False):
        st.write("新增或更新商品；更新时填写列表中的商品 ID。")
        product_id = st.text_input("商品 ID（新增留空）", max_chars=64)
        name = st.text_input("商品名称", max_chars=200)
        description = st.text_area("商品描述", max_chars=6000)
        price = st.number_input("价格（分，人民币）", min_value=0, value=0, step=1)
        url = st.text_input("商品链接", max_chars=2000)
        active = st.checkbox("商品上架", value=True)
        submitted = st.form_submit_button("保存商品")
    if submitted:
        if not name.strip() or not description.strip():
            st.warning("请填写商品名称与描述。")
        else:
            body = {"name": name.strip(), "description": description, "price_cents": int(price),
                    "currency": "CNY", "url": url, "active": active}
            if product_id.strip():
                body["id"] = product_id.strip()
            if _call(api, "POST", "/admin/products", body=body, admin=True) is not None:
                st.success("商品已保存；列表将在下次操作时更新。")
    knowledge = _call(api, "GET", "/admin/knowledge", admin=True)
    _table(_rows(knowledge, "knowledge"), ("id", "title", "content", "kind", "product_id", "active"))
    with st.form("support_knowledge_form", clear_on_submit=False):
        st.write("管理商品文档、常见问题与售后政策。")
        knowledge_id = st.text_input("知识 ID（新增留空）", max_chars=64)
        title = st.text_input("知识标题", max_chars=200)
        content = st.text_area("知识内容", max_chars=12000)
        kind_label = st.selectbox("知识类型", ["商品文档", "常见问题", "售后政策"])
        related_product = st.text_input("关联商品 ID（可选）", max_chars=64)
        knowledge_active = st.checkbox("知识启用", value=True)
        saved = st.form_submit_button("保存知识")
    if saved:
        if not title.strip() or not content.strip():
            st.warning("请填写知识标题与内容。")
        else:
            body = {"title": title.strip(), "content": content,
                    "kind": {"商品文档": "product", "常见问题": "faq", "售后政策": "policy"}[kind_label],
                    "active": knowledge_active}
            if knowledge_id.strip():
                body["id"] = knowledge_id.strip()
            if related_product.strip():
                body["product_id"] = related_product.strip()
            if _call(api, "POST", "/admin/knowledge", body=body, admin=True) is not None:
                st.success("知识已保存；列表将在下次操作时更新。")


def _render_message_metadata(payload: dict):
    model_status = payload.get("model_status", "unknown")
    source = payload.get("source", "unknown")
    st.caption(f"回复来源：{source} · 模型状态：{model_status}")
    if str(model_status).lower() in {"blocked", "unavailable", "error", "timeout", "not_configured"}:
        st.warning("BLOCKED：本次真实模型调用未完成。检索或人工转接结果不代表模型调用成功。")
    if str(model_status).upper() == "MOCK" or "mock" in str(source).lower():
        st.warning("MOCK：这是测试替身结果，不能作为真实模型调用成功的依据。")
    if str(model_status).upper() == "NOT_CALLED":
        st.caption("本次请求未调用模型。")
    if payload.get("need_summary"):
        st.write("需求摘要：", payload["need_summary"])
    products = _rows(payload.get("products", []))
    if products:
        st.write("相关商品（以后台记录为准）")
        _table(products, ("id", "name", "price_cents", "currency", "url"))
    citations = payload.get("citations", [])
    if citations:
        st.write("资料引用")
        for citation in citations:
            if isinstance(citation, dict):
                st.write(citation.get("title") or citation.get("id") or citation.get("source_id", "资料"))
            else:
                st.write(str(citation))
    if payload.get("handoff") or payload.get("handoff_id"):
        st.info("已进入人工接管流程。")


def _chat_test(api: SupportAPI):
    st.subheader("AI 客服测试")
    st.caption("测试消息会记录在会话中。请使用测试资料，避免填写无关的敏感信息。")
    if st.button("创建新的测试会话"):
        session = _call(api, "POST", "/support/conversations", body={})
        if isinstance(session, dict) and session.get("id") and session.get("session_token"):
            st.session_state.support_session_id = session["id"]
            st.session_state.support_session_token = session["session_token"]
            st.session_state.support_privacy_notice = session.get("privacy_notice", "会话消息将保存用于本次客服服务。")
            st.session_state.pop("support_last_reply", None)
            # Consent and contact details belong to one visitor/session only.
            for key in ("support_lead_consent", "support_lead_name", "support_lead_contact", "support_lead_need"):
                st.session_state.pop(key, None)
    session_id = st.session_state.get("support_session_id")
    session_token = st.session_state.get("support_session_token", "")
    if not session_id or not session_token:
        st.info("创建测试会话后即可咨询商品、记录需求和申请人工接管。")
        return
    st.info(st.session_state.get("support_privacy_notice", "会话消息将保存用于客服服务。"))
    path = f"/support/conversations/{_id(session_id)}"
    conversation = _call(api, "GET", path, session_token=session_token)
    if isinstance(conversation, dict):
        st.caption(f"会话：{session_id} · 状态：{conversation.get('status', 'unknown')}")
        for message in _rows(conversation.get("messages", [])):
            role = "user" if message.get("role") in {"user", "customer"} else "assistant"
            with st.chat_message(role):
                st.write(message.get("content", message.get("message", "")))
    with st.form("support_chat_form", clear_on_submit=True):
        message = st.text_area("客户消息", placeholder="例如：预算 500 元，有适合办公的商品吗？", max_chars=2000)
        send = st.form_submit_button("发送客服消息")
    if send:
        if not message.strip():
            st.warning("请先输入消息。")
        else:
            reply = _call(api, "POST", path + "/messages", body={"message": message}, session_token=session_token)
            if isinstance(reply, dict):
                st.session_state.support_last_reply = reply
                st.rerun()
    if isinstance(st.session_state.get("support_last_reply"), dict):
        _render_message_metadata(st.session_state.support_last_reply)
    with st.form("support_lead_form"):
        st.write("自愿留下联系信息；拒绝或不勾选同意不影响继续咨询。")
        consent = st.checkbox("我已了解用途并同意保存以下联系信息和需求", value=False, key="support_lead_consent")
        name = st.text_input("称呼（可选）", max_chars=100, key="support_lead_name")
        contact = st.text_input("联系方式（手机号或邮箱）", max_chars=200, key="support_lead_contact")
        need = st.text_area("需要人工跟进的需求", max_chars=1000, key="support_lead_need")
        save_lead = st.form_submit_button("保存客户线索")
    if save_lead:
        if not consent:
            st.warning("未取得同意，未发送或保存客户线索。")
        elif not contact.strip():
            st.warning("请填写用于跟进的手机号或邮箱。")
        else:
            saved = _call(api, "POST", path + "/leads", body={"consent": True, "name": name,
                          "contact": contact, "need": need}, session_token=session_token)
            if saved is not None:
                st.success("已按你的同意保存联系信息与需求。")
    with st.form("support_handoff_form"):
        reason = st.text_area("申请人工接管的原因", value="需要人工协助", max_chars=200)
        handoff = st.form_submit_button("转接人工客服")
    if handoff:
        if _call(api, "POST", path + "/handoff", body={"reason": reason}, session_token=session_token) is not None:
            st.success("人工接管请求已提交。")


def _conversation_history(api: SupportAPI, authorized: bool):
    st.subheader("客户会话")
    if not authorized:
        return
    rows = _rows(_call(api, "GET", "/admin/conversations", admin=True), "conversations")
    _table(rows, ("id", "status", "need_summary", "created_at", "updated_at"))
    if rows:
        selected = st.selectbox("查看会话", [str(row["id"]) for row in rows if row.get("id")])
        conversation = _call(api, "GET", "/admin/conversations/" + _id(selected), admin=True)
        if isinstance(conversation, dict):
            st.write("需求摘要：", conversation.get("need_summary", ""))
            for message in _rows(conversation.get("messages", [])):
                st.write(f"{message.get('role', '')}：", message.get("content", ""))
    st.write("已同意保存的客户线索（仅管理员可见）")
    _table(_rows(_call(api, "GET", "/admin/leads", admin=True), "leads"),
           ("id", "conversation_id", "name", "contact", "need", "consent_version", "consent_at"))


def _handoffs(api: SupportAPI, authorized: bool):
    st.subheader("人工接管队列")
    if not authorized:
        return
    rows = _rows(_call(api, "GET", "/admin/handoffs", admin=True), "handoffs")
    _table(rows, ("id", "conversation_id", "reason", "status", "created_at", "reply"))
    pending = [row for row in rows if row.get("status") not in {"resolved", "closed"} and row.get("id")]
    if not pending:
        return
    with st.form("support_resolve_handoff"):
        selected = st.selectbox("待处理人工请求", [str(row["id"]) for row in pending])
        reply = st.text_area("人工回复", max_chars=4000, help="回复将进入客户会话。付款、退款、特殊优惠等请按后台权限与人工流程处理。")
        resolve = st.form_submit_button("回复并完成接管")
    if resolve:
        if not reply.strip():
            st.warning("请填写人工回复。")
        elif _call(api, "POST", "/admin/handoffs/" + _id(selected) + "/resolve",
                   body={"reply": reply}, admin=True) is not None:
            st.success("人工回复已发送；队列将在下次操作时更新。")


def _model_status(api: SupportAPI, authorized: bool):
    st.subheader("模型状态和调用成本")
    if not authorized:
        return
    status = _call(api, "GET", "/admin/model/status", admin=True)
    if isinstance(status, dict):
        # Do not dump provider configuration: it may contain credentials.
        _table([status], ("provider", "model", "status", "available", "configured"))
        if str(status.get("status", "")).upper() == "BLOCKED":
            st.warning("BLOCKED：当前模型配置或连通性不可用，尚未通过真实模型验收。")
        local_provider = status.get("local_provider")
        if isinstance(local_provider, dict):
            st.write("Ollama 本地模型")
            _table([local_provider], ("provider", "model", "status", "available", "configured"))
            if str(local_provider.get("status", "")).upper() == "BLOCKED":
                st.info("Ollama：不可用。请先部署并配置本地模型，再验证连通性。")
        providers = status.get("providers")
        if isinstance(providers, dict):
            for name, provider in providers.items():
                if isinstance(provider, dict):
                    _table([{"provider": name, **{k: v for k, v in provider.items() if k in {"model", "status", "available", "configured"}}}],
                           ("provider", "model", "status", "available", "configured"))
        elif isinstance(providers, list):
            _table(_rows(providers), ("provider", "model", "status", "available", "configured"))
        st.caption("配置存在不等于真实调用成功；真实连通性以请求结果为准。Ollama 未部署时应显示不可用。")
    usage = _call(api, "GET", "/admin/model/usage", admin=True)
    if isinstance(usage, dict):
        _table([usage], ("total_calls", "blocked_calls", "prompt_tokens", "completion_tokens", "unknown_cost_calls"))
        costs = usage.get("estimated_cost")
        if costs is None:
            costs = usage.get("estimated_cost_cny")
        if costs is None:
            st.info("调用成本：未知。未配置可信价格或缺少计费用量时，不估算金额。")
        else:
            st.metric("已知调用成本估算（配置单价币种）", str(costs))
            if usage.get("unknown_cost_calls", 0):
                st.warning("部分调用成本未知，以上仅为已知部分，不能作为总成本。")
        st.caption("按管理员配置单价估算，不代表服务商实际账单。")
        records = _rows(usage, "recent", "calls", "records", "usage")
        if records:
            _table(records, ("provider", "model", "status", "prompt_tokens", "completion_tokens", "total_tokens", "estimated_cost", "created_at"))


def render_support_admin(admin_token: str = ""):
    st.title("AI 销售与客服工作台")
    with st.sidebar:
        st.caption("客服管理调用已配置的后端服务。")
        st.caption("令牌仅用于已登录会话的请求，不展示在表格、报告或日志中。")
    try:
        api = SupportAPI(os.environ.get("SUPPORT_API_URL", "http://127.0.0.1:8080"), admin_token)
    except SupportAPIError as exc:
        st.error(str(exc))
        return
    authorized = bool(admin_token)
    if not authorized:
        st.warning("BLOCKED：管理页面需要先登录当前浏览器会话。")
    tabs = st.tabs(["产品与知识库", "AI 客服测试", "客户会话", "人工接管队列", "模型状态和调用成本"])
    for tab, renderer in zip(tabs, (_managed_catalog, _chat_test, _conversation_history, _handoffs, _model_status)):
        with tab:
            if renderer is _chat_test:
                renderer(api)
            else:
                renderer(api, authorized)
