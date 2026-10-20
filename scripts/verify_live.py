#!/usr/bin/env python3
"""Real provider + synthetic support acceptance; never substitutes a mock.

Run in a fresh process after securely configuring credentials:
    python scripts/verify_live.py --provider deepseek --output /tmp/live-acceptance.json
Local model, after explicit activation (no API key required):
    python scripts/verify_live.py --provider ollama --output /tmp/local-acceptance.json

MODEL_ENABLED=false reports BLOCKED without contacting any model service.

Exit codes: 0 PASS, 1 FAIL, 2 BLOCKED (configuration/network/authentication).
Only synthetic catalog/customer data is used. The temporary SQLite file is deleted.
"""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

from core.model_gateway import ModelGateway
from customer_service.service import CustomerService
from customer_service.store import Store


BLOCKED_CODES = {
    "missing_key", "configuration", "authentication", "timeout", "unavailable",
    "rate_limited", "unknown_provider", "disabled",
}


def verify(provider=None):
    """Make one genuine request only when the selected provider is configured."""
    report = {
        "status": "BLOCKED",
        "provider": provider,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "synthetic_data_only": True,
        "checks": {
            "provider_configuration": "NOT_RUN", "real_model_request": "NOT_RUN",
            "managed_product_reply": "NOT_RUN", "consented_lead": "NOT_RUN",
            "human_handoff_reply": "NOT_RUN",
        },
    }
    gateway = ModelGateway(provider=provider)
    state = gateway.status()
    provider = gateway.config.provider
    report["provider"] = provider
    if state.get("status") == "DISABLED" or not state.get("configured"):
        report["checks"]["provider_configuration"] = "BLOCKED"
        report["checks"]["real_model_request"] = "BLOCKED"
        report["reason"] = state.get("code") if state.get("code") in BLOCKED_CODES else "configuration"
        return report, 2
    report["checks"]["provider_configuration"] = "PASS"
    # Exclude paths, query strings and credentials from the target description.
    model, host = gateway.config.model, urlsplit(gateway.config.base_url).hostname or ""
    secret = gateway.config.api_key
    if secret:
        model, host = model.replace(secret, "[redacted]"), host.replace(secret, "[redacted]")
    report["target"] = {"host": host, "model": model}

    with tempfile.TemporaryDirectory(prefix="sales-support-live-") as directory:
        store = Store(str(Path(directory) / "synthetic-support.db"))
        product = store.upsert_product(
            id="synthetic-acceptance-cup", name="验收专用合成保温杯",
            description="仅用于验收的合成商品，500ml，非真实可购买商品。",
            price_cents=12345, url="https://example.invalid/synthetic-acceptance-cup",
        )
        store.upsert_knowledge(
            id="synthetic-acceptance-policy", title="验收专用合成保温杯售后资料",
            content="这是合成验收资料；实际售后由人工确认。", kind="policy", product_id=product["id"],
        )
        service = CustomerService(store=store, gateway=gateway)
        conversation = service.create_conversation()
        cid, token = conversation["id"], conversation["session_token"]
        answer = service.chat(cid, token, "请介绍验收专用合成保温杯，价格是多少？")
        result = gateway.last_result
        if result is None:
            code = getattr(gateway.last_error, "code", None) or answer.get("error_code")
            blocked = code in BLOCKED_CODES
            report["status"] = "BLOCKED" if blocked else "FAIL"
            report["checks"]["real_model_request"] = report["status"]
            report["reason"] = code if code in BLOCKED_CODES | {"invalid_response", "invalid_request"} else "model_response_invalid"
            return report, 2 if blocked else 1
        if result.provider != provider or gateway.status().get("status") != "AVAILABLE":
            report.update(status="FAIL", reason="real_provider_not_verified")
            report["checks"]["real_model_request"] = "FAIL"
            return report, 1
        report["checks"]["real_model_request"] = "PASS"
        report["usage"] = {
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "estimated_cost": result.estimated_cost,
            "cost_note": "配置单价的估算值；null 表示未配置单价或未返回用量。",
        }
        if (answer.get("source") != "managed_catalog" or answer.get("model_status") != "OK"
                or product["id"] not in {p["id"] for p in answer.get("products", [])}
                or "123.45" not in answer.get("reply", "")):
            report.update(status="FAIL", reason="managed_product_reply_not_verified")
            report["checks"]["managed_product_reply"] = "FAIL"
            return report, 1
        report["checks"]["managed_product_reply"] = "PASS"

        service.save_lead(cid, token, consent=True, name="合成验收用户",
                          contact="acceptance@example.invalid", need="合成商品验收咨询")
        leads = store.list_leads()
        if len(leads) != 1 or not leads[0].get("consent_at"):
            report.update(status="FAIL", reason="consented_lead_not_verified")
            report["checks"]["consented_lead"] = "FAIL"
            return report, 1
        report["checks"]["consented_lead"] = "PASS"
        handoff = service.request_handoff(cid, token, "合成验收请求人工协助")
        human_reply = "此为合成验收人工答复，未执行任何交易。"
        resolved = store.resolve_handoff(handoff["id"], human_reply)
        history = service.get_conversation(cid, token)
        if (resolved.get("status") != "resolved" or store.list_handoffs(status="pending")
                or not any(m["role"] == "human" and m["content"] == human_reply for m in history["messages"])):
            report.update(status="FAIL", reason="human_reply_not_verified")
            report["checks"]["human_handoff_reply"] = "FAIL"
            return report, 1
        report["checks"]["human_handoff_reply"] = "PASS"
        report.update(status="PASS", reason="real_provider_and_synthetic_support_flow_verified")
        return report, 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("deepseek", "ollama"), default=None,
                        help="Defaults to LLM_PROVIDER; never switches to another provider after failure.")
    parser.add_argument("--output", type=Path, help="Write the same sanitized JSON report to this file.")
    args = parser.parse_args(argv)
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    try:
        report, code = verify(args.provider)
    except Exception:
        # No tracebacks or raw upstream exceptions: they can contain headers/body.
        report, code = {"status": "FAIL", "reason": "acceptance_internal_error", "synthetic_data_only": True}, 1
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        try:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(payload + "\n", encoding="utf-8")
        except OSError:
            print(json.dumps({"status": "FAIL", "reason": "report_write_failed"}))
            return 1
    print(payload)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
