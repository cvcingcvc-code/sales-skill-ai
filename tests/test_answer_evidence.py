"""Historical answer evidence using explicit offline selections and isolated SQLite.

No test in this module is a real model acceptance test. AppTest uses in-process
FastAPI HTTP transport, separately from the live browser startup checks.
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

from agent_skill.api_server import create_app
from customer_service import CustomerService, Store, SupportError
from core.model_gateway import ModelError
from tests.support_fixtures import reviewed_knowledge
from tests.test_customer_service import MockGateway


class TestAnswerEvidence(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "support.db"
        environment = patch.dict(os.environ, {
            "ADMIN_API_TOKEN": "offline-evidence-admin", "MODEL_ENABLED": "false",
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.store = Store(self.path)
        self.product = self.store.upsert_product(
            "p1", name="合成数字资料包", description="PDF 格式，仅供个人学习。", price_cents=1299,
            url="https://example.invalid/p1")
        self.knowledge = reviewed_knowledge(
            self.store, "k1", title="资料包使用说明", content="文件无法打开时请联系人工核对。",
            kind="faq", product_id="p1")
        self.gateway = MockGateway()
        self.gateway.selection["knowledge_ids"] = ["k1"]
        self.service = CustomerService(self.store, self.gateway)
        self.session = self.store.create_conversation()
        self.cid = self.session["id"]
        self.client = TestClient(create_app(store=self.store, gateway=self.gateway))
        self.addCleanup(self.client.close)
        self.admin = {"Authorization": "Bearer offline-evidence-admin"}
        self.visitor = {"X-Session-Token": self.session["session_token"]}

    def answer(self):
        return self.service.chat(self.cid, self.session["session_token"], "合成数字资料包的价格和使用说明？")

    def messages(self, store=None):
        return (store or self.store).admin_get_conversation(self.cid)["messages"]

    def evidence(self):
        return {"products": [self.product], "citations": [self.knowledge]}

    def test_reply_and_exact_rendered_evidence_survive_reopen(self):
        reply = self.answer()
        message = self.messages(Store(self.path))[-1]
        self.assertEqual(message["content"], reply["reply"])
        self.assertEqual(message["source"], "mock_selection")
        self.assertEqual(reply["model_status"], "MOCK")
        self.assertEqual(message["evidence"], {
            "version": 1, "products": reply["products"], "citations": reply["citations"],
        })
        self.assertEqual(message["evidence"]["citations"][0]["review_status"], "approved")
        self.assertEqual(message["evidence"]["citations"][0]["reviewed_at"], self.knowledge["reviewed_at"])
        self.assertNotIn("evidence_json", message)
        self.assertIsNone(self.messages()[0]["evidence"])

    def test_catalog_edits_do_not_rewrite_prior_answer_evidence(self):
        self.answer()
        before = self.messages()[-1]
        self.store.upsert_product("p1", name=self.product["name"], description="新版合成商品描述。", price_cents=2999)
        reviewed_knowledge(self.store, "k1", title=self.knowledge["title"], content="新版说明需要人工核对兼容条件。",
                           kind="faq", product_id="p1")
        self.answer()
        after = self.messages(Store(self.path))
        self.assertEqual(after[1], before)
        self.assertEqual(after[-1]["evidence"]["products"][0]["price_cents"], 2999)
        self.assertEqual(after[1]["evidence"]["products"][0]["price_cents"], 1299)
        self.assertNotEqual(after[1]["evidence"]["citations"], after[-1]["evidence"]["citations"])
        self.store.upsert_product("p1", name=self.product["name"], description="下架资料。", price_cents=2999, active=False)
        self.assertEqual(self.messages(Store(self.path))[1], before)

    def test_only_selected_evidence_is_saved_and_policy_only_retains_product_label(self):
        self.gateway.selection["product_ids"] = []
        result = self.answer()
        evidence = self.messages()[-1]["evidence"]
        self.assertEqual(evidence["products"], [])
        self.assertEqual(evidence["citations"], result["citations"])
        self.assertEqual(evidence["citations"][0]["product_name"], self.product["name"])
        self.gateway.selection = {"product_ids": ["p1"], "knowledge_ids": [], "needs_human": False}
        self.answer()
        self.assertEqual(self.messages()[-1]["evidence"]["citations"], [])

    def test_rules_disabled_handoff_and_human_reply_have_no_model_evidence(self):
        greeting = self.service.chat(self.cid, self.session["session_token"], "你好")
        self.assertEqual(greeting["source"], "rule")
        self.gateway.error = ModelError("disabled")
        result = self.answer()
        self.assertEqual(result["model_status"], "DISABLED")
        self.store.resolve_handoff(result["handoff"]["id"], "合成的人工答复。")
        self.assertTrue(all(message["evidence"] is None for message in self.messages()))
        self.assertEqual(self.messages()[-1]["source"], "human")

    def test_late_reply_saves_neither_content_nor_evidence(self):
        original = self.store.add_ai_message_if_active

        def takeover(cid, content, source, expected_epoch=None, **kwargs):
            Store(self.path).create_handoff(cid, "customer_request")
            return original(cid, content, source, expected_epoch=expected_epoch, **kwargs)

        with patch.object(self.store, "add_ai_message_if_active", side_effect=takeover):
            result = self.answer()
        self.assertEqual(result["source"], "human_handoff")
        self.assertTrue(all(message["evidence"] is None for message in self.messages()))
        self.assertFalse(any(message["source"] == "mock_selection" for message in self.messages()))
        self.store.resolve_handoff(result["handoff"]["id"], "合成人工确认。")
        self.store.resume_ai(self.cid)
        self.assertFalse(original(self.cid, "过期回复", "mock_selection", expected_epoch=0, evidence=self.evidence()))
        self.assertFalse(any(message["content"] == "过期回复" for message in self.messages()))

    def test_transaction_failure_rolls_back_answer_and_evidence(self):
        with patch.object(self.store, "_touch", side_effect=RuntimeError("offline rollback test")):
            with self.assertRaises(RuntimeError):
                self.store.add_ai_message_if_active(self.cid, "未完成回复", "mock_selection", evidence=self.evidence())
        self.assertEqual(self.messages(Store(self.path)), [])

    def test_legacy_message_migration_does_not_invent_or_rewrite_evidence(self):
        path = self.path.with_name("legacy.db")
        with sqlite3.connect(path) as connection:
            connection.execute("""CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
                source TEXT NOT NULL, created_at TEXT NOT NULL)""")
            connection.execute("INSERT INTO messages VALUES(1,?,?,?,?,?)",
                               ("legacy", "assistant", "合成历史正文", "managed_catalog", "2020-01-01T00:00:00+00:00"))
        for _ in range(2):
            store = Store(path)
            with store.connection() as connection:
                row = dict(connection.execute("SELECT * FROM messages WHERE id=1").fetchone())
            self.assertEqual(row["content"], "合成历史正文")
            self.assertEqual(row["source"], "managed_catalog")
            self.assertEqual(row["created_at"], "2020-01-01T00:00:00+00:00")
            self.assertIsNone(row["evidence_json"])
        new = store.create_conversation()
        self.assertTrue(store.add_ai_message_if_active(new["id"], "合成新回复", "mock_selection", evidence=self.evidence()))
        self.assertEqual(store.admin_get_conversation(new["id"])["messages"][-1]["evidence"]["products"], [self.product])

    def test_evidence_drops_unrelated_metadata_and_refuses_rule_source(self):
        evidence = self.evidence()
        evidence["products"] = [{**self.product, "internal_note": "not-for-snapshot"}]
        evidence["prompt"] = "not-for-snapshot"
        self.store.add_ai_message_if_active(self.cid, "合成回复", "mock_selection", evidence=evidence)
        self.assertNotIn("not-for-snapshot", json.dumps(self.messages()))
        with self.assertRaises(SupportError):
            self.store.add_ai_message_if_active(self.cid, "规则回复", "rule", evidence=evidence)
        self.assertEqual(len(self.messages()), 1)

    def test_admin_snapshot_requires_admin_and_visitor_history_keeps_existing_fields(self):
        response = self.client.post(f"/support/conversations/{self.cid}/messages", headers=self.visitor,
                                    json={"message": "合成数字资料包的价格和使用说明？"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["model_status"], "MOCK")
        path = f"/admin/conversations/{self.cid}"
        for headers in ({}, self.visitor, {"Authorization": "Bearer incorrect"},
                        {"Authorization": "Bearer " + self.session["session_token"]}):
            with self.subTest(headers_kind=list(headers)):
                self.assertEqual(self.client.get(path, headers=headers).status_code, 401)
        admin = self.client.get(path, headers=self.admin)
        self.assertEqual(admin.status_code, 200)
        self.assertIsNotNone(admin.json()["messages"][-1]["evidence"])
        visitor = self.client.get(f"/support/conversations/{self.cid}", headers=self.visitor)
        self.assertEqual(visitor.status_code, 200)
        for message in visitor.json()["messages"]:
            self.assertEqual(set(message), {"id", "role", "content", "source", "created_at"})
        other = self.client.post("/support/conversations").json()
        self.assertEqual(self.client.get(f"/support/conversations/{self.cid}",
                         headers={"X-Session-Token": other["session_token"]}).status_code, 403)
        for field in ("evidence", "evidence_json", "source"):
            forged = self.client.post(f"/support/conversations/{self.cid}/messages", headers=self.visitor,
                                      json={"message": "你好", field: "forged"})
            self.assertEqual(forged.status_code, 422)

    def test_visitor_and_admin_deletion_remove_snapshot_rows(self):
        for actor in ("visitor", "admin"):
            with self.subTest(actor=actor):
                session = self.client.post("/support/conversations").json()
                self.store.add_ai_message_if_active(session["id"], "合成回复", "mock_selection", evidence=self.evidence())
                path = f"/{'support' if actor == 'visitor' else 'admin'}/conversations/{session['id']}"
                headers = {"X-Session-Token": session["session_token"]} if actor == "visitor" else self.admin
                response = self.client.request("DELETE", path, headers=headers, json={"confirm": True})
                self.assertEqual(response.status_code, 200, response.text)
                with self.store.connection() as connection:
                    count = connection.execute("SELECT COUNT(*) FROM messages WHERE conversation_id=?", (session["id"],)).fetchone()[0]
                self.assertEqual(count, 0)
        self.assertNotIn(self.knowledge["content"], json.dumps(self.store.list_audit_events(), ensure_ascii=False))

    def test_retention_preview_keeps_snapshot_and_confirmed_purge_removes_it(self):
        self.answer()
        self.store.close_conversation(self.cid)
        with self.store.connection() as connection:
            connection.execute("UPDATE conversations SET last_activity_at=? WHERE id=?",
                               ("2020-01-01T00:00:00+00:00", self.cid))
        now = datetime(2026, 10, 26, tzinfo=timezone.utc)
        self.assertEqual(self.store.purge_expired(30, now=now)["eligible_count"], 1)
        self.assertIsNotNone(self.messages()[-1]["evidence"])
        self.assertEqual(self.store.purge_expired(30, dry_run=False, now=now)["deleted_count"], 1)
        with self.store.connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)

    def app(self, authorized=True):
        def request(method, url, **kwargs):
            response = self.client.request(method, httpx.URL(url).path,
                                           json=kwargs.get("json"), headers=kwargs.get("headers"))
            return httpx.Response(response.status_code, content=response.content, headers=response.headers,
                                  request=httpx.Request(method, url))

        with patch("demo.support_admin.httpx.request", side_effect=request):
            app = AppTest.from_string(
                "from demo.support_admin import SupportAPI, _conversation_history\n"
                f"_conversation_history(SupportAPI('http://testserver', 'offline-evidence-admin'), {authorized})"
            ).run()
        self.assertFalse(app.exception, app.exception)
        return app

    def test_admin_history_displays_old_facts_and_mock_warning(self):
        self.answer()
        self.store.upsert_product("p1", name="新版资料包", description="新版描述", price_cents=2999)
        app = self.app()
        self.assertEqual(len(app.expander), 1)
        self.assertTrue(any(self.product["name"] in item.value for item in app.text))
        self.assertTrue(any(self.knowledge["content"] == item.value for item in app.text))
        self.assertEqual(app.dataframe[1].value.iloc[0]["price_cents"], 1299)
        self.assertTrue(any("MOCK" in item.value for item in app.warning))
        self.assertTrue(any("不代表当前价格" in item.value for item in app.caption))

    def test_admin_history_marks_legacy_evidence_missing_without_inventing_it(self):
        self.store.add_message(self.cid, "assistant", "旧的合成答复", "managed_catalog")
        app = self.app()
        self.assertFalse(app.expander)
        self.assertTrue(any("未保存资料快照" in item.value for item in app.caption))

    def test_unauthorized_workbench_does_not_display_snapshots(self):
        self.answer()
        app = self.app(authorized=False)
        self.assertFalse(app.expander)
        self.assertFalse(app.dataframe)
        self.assertFalse(app.text)
