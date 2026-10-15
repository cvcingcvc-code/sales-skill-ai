"""Website text support. Administration and visitor capabilities stay separate."""

from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent_skill.auth import require_admin


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


def build_router(service, store, gateway) -> APIRouter:
    router = APIRouter()
    admin = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])

    @router.get("/support/products")
    def catalog():
        return store.list_products(active_only=True)

    @router.post("/support/conversations", status_code=201)
    def create_conversation():
        return service.create_conversation()

    @router.get("/support/conversations/{conversation_id}")
    def conversation(conversation_id: str, x_session_token: str = Header(default="")):
        return service.get_conversation(conversation_id, x_session_token)

    @router.post("/support/conversations/{conversation_id}/messages")
    def message(conversation_id: str, payload: MessageInput, x_session_token: str = Header(default="")):
        return service.chat(conversation_id, x_session_token, payload.message)

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

    @admin.get("/conversations")
    def list_conversations():
        return store.list_conversations()

    @admin.get("/conversations/{conversation_id}")
    def admin_conversation(conversation_id: str):
        return store.admin_get_conversation(conversation_id)

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
