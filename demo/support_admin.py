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
            elif code == 429:
                message = "请求过于频繁或已达到调用预算，请稍后重试或联系管理员。"
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
    if products is None:
        return
    products = _rows(products, "products")
    _table(products, ("id", "name", "description", "price_cents", "currency", "url", "active"))
    product_by_id = {item["id"]: item for item in products if item.get("id")}
    saved_product = st.session_state.pop("support_saved_product", None)
    if saved_product:
        st.success("商品已保存。")
        if saved_product in product_by_id:
            st.session_state.support_product_selection = saved_product
    selected_product = st.selectbox(
        "选择商品", [None, *product_by_id], key="support_product_selection",
        format_func=lambda value: "新增商品" if value is None else f"{product_by_id[value]['name']}（{value}）",
    )
    product = product_by_id.get(selected_product, {})
    # A separate form identity per record prevents drafts leaking into another item.
    with st.form(f"support_product_form_{'new' if selected_product is None else 'edit_' + selected_product}", clear_on_submit=False):
        product_id = st.text_input("商品 ID", value=product.get("id", ""), max_chars=64,
                                   disabled=selected_product is not None, help="新增可留空自动生成；编辑已有商品时 ID 锁定。")
        name = st.text_input("商品名称", value=product.get("name", ""), max_chars=200)
        description = st.text_area("商品描述", value=product.get("description", ""), max_chars=6000)
        price = st.number_input("价格（分，人民币）", min_value=0, value=product.get("price_cents", 0), step=1)
        url = st.text_input("商品链接", value=product.get("url", ""), max_chars=2000)
        active = st.checkbox("商品上架", value=product.get("active", True))
        submitted = st.form_submit_button("保存商品")
    if submitted:
        if not name.strip() or not description.strip():
            st.warning("请填写商品名称与描述。")
        elif selected_product is None and product_id.strip() in product_by_id:
            st.warning("商品 ID 已存在，请从“选择商品”加载记录后编辑。")
        else:
            body = {"name": name.strip(), "description": description, "price_cents": int(price),
                    "currency": "CNY", "url": url, "active": active}
            if selected_product is not None or product_id.strip():
                body["id"] = selected_product if selected_product is not None else product_id.strip()
            saved = _call(api, "POST", "/admin/products", body=body, admin=True)
            if isinstance(saved, dict) and saved.get("id"):
                st.session_state.support_saved_product = saved["id"]
                st.rerun()
    knowledge = _call(api, "GET", "/admin/knowledge", admin=True)
    if knowledge is None:
        return
    knowledge = _rows(knowledge, "knowledge")
    _table(knowledge, ("id", "title", "content", "kind", "product_id", "active"))
    knowledge_by_id = {item["id"]: item for item in knowledge if item.get("id")}
    saved_knowledge = st.session_state.pop("support_saved_knowledge", None)
    if saved_knowledge:
        st.success("知识已保存。")
        if saved_knowledge in knowledge_by_id:
            st.session_state.support_knowledge_selection = saved_knowledge
    selected_knowledge = st.selectbox(
        "选择知识", [None, *knowledge_by_id], key="support_knowledge_selection",
        format_func=lambda value: "新增知识" if value is None else f"{knowledge_by_id[value]['title']}（{value}）",
    )
    document = knowledge_by_id.get(selected_knowledge, {})
    kinds = {"商品文档": "product", "常见问题": "faq", "售后政策": "policy"}
    with st.form(f"support_knowledge_form_{'new' if selected_knowledge is None else 'edit_' + selected_knowledge}", clear_on_submit=False):
        knowledge_id = st.text_input("知识 ID", value=document.get("id", ""), max_chars=64,
                                     disabled=selected_knowledge is not None, help="新增可留空自动生成；编辑已有知识时 ID 锁定。")
        title = st.text_input("知识标题", value=document.get("title", ""), max_chars=200)
        content = st.text_area("知识内容", value=document.get("content", ""), max_chars=12000)
        kind_label = st.selectbox("知识类型", list(kinds), index=list(kinds.values()).index(document.get("kind", "product")))
        related_product = st.text_input("关联商品 ID（可选）", value=document.get("product_id") or "", max_chars=64)
        knowledge_active = st.checkbox("知识启用", value=document.get("active", True))
        saved = st.form_submit_button("保存知识")
    if saved:
        if not title.strip() or not content.strip():
            st.warning("请填写知识标题与内容。")
        elif selected_knowledge is None and knowledge_id.strip() in knowledge_by_id:
            st.warning("知识 ID 已存在，请从“选择知识”加载记录后编辑。")
        else:
            body = {"title": title.strip(), "content": content,
                    "kind": kinds[kind_label],
                    "active": knowledge_active}
            if selected_knowledge is not None or knowledge_id.strip():
                body["id"] = selected_knowledge if selected_knowledge is not None else knowledge_id.strip()
            if related_product.strip():
                body["product_id"] = related_product.strip()
            result = _call(api, "POST", "/admin/knowledge", body=body, admin=True)
            if isinstance(result, dict) and result.get("id"):
                st.session_state.support_saved_knowledge = result["id"]
                st.rerun()


