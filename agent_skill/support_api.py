"""Website text support. Administration and visitor capabilities stay separate."""

import os
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Header, Request, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent_skill.auth import require_admin
from customer_service.controls import RateLimiter
from customer_service.store import SupportError


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ProductInput(InputModel):
    id: str | None = Field(None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=6000)
    price_cents: int = Field(ge=0, le=999999999, strict=True)
    currency: Literal["CNY"] = "CNY"
    url: str = Field("", max_length=2000)
    active: bool = True

    @field_validator("url")
    @classmethod
    def safe_url(cls, value: str) -> str:
        if value:
            parsed = urlsplit(value)
            if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("商品链接必须为不含凭据的 HTTP(S) 地址")
        return value


class KnowledgeInput(InputModel):
    id: str | None = Field(None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=12000)
    kind: Literal["product", "faq", "policy"] = "faq"
    product_id: str | None = Field(None, min_length=1, max_length=64)
    active: bool = True
    source_note: str | None = Field(None, max_length=1000)


class MessageInput(InputModel):
    message: str = Field(min_length=1, max_length=2000)


class LeadInput(InputModel):
    consent: bool = Field(strict=True)
    name: str = Field("", max_length=100)
    contact: str = Field("", max_length=200)
    need: str = Field("", max_length=1000)


class HandoffInput(InputModel):
    reason: str = Field("customer_request", min_length=1, max_length=200)


class ResolveInput(InputModel):
    reply: str = Field(min_length=1, max_length=4000)


class ConfirmationInput(InputModel):
    confirm: bool = Field(strict=True)

    @field_validator("confirm")
    @classmethod
    def confirmed(cls, value):
        if value is not True:
            raise ValueError("操作必须明确确认")
        return value


class RetentionInput(InputModel):
    days: int = Field(ge=1, le=36500, strict=True)


class KnowledgeApprovalInput(ConfirmationInput):
    expected_updated_at: str = Field(min_length=1, max_length=64)


class PurgeInput(RetentionInput, ConfirmationInput):
    pass


def limit_setting(name, default, *, minimum=0, maximum=1000000):
    raw = os.getenv(name, str(default)).strip()
    if not raw.isascii() or not raw.isdecimal() or not minimum <= int(raw) <= maximum:
        raise SupportError("configuration", "资源限制配置无效，请联系管理员。", 503)
    return int(raw)


