"""Sales training and website text support API. Start from the repository root."""

import sys
import threading
import time
import uuid
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from agent_skill.auth import require_admin
from agent_skill.skill_interface import SkillInput, SkillOutput
from agent_skill.support_api import build_router
from core.model_gateway import ModelError, ModelGateway
from core.scorer import ScoringError
from core.skill_engine import SkillEngine
from customer_service.service import CustomerService
from customer_service.store import Store, SupportError
from scenarios.scenario_loader import list_scenarios


def _score_payload(score):
    data = asdict(score)
    data.pop("raw_response", None)
    return data


def create_app(store=None, gateway=None):
    app = FastAPI(title="AI Sales Agent", version="2.0.0")
    store = store if store is not None else Store()
    gateway = gateway if gateway is not None else ModelGateway()
    service = CustomerService(store=store, gateway=gateway)
    app.state.support_store = store
    app.state.model_gateway = gateway
    app.state.customer_service = service
    sessions: dict[str, dict] = {}
    sessions_lock = threading.RLock()

    @app.exception_handler(SupportError)
    async def support_error(_request: Request, error: SupportError):
        return JSONResponse(status_code=error.status_code, content={
            "detail": {"code": error.code, "message": error.public_message},
        }, headers={"Retry-After": str(error.retry_after)} if hasattr(error, "retry_after") else None)

    @app.exception_handler(ModelError)
    async def model_error(_request: Request, error: ModelError):
        return JSONResponse(status_code=503 if error.blocked else 502, content={
            "detail": {"status": "BLOCKED" if error.blocked else "FAILED",
                       "code": error.code, "message": error.public_message},
        })

    @app.exception_handler(ScoringError)
    async def score_error(_request: Request, error: ScoringError):
        return JSONResponse(status_code=502, content={
            "detail": {"status": "FAILED", "code": error.code, "message": error.public_message},
        })

    @app.exception_handler(Exception)
    async def unexpected_error(_request: Request, _error: Exception):
        return JSONResponse(status_code=500, content={
            "detail": {"code": "internal_error", "message": "服务暂时无法处理请求，请联系人工。"},
        })

    @app.get("/skill/health")
    def health():
        return {"status": "ok", "service": "sales-skill-ai", "version": "2.0.0"}

    @app.get("/skill/scenarios")
    def scenarios(difficulty: str | None = None):
        values = list_scenarios(difficulty)
        return {"scenarios": values, "count": len(values)}

    @app.post("/skill/invoke", response_model=SkillOutput, dependencies=[Depends(require_admin)])
    def invoke(payload: SkillInput):
        learner_id = payload.learner_id or "api_user"
        if payload.action == "evaluate":
            if not payload.sales_text:
                raise HTTPException(400, "evaluate 操作需要 sales_text")
            score = SkillEngine().evaluate_text(payload.sales_text, payload.scenario_id or "cold_visit_001")
            return SkillOutput(action="evaluate", result=_score_payload(score))

        if payload.action == "recommend":
            report = SkillEngine().recommend_for_learner(learner_id)
            progress = report.get("progress", {})
            return SkillOutput(action="recommend", result={
                "learner_id": learner_id, "weak_areas": report.get("weak_areas", []),
                "recommended_scenarios": report.get("recommended_scenarios", []),
                "dimensions": progress.get("dimensions", {}),
                "total_sessions": progress.get("total_sessions", 0),
            })

        if payload.action != "roleplay":
            raise HTTPException(400, "未知 action")

        if payload.session_id:
            with sessions_lock:
                entry = sessions.get(payload.session_id)
            if not entry or time.monotonic() - entry["created_at"] > 7200:
                raise HTTPException(404, "训练会话不存在或已过期")
            if payload.learner_id and payload.learner_id != entry["learner_id"]:
                raise HTTPException(403, "训练会话学员不匹配")
            with entry["lock"]:
                engine = entry["engine"]
                reply, done = engine.respond(payload.customer_message or "你好")
                result = {"customer_reply": reply, "is_finished": done,
                          "result": engine._current_session.result, "session_id": payload.session_id}
                if done:
                    result["score"] = _score_payload(engine.finish_session())
                    with sessions_lock:
                        sessions.pop(payload.session_id, None)
                return SkillOutput(action="roleplay", result=result)

        with sessions_lock:
            expired = [sid for sid, entry in sessions.items() if time.monotonic() - entry["created_at"] > 7200]
            for sid in expired:
                del sessions[sid]
            if len(sessions) >= 1000:
                raise HTTPException(503, "训练会话容量已满")
        engine = SkillEngine()
        try:
            opening = engine.start_roleplay(payload.scenario_id or "cold_visit_001", learner_id)
        except ValueError:
            raise HTTPException(400, "训练场景不存在") from None
        sid = uuid.uuid4().hex
        with sessions_lock:
            sessions[sid] = {"engine": engine, "learner_id": learner_id,
                             "created_at": time.monotonic(), "lock": threading.Lock()}
        return SkillOutput(action="roleplay", result={
            "customer_reply": opening, "is_finished": False, "result": None, "session_id": sid,
        })

    @app.get("/chat", include_in_schema=False)
    def customer_page():
        return FileResponse(Path(__file__).resolve().parent.parent / "web" / "customer.html",
                            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                                     "Referrer-Policy": "no-referrer",
                                     "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})

    @app.get("/static/customer.js", include_in_schema=False)
    def customer_script():
        return FileResponse(Path(__file__).resolve().parent.parent / "web" / "customer.js",
                            media_type="application/javascript", headers={"X-Content-Type-Options": "nosniff"})

    app.include_router(build_router(service, store, gateway))
    return app


app = create_app()