def _render_message_metadata(payload: dict):
    model_status = payload.get("model_status", "unknown")
    source = payload.get("source", "unknown")
    st.caption(f"回复来源：{source} · 模型状态：{model_status}")
    if str(model_status).lower() in {"blocked", "unavailable", "error", "timeout", "not_configured"}:
        st.warning("BLOCKED：本次真实模型调用未完成。检索或人工转接结果不代表模型调用成功。")
    if str(model_status).upper() == "MOCK" or "mock" in str(source).lower():
        st.warning("MOCK：这是测试替身结果，不能作为真实模型调用成功的依据。")
    if str(model_status).upper() == "DISABLED":
        st.info("DISABLED：管理员已主动暂停模型；当前未连接本地或云端模型。")
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
    closed = isinstance(conversation, dict) and conversation.get("status") == "closed"
    if closed:
        st.info("会话已关闭，可查看记录或在访客页面执行隐私删除；不能继续发送消息、留资或转人工。")
    if isinstance(conversation, dict):
        st.caption(f"会话：{session_id} · 状态：{conversation.get('status', 'unknown')}")
        for message in _rows(conversation.get("messages", [])):
            role = "user" if message.get("role") in {"user", "customer"} else "assistant"
            with st.chat_message(role):
                st.write(message.get("content", message.get("message", "")))
    with st.form("support_chat_form", clear_on_submit=True):
        message = st.text_area("客户消息", placeholder="例如：预算 500 元，有适合办公的商品吗？", max_chars=2000)
        send = st.form_submit_button("发送客服消息", disabled=closed)
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
        save_lead = st.form_submit_button("保存客户线索", disabled=closed)
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
        handoff = st.form_submit_button("转接人工客服", disabled=closed)
    if handoff:
        if _call(api, "POST", path + "/handoff", body={"reason": reason}, session_token=session_token) is not None:
            st.success("人工接管请求已提交。")