def build_router(service, store, gateway) -> APIRouter:
    router = APIRouter()
    admin = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])
    limiter = RateLimiter(store)

    @router.get("/support/products")
    def catalog():
        return store.list_products(active_only=True)

    @router.post("/support/conversations", status_code=201)
    def create_conversation(request: Request):
        limiter.check("create_conversation", request.client.host if request.client else "unknown",
                      limit_setting("SUPPORT_CREATE_LIMIT_PER_HOUR", 20), 3600)
        return service.create_conversation()

    @router.get("/support/conversations/{conversation_id}")
    def conversation(conversation_id: str, x_session_token: str = Header(default="")):
        return service.get_conversation(conversation_id, x_session_token)

    @router.post("/support/conversations/{conversation_id}/messages")
    def message(conversation_id: str, payload: MessageInput, x_session_token: str = Header(default="")):
        store.authorize(conversation_id, x_session_token)
        limiter.check("conversation_message", conversation_id,
                      limit_setting("SUPPORT_MESSAGE_LIMIT_PER_MINUTE", 20), 60)
        return service.chat(conversation_id, x_session_token, payload.message)

    @router.post("/support/conversations/{conversation_id}/close")
    def close_visitor(conversation_id: str, x_session_token: str = Header(default="")):
        return service.close_conversation(conversation_id, x_session_token)

    @router.delete("/support/conversations/{conversation_id}")
    def delete_visitor(conversation_id: str, payload: ConfirmationInput, x_session_token: str = Header(default="")):
        return service.delete_conversation(conversation_id, x_session_token)

    @router.delete("/support/conversations/{conversation_id}/leads")
    def withdraw_visitor(conversation_id: str, payload: ConfirmationInput, x_session_token: str = Header(default="")):
        return service.withdraw_lead(conversation_id, x_session_token)

    @router.post("/support/conversations/{conversation_id}/leads", status_code=201)
    def lead(conversation_id: str, payload: LeadInput, x_session_token: str = Header(default="")):
        return service.save_lead(conversation_id, x_session_token, **payload.model_dump())

    @router.post("/support/conversations/{conversation_id}/handoff")
    def handoff(conversation_id: str, payload: HandoffInput, x_session_token: str = Header(default="")):
        return service.request_handoff(conversation_id, x_session_token, payload.reason)

    @admin.get("/products")
    def list_products():
        return store.list_products()

    @admin.post("/products")
    def product(payload: ProductInput):
        return store.upsert_product(**payload.model_dump())

    @admin.get("/knowledge")
    def list_knowledge():
        return store.list_knowledge()

    @admin.post("/knowledge")
    def knowledge(payload: KnowledgeInput):
        return store.upsert_knowledge(**payload.model_dump())

    @admin.post("/knowledge/{knowledge_id}/approve")
    def approve_knowledge(knowledge_id: str, payload: KnowledgeApprovalInput):
        return store.approve_knowledge(knowledge_id, expected_updated_at=payload.expected_updated_at)

    @admin.get("/conversations")
    def list_conversations():
        return store.list_conversations()

    @admin.get("/conversations/{conversation_id}")
    def admin_conversation(conversation_id: str):
        return store.admin_get_conversation(conversation_id)

    @admin.post("/conversations/{conversation_id}/close")
    def close_admin(conversation_id: str):
        return store.close_conversation(conversation_id, actor="admin")

    @admin.post("/conversations/{conversation_id}/resume")
    def resume_admin(conversation_id: str):
        return store.resume_ai(conversation_id)

    @admin.delete("/conversations/{conversation_id}")
    def delete_admin(conversation_id: str, payload: ConfirmationInput):
        return store.delete_conversation(conversation_id, actor="admin")

    @admin.delete("/conversations/{conversation_id}/lead")
    def withdraw_admin(conversation_id: str, payload: ConfirmationInput):
        return store.delete_lead(conversation_id, actor="admin")

    @admin.post("/privacy/retention/preview")
    def retention_preview(payload: RetentionInput):
        return store.purge_expired(payload.days, dry_run=True)

    @admin.post("/privacy/retention/purge")
    def retention_purge(payload: PurgeInput):
        return store.purge_expired(payload.days, dry_run=False)

    @admin.get("/audit")
    def audit(limit: int = Query(100, ge=1, le=1000)):
        return store.list_audit_events(limit=limit)

    @admin.get("/limits")
    def resource_limits():
        return {"rate_limits": {
            "create_per_hour": limit_setting("SUPPORT_CREATE_LIMIT_PER_HOUR", 20),
            "message_per_minute": limit_setting("SUPPORT_MESSAGE_LIMIT_PER_MINUTE", 20)},
            "model_budget": {"scope": "customer_service", **service.budget.summary()},
            "retention_days": limit_setting("SUPPORT_RETENTION_DAYS", 30, minimum=1, maximum=36500),
            "model": gateway.status()}

    @admin.get("/handoffs")
    def list_handoffs(status: str | None = None):
        return store.list_handoffs(status=status)

    @admin.post("/handoffs/{handoff_id}/resolve")
    def resolve(handoff_id: str, payload: ResolveInput):
        return store.resolve_handoff(handoff_id, payload.reply)

    @admin.get("/leads")
    def leads():
        return store.list_leads()

    @admin.get("/model/status")
    def model_status():
        from core.model_gateway import ModelGateway
        return {**gateway.status(), "local_provider": ModelGateway("ollama").status()}

    @admin.get("/model/usage")
    def usage():
        return store.usage_summary()

    router.include_router(admin)
    return router
