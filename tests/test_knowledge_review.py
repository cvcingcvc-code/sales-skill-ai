"""Knowledge approval, legacy migration and API permissions with isolated SQLite."""

from datetime import datetime
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from agent_skill.api_server import create_app
from customer_service import CustomerService, Store, SupportError


class TestKnowledgeReview(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "support.db"
        self.store = Store(self.path)
        self.store.upsert_product("p1", name="合成资料包", description="PDF 格式。", price_cents=1299)
        self.store.upsert_product("p2", name="其他合成资料包", description="表格格式。", price_cents=2999)
        env = patch.dict(os.environ, {"ADMIN_API_TOKEN": "offline-review-admin", "MODEL_ENABLED": "false"})
        env.start()
        self.addCleanup(env.stop)
        self.client = TestClient(create_app(store=self.store))
        self.addCleanup(self.client.close)
        self.admin = {"Authorization": "Bearer offline-review-admin"}
        self.fields = {"id": "k1", "title": "文件兼容说明", "content": "软件兼容问题请人工确认。",
                       "kind": "faq", "product_id": "p1", "active": True}

    def document(self, **changes):
        return self.store.upsert_knowledge(**{**self.fields, **changes})

    def approve(self, document):
        return self.store.approve_knowledge(document["id"], expected_updated_at=document["updated_at"])

    def test_new_knowledge_is_visible_to_admin_but_excluded_from_retrieval(self):
        document = self.document()
        self.assertEqual(document["review_status"], "draft")
        self.assertIsNone(document["reviewed_at"])
        self.assertEqual(self.store.list_knowledge()[0]["id"], "k1")
        self.assertEqual(self.store.list_knowledge(active_only=True), [])
        self.assertEqual(CustomerService(self.store)._retrieve("合成资料包文件兼容说明", [])[1], [])

    def test_approval_persists_and_makes_enabled_knowledge_retrievable(self):
        original = self.document()
        approved = self.approve(original)
        self.assertEqual(approved["review_status"], "approved")
        self.assertIsNotNone(datetime.fromisoformat(approved["reviewed_at"]).tzinfo)
        for key in self.fields:
            self.assertEqual(approved[key], original[key])
        self.assertEqual(approved["updated_at"], original["updated_at"])
        self.assertEqual(Store(self.path).list_knowledge(active_only=True), [approved])
        self.assertEqual(CustomerService(self.store)._retrieve("合成资料包文件兼容说明", [])[1][0]["id"], "k1")

    def test_material_edits_reset_approval_and_remove_knowledge_from_candidates(self):
        for change in ({"title": "新版文件说明"}, {"content": "需要核对的新版内容。"},
                       {"kind": "policy"}, {"product_id": "p2"}, {"product_id": None}):
            with self.subTest(change=change):
                self.approve(self.document())
                edited = self.document(**change)
                self.assertEqual(edited["review_status"], "draft")
                self.assertIsNone(edited["reviewed_at"])
                self.assertEqual(self.store.list_knowledge(active_only=True), [])

    def test_noop_save_and_repeated_approval_preserve_review_time(self):
        approved = self.approve(self.document())
        repeated = self.approve(approved)
        saved = self.document()
        self.assertEqual(repeated["reviewed_at"], approved["reviewed_at"])
        self.assertEqual(saved["review_status"], "approved")
        self.assertEqual(saved["reviewed_at"], approved["reviewed_at"])

    def test_disabled_knowledge_keeps_approval_but_cannot_be_retrieved(self):
        approved = self.approve(self.document())
        disabled = self.document(active=False)
        self.assertEqual(disabled["review_status"], "approved")
        self.assertEqual(disabled["reviewed_at"], approved["reviewed_at"])
        self.assertEqual(self.store.list_knowledge(active_only=True), [])
        self.assertEqual(self.document()["review_status"], "approved")
        self.assertEqual(len(self.store.list_knowledge(active_only=True)), 1)

    def test_approving_disabled_or_unpublished_product_knowledge_does_not_publish_it(self):
        self.approve(self.document(active=False))
        self.assertEqual(self.store.list_knowledge(active_only=True), [])
        self.approve(self.document())
        self.store.upsert_product("p1", name="合成资料包", description="PDF 格式。", price_cents=1299, active=False)
        self.assertEqual(self.store.list_knowledge(active_only=True), [])

    def test_stale_approval_cannot_approve_changed_content(self):
        old = self.document()
        current = self.document(content="尚未审核的新内容。")
        with self.assertRaises(SupportError) as caught:
            self.approve(old)
        self.assertEqual(caught.exception.code, "knowledge_changed")
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(self.store.list_knowledge(), [current])

    def test_missing_or_invalid_version_does_not_change_approval(self):
        self.document()
        with self.assertRaises(SupportError) as caught:
            self.store.approve_knowledge("missing", expected_updated_at="old-version")
        self.assertEqual(caught.exception.status_code, 404)
        for version in (None, "", "x" * 65):
            with self.subTest(version=version), self.assertRaises(SupportError):
                self.store.approve_knowledge("k1", expected_updated_at=version)
        self.assertEqual(self.store.list_knowledge()[0]["review_status"], "draft")

    def test_old_database_preserves_content_as_draft_and_migration_is_repeatable(self):
        legacy_path = self.path.with_name("legacy.db")
        with sqlite3.connect(legacy_path) as conn:
            conn.execute("""CREATE TABLE knowledge(id TEXT PRIMARY KEY,title TEXT NOT NULL,content TEXT NOT NULL,
                kind TEXT NOT NULL,product_id TEXT,active INTEGER NOT NULL,updated_at TEXT NOT NULL)""")
            conn.execute("INSERT INTO knowledge VALUES(?,?,?,?,?,?,?)",
                         ("legacy", "旧版合成资料", "原始内容保留。", "policy", None, 1, "2026-01-01T00:00:00+00:00"))
        migrated = Store(legacy_path)
        document = migrated.list_knowledge()[0]
        self.assertEqual(document["content"], "原始内容保留。")
        self.assertEqual(document["updated_at"], "2026-01-01T00:00:00+00:00")
        self.assertTrue(document["active"])
        self.assertEqual(document["review_status"], "draft")
        self.assertEqual(migrated.list_knowledge(active_only=True), [])
        self.assertEqual(Store(legacy_path).list_knowledge(), [document])
        approved = migrated.approve_knowledge("legacy", expected_updated_at=document["updated_at"])
        self.assertEqual(Store(legacy_path).list_knowledge(active_only=True), [approved])

    def test_approval_endpoint_requires_admin_even_with_a_visitor_token(self):
        document = self.document()
        session = self.client.post("/support/conversations").json()
        for headers in ({}, {"Authorization": "Bearer wrong-offline-token"},
                        {"X-Session-Token": session["session_token"]}):
            response = self.client.post("/admin/knowledge/k1/approve", headers=headers,
                                        json={"confirm": True, "expected_updated_at": document["updated_at"]})
            self.assertEqual(response.status_code, 401)
        self.assertEqual(self.store.list_knowledge()[0]["review_status"], "draft")

    def test_approval_api_requires_confirmation_and_the_current_version(self):
        document = self.document()
        for body in ({}, {"confirm": False, "expected_updated_at": document["updated_at"]}, {"confirm": True}):
            self.assertEqual(self.client.post("/admin/knowledge/k1/approve", headers=self.admin, json=body).status_code, 422)
        changed = self.document(content="新版本必须重新核对。")
        stale = self.client.post("/admin/knowledge/k1/approve", headers=self.admin,
                                 json={"confirm": True, "expected_updated_at": document["updated_at"]})
        self.assertEqual(stale.status_code, 409)
        approved = self.client.post("/admin/knowledge/k1/approve", headers=self.admin,
                                    json={"confirm": True, "expected_updated_at": changed["updated_at"]})
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(approved.json()["review_status"], "approved")

    def test_ordinary_save_cannot_forge_review_metadata(self):
        for forged in ({"review_status": "approved"}, {"reviewed_at": "2026-01-01"}):
            response = self.client.post("/admin/knowledge", headers=self.admin, json={**self.fields, **forged})
            self.assertEqual(response.status_code, 422)
        saved = self.client.post("/admin/knowledge", headers=self.admin, json=self.fields)
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["review_status"], "draft")