def _conversation_history(api: SupportAPI, authorized: bool):
    st.subheader("客户会话")
    if not authorized:
        return
    notice = st.session_state.pop("support_conversation_notice", None)
    if notice:
        st.success(notice)
    reset_lead = st.session_state.pop("support_reset_lead_confirmation", None)
    if reset_lead:
        st.session_state[f"lead_delete_confirm_{reset_lead}"] = False
    rows = _rows(_call(api, "GET", "/admin/conversations", admin=True), "conversations")
    _table(rows, ("id", "status", "need_summary", "created_at", "last_activity_at"))
    ids = [str(row["id"]) for row in rows if row.get("id")]
    if ids:
        selected = st.selectbox("查看会话", ids)
        path = "/admin/conversations/" + _id(selected)
        conversation = _call(api, "GET", path, admin=True)
        if isinstance(conversation, dict):
            state = conversation.get("status", "unknown")
            st.caption(f"状态：{state} · 最近活动：{conversation.get('last_activity_at', '未知')}")
            st.write("需求摘要：", conversation.get("need_summary", ""))
            for message in _rows(conversation.get("messages", [])):
                st.write(f"{message.get('role', '')}：", message.get("content", ""))
            handoffs = _call(api, "GET", "/admin/handoffs", admin=True)
            pending_known = isinstance(handoffs, (list, dict))
            pending = [row for row in _rows(handoffs, "handoffs")
                       if row.get("conversation_id") == selected and row.get("status") == "pending"]
            if pending:
                st.info("请先在人工接管队列回复并完成全部待办，再恢复 AI。")
            if st.button("关闭所选会话", disabled=state == "closed", key=f"close_{selected}"):
                if _call(api, "POST", path + "/close", body={}, admin=True) is not None:
                    st.session_state.support_conversation_notice = "会话已关闭；关闭后不能发送消息或留资。"
                    st.rerun()
            if st.button("恢复所选会话的 AI", disabled=state != "human" or bool(pending) or not pending_known,
                         key=f"resume_{selected}"):
                if _call(api, "POST", path + "/resume", body={}, admin=True) is not None:
                    st.session_state.support_conversation_notice = "AI 已恢复；模型是否启用仍由后端配置决定。"
                    st.rerun()
            lead_confirm = st.checkbox("确认撤回所选会话的联系线索", value=False, key=f"lead_delete_confirm_{selected}")
            if st.button("撤回所选联系线索", disabled=not lead_confirm, key=f"lead_delete_{selected}"):
                if _call(api, "DELETE", path + "/lead", body={"confirm": True}, admin=True) is not None:
                    st.session_state.support_conversation_notice = "联系线索已撤回；会话消息仍按保留策略管理。"
                    st.session_state.support_reset_lead_confirmation = selected
                    st.rerun()
            st.caption("永久删除会移除所选会话、消息、线索和关联接管记录，仅保留无正文的审计元数据。")
            delete_confirm = st.checkbox("我确认永久删除所选会话及关联数据，且无法恢复", value=False,
                                         key=f"conversation_delete_confirm_{selected}")
            if st.button("永久删除所选会话", disabled=not delete_confirm, key=f"conversation_delete_{selected}"):
                if _call(api, "DELETE", path, body={"confirm": True}, admin=True) is not None:
                    st.session_state.support_conversation_notice = "会话已永久删除。"
                    st.rerun()
    st.write("已同意保存的客户线索（仅管理员可见）")
    _table(_rows(_call(api, "GET", "/admin/leads", admin=True), "leads"),
           ("id", "conversation_id", "name", "contact", "need", "consent_version", "consent_at"))


