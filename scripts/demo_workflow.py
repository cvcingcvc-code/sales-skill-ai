#!/usr/bin/env python3
"""Isolated synthetic API demo. --mock is explicitly labelled, never a live PASS."""

import argparse
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
from fastapi.testclient import TestClient

from agent_skill.api_server import create_app
from core.model_gateway import ModelGateway, ModelResult
from customer_service.store import Store


class ExplicitMockSelector:
    def chat(self, messages, system="", model=None):
        data = json.loads(messages[-1]["content"])
        return ModelResult(json.dumps({
            "product_ids": [p["id"] for p in data["untrusted_managed_products"]],
            "knowledge_ids": [k["id"] for k in data["untrusted_managed_knowledge"]],
            "needs_human": False,
        }), "mock", "explicit-demo-selector")

    def status(self):
        return {"provider": "mock", "status": "MOCK", "configured": True, "available": None}


def run(mock=False):
    load_dotenv(override=False)
    # Generated only for this local in-process demo, never printed or persisted.
    previous = os.environ.get("ADMIN_API_TOKEN")
    os.environ["ADMIN_API_TOKEN"] = secrets.token_urlsafe(32)
    admin = {"Authorization": "Bearer " + os.environ["ADMIN_API_TOKEN"]}
    try:
        with tempfile.TemporaryDirectory(prefix="sales-v1-demo-") as directory:
            store = Store(str(Path(directory) / "support.db"))
            gateway = ExplicitMockSelector() if mock else ModelGateway()
            with TestClient(create_app(store=store, gateway=gateway)) as client:
                product = client.post("/admin/products", headers=admin, json={
                    "id": "synthetic-demo-cup", "name": "合成演示保温杯",
                    "description": "仅供演示的合成商品，500ml，不可真实购买。",
                    "price_cents": 12345, "url": "https://example.invalid/demo-cup",
                })
                assert product.status_code == 200
                knowledge = client.post("/admin/knowledge", headers=admin, json={
                    "id": "synthetic-demo-policy", "title": "合成演示保温杯售后",
                    "content": "这是演示资料；售后申请由人工核实。", "kind": "policy",
                    "product_id": "synthetic-demo-cup",
                })
                assert knowledge.status_code == 200
                catalog = client.get("/support/products").json()
                assert len(catalog) == 1
                conversation = client.post("/support/conversations").json()
                cid = conversation["id"]
                visitor = {"X-Session-Token": conversation["session_token"]}
                answer = client.post(f"/support/conversations/{cid}/messages", headers=visitor,
                                     json={"message": "介绍合成演示保温杯，价格是多少？"}).json()
                lead = client.post(f"/support/conversations/{cid}/leads", headers=visitor, json={
                    "consent": True, "name": "合成演示用户", "contact": "demo@example.invalid",
                    "need": "仅用于验证演示商品咨询链路",
                })
                assert lead.status_code == 201
                handoff = client.post(f"/support/conversations/{cid}/handoff", headers=visitor,
                                      json={"reason": "customer_request"}).json()
                handled = client.post(f"/admin/handoffs/{handoff['id']}/resolve", headers=admin,
                                      json={"reply": "已收到合成演示需求，由人工核实处理；未执行交易。"})
                assert handled.status_code == 200
                history = client.get(f"/support/conversations/{cid}", headers=visitor).json()
                assert any(m["role"] == "human" for m in history["messages"])
                assert history["need_summary"]
                assert len(client.get("/admin/leads", headers=admin).json()) == 1
                status = answer["model_status"]
                report = {
                    "status": "MOCK_DEMO_COMPLETE" if mock else ("BLOCKED" if status == "BLOCKED" else "COMPLETE"),
                    "synthetic_data_only": True, "model_status": status,
                    "checks": {"catalog": "PASS", "conversation_record": "PASS", "need_record": "PASS",
                               "explicit_consent_lead": "PASS", "human_handoff_and_reply": "PASS"},
                    "customer_reply": answer["reply"],
                    "note": "Mock 演示不是真实模型验收。" if mock else "模型缺失或失败不会伪造成功；独立人工链路仍可验证。",
                }
                return report, 2 if status == "BLOCKED" else 0
    finally:
        if previous is None:
            os.environ.pop("ADMIN_API_TOKEN", None)
        else:
            os.environ["ADMIN_API_TOKEN"] = previous


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mock", action="store_true", help="Explicit offline selector; output is marked MOCK.")
    args = parser.parse_args()
    result, code = run(mock=args.mock)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(code)
