"""Server-side administration boundary; credentials are never returned or logged."""

import os
import secrets

from fastapi import Header, HTTPException


def require_admin(authorization: str | None = Header(default=None)) -> None:
    expected = os.getenv("ADMIN_API_TOKEN", "")
    if not expected:
        raise HTTPException(503, detail={
            "status": "BLOCKED", "code": "admin_not_configured",
            "message": "管理员凭据尚未配置。",
        })
    scheme, _, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(401, detail={"code": "unauthorized", "message": "需要管理员授权。"})