def _privacy(api: SupportAPI, authorized: bool):
    st.subheader("隐私与审计")
    if not authorized:
        return
    notice = st.session_state.pop("support_privacy_notice_result", None)
    if notice:
        st.success(notice)
    if st.session_state.pop("support_retention_clear_confirm", False):
        st.session_state["support_retention_confirm"] = False
    st.write("仅清理已关闭、超过保留天数且无待办人工接管的会话及关联记录。先预览，再明确确认。")
    limits = _call(api, "GET", "/admin/limits", admin=True)
    configured_days = limits.get("retention_days", 90) if isinstance(limits, dict) else 90
    if type(configured_days) is not int or not 1 <= configured_days <= 36500:
        configured_days = 90
    days = st.number_input("会话保留天数", min_value=1, max_value=36500, value=configured_days, step=1,
                           key="support_retention_days")
    if st.session_state.get("support_retention_previous_days") != int(days):
        st.session_state.pop("support_retention_preview", None)
        st.session_state["support_retention_confirm"] = False
        st.session_state.support_retention_previous_days = int(days)
    if st.button("预览到期数据"):
        # A fresh preview invalidates any earlier deletion consent.
        st.session_state.pop("support_retention_preview", None)
        st.session_state["support_retention_confirm"] = False
        preview = _call(api, "POST", "/admin/privacy/retention/preview", body={"days": int(days)}, admin=True)
        if isinstance(preview, dict) and preview.get("dry_run") is True:
            st.session_state.support_retention_preview = {"days": int(days), "result": preview}
    saved = st.session_state.get("support_retention_preview")
    current_preview = isinstance(saved, dict) and saved.get("days") == int(days)
    if current_preview:
        result = saved.get("result", {})
        st.caption(f"预览截止时间：{result.get('cutoff', '未知')}；候选会话：{result.get('eligible_count', '未知')}")
        counts = result.get("counts")
        if isinstance(counts, dict):
            _table([counts], ("conversations", "messages", "handoffs", "leads", "model_usage"))
    else:
        st.info("请先预览当前保留天数对应的数据。修改天数后需要重新预览。")
    confirm = st.checkbox("我已核对当前预览，确认永久清理到期会话及关联数据，且无法恢复", value=False,
                          key="support_retention_confirm", disabled=not current_preview)
    if st.button("永久清理到期数据", disabled=not current_preview or not confirm):
        result = _call(api, "POST", "/admin/privacy/retention/purge", body={"days": int(days), "confirm": True}, admin=True)
        if isinstance(result, dict):
            st.session_state.support_privacy_notice_result = f"清理完成：删除 {result.get('deleted_count', '未知')} 个会话。下一次清理需要重新预览并确认。"
            st.session_state.pop("support_retention_preview", None)
            st.session_state.support_retention_clear_confirm = True
            st.rerun()
    st.write("最近审计事件（仅操作元数据，不含客户正文或凭证）")
    _table(_rows(_call(api, "GET", "/admin/audit?limit=100", admin=True), "events", "audit"),
           ("id", "event", "actor", "object_id", "created_at"))


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
        if str(status.get("status", "")).upper() == "DISABLED":
            st.info("DISABLED：管理员已主动暂停模型。当前工作台可管理商品、测试规则和处理人工接管；本地模型部署完成后再按部署说明启用。")
        if str(status.get("status", "")).upper() == "BLOCKED":
            st.warning("BLOCKED：当前模型配置或连通性不可用，尚未通过真实模型验收。")
        local_provider = status.get("local_provider")
        if isinstance(local_provider, dict):
            st.write("Ollama 本地模型")
            _table([local_provider], ("provider", "model", "status", "available", "configured"))
            if str(local_provider.get("status", "")).upper() == "DISABLED":
                st.caption("Ollama：主动暂停，未尝试连接本地服务。")
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
    limits = _call(api, "GET", "/admin/limits", admin=True)
    if isinstance(limits, dict):
        rate_limits = limits.get("rate_limits")
        if isinstance(rate_limits, dict):
            st.write("后端请求限流")
            _table([rate_limits], ("create_per_hour", "message_per_minute"))
        budget = limits.get("model_budget")
        if isinstance(budget, dict):
            st.write("模型调用预算（由后端强制执行）")
            _table([budget], ("day", "timezone", "daily_call_limit", "daily_token_limit", "calls", "tokens", "remaining_calls", "remaining_tokens"))
            _table([budget], ("max_input_tokens", "max_output_tokens", "reserved_calls", "completed_calls", "failed_calls", "daily_cost_limit", "cost_status"))
            st.caption("Token 为保守预留额度，包含失败或缺少用量的调用；缺失或未知的用量不视为零。预算日期按 UTC 计算。")
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
    tabs = st.tabs(["产品与知识库", "AI 客服测试", "客户会话", "人工接管队列", "模型状态和调用成本", "隐私与审计"])
    for tab, renderer in zip(tabs, (_managed_catalog, _chat_test, _conversation_history, _handoffs, _model_status, _privacy)):
        with tab:
            if renderer is _chat_test:
                renderer(api)
            else:
                renderer(api, authorized)
