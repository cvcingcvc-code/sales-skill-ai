"""Offline V2 persistence, retention, privacy and multi-connection regressions."""

import hashlib
import json
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from customer_service.store import Store, SupportError


class DataLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "support.db")
        self.store = Store(self.path)
        self.session = self.store.create_conversation()
        self.cid = self.session["id"]

    def tearDown(self):
        self.tmp.cleanup()

    def assert_error(self, code, callback):
        with self.assertRaises(SupportError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)

    def lead(self, cid=None):
        return self.store.save_lead(cid or self.cid, consent=True, name="Test Person",
            contact="private@example.test", need="Private need", consent_version="v2")

    def populated(self):
        self.store.add_message(self.cid, "user", "Private message", "customer")
        handoff = self.store.create_handoff(self.cid, "customer_request")
        self.lead()
        self.store.record_usage(self.cid, "mock", "offline", "MOCK", 2, 3, 0.01)
        return handoff

    def set_age(self, cid, stamp):
        with self.store.connection() as conn:
            conn.execute("UPDATE conversations SET created_at=?,updated_at=?,last_activity_at=? WHERE id=?",
                         (stamp, stamp, stamp, cid))

    def test_create_and_all_snapshot_paths_expose_epoch_and_timestamps(self):
        for result in (self.session, self.store.get_admin_conversation(self.cid),
                       self.store.get_conversation(self.cid, self.session["session_token"]),
                       self.store.list_conversations()[0]):
            self.assertEqual(result["ai_epoch"], 0)
            for key in ("created_at", "updated_at", "last_activity_at"):
                self.assertIsNotNone(datetime.fromisoformat(result[key]).tzinfo)
            self.assertNotIn("token_hash", result)

    def test_catalog_conversation_messages_and_handoff_survive_store_reopen(self):
        product = self.store.upsert_product(
            id="restart-demo-item", name="合成验收商品",
            description="仅用于验证 SQLite 文件重开后的记录持久化。", price_cents=499)
        knowledge = self.store.upsert_knowledge(
            id="restart-demo-faq", title="合成配送 FAQ",
            content="配送时间由人工确认。", kind="faq", product_id=product["id"])
        self.store.add_message(self.cid, "user", "什么时候能收到？", "customer")
        handoff = self.store.create_handoff(self.cid, "knowledge_gap")
        self.store.resolve_handoff(handoff["id"], "此为合成验收人工回复。")

        reopened = Store(self.path)
        conversation = reopened.admin_get_conversation(self.cid)
        self.assertEqual(reopened.get_product(product["id"])["price_cents"], 499)
        self.assertEqual(reopened.list_knowledge()[0]["id"], knowledge["id"])
        self.assertEqual([message["role"] for message in conversation["messages"]], ["user", "human"])
        self.assertEqual(conversation["handoffs"][0]["status"], "resolved")

    def test_database_journal_mode_persists_and_foreign_keys_are_enabled_per_connection(self):
        for _ in range(2):
            with self.store.connection() as conn:
                self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
                self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)

    def test_every_message_writer_refreshes_last_activity(self):
        old = "2000-01-01T00:00:00+00:00"
        self.set_age(self.cid, old)
        self.store.add_message(self.cid, "user", "Question", "customer")
        self.assertGreater(self.store.get_admin_conversation(self.cid)["last_activity_at"], old)
        self.set_age(self.cid, old)
        self.assertTrue(self.store.add_ai_message_if_active(self.cid, "Answer", "mock", 0))
        self.assertGreater(self.store.get_admin_conversation(self.cid)["last_activity_at"], old)
        handoff = self.store.create_handoff(self.cid, "help")
        self.set_age(self.cid, old)
        self.store.resolve_handoff(handoff["id"], "Human answer")
        self.assertGreater(self.store.get_admin_conversation(self.cid)["last_activity_at"], old)

    def test_close_invalidates_epoch_and_pending_handoffs(self):
        handoff = self.store.create_handoff(self.cid, "help")
        closed = self.store.close_conversation(self.cid, actor="visitor")
        self.assertEqual(closed["status"], "closed")
        self.assertEqual(closed["ai_epoch"], 2)
        self.assertEqual(closed["handoffs"][0]["status"], "closed")
        self.assertIsNotNone(closed["handoffs"][0]["resolved_at"])
        self.assert_error("conversation_closed", lambda: self.store.resolve_handoff(handoff["id"], "Late human"))
        self.assertEqual(self.store.close_conversation(self.cid)["ai_epoch"], 2)

    def test_closed_rejects_customer_writes(self):
        self.store.close_conversation(self.cid)
        for callback in (
            lambda: self.store.add_message(self.cid, "user", "Late", "customer"),
            lambda: self.store.add_message(self.cid, "human", "Late", "human"),
            lambda: self.store.create_handoff(self.cid, "Late"),
            lambda: self.store.update_need_summary(self.cid, "Late"),
            lambda: self.lead(),
            lambda: self.store.resume_ai(self.cid),
        ):
            self.assert_error("conversation_closed", callback)
        self.assertFalse(self.store.add_ai_message_if_active(self.cid, "Late AI", "mock", 0))
        self.assertEqual(self.store.get_admin_conversation(self.cid)["messages"], [])

    def test_resume_requires_resolved_human_queue_and_is_explicit(self):
        self.assert_error("invalid_state", lambda: self.store.resume_ai(self.cid))
        handoff = self.store.create_handoff(self.cid, "help")
        self.assert_error("handoff_pending", lambda: self.store.resume_ai(self.cid))
        self.store.resolve_handoff(handoff["id"], "Resolved")
        self.assertEqual(self.store.get_admin_conversation(self.cid)["status"], "human")
        result = self.store.resume_ai(self.cid)
        self.assertEqual((result["status"], result["ai_epoch"]), ("ai", 3))
        self.assertTrue(self.store.add_ai_message_if_active(self.cid, "New AI", "mock", 3))

    def test_resolution_invalidates_pending_snapshot_before_late_handoff_write(self):
        handoff = self.store.create_handoff(self.cid, "help")
        pending_epoch = handoff["conversation_epoch"]
        self.store.resolve_handoff(handoff["id"], "Resolved")
        self.assertEqual(self.store.get_admin_conversation(self.cid)["ai_epoch"], pending_epoch + 1)
        self.assert_error("conversation_changed", lambda: self.store.create_handoff(
            self.cid, "Stale inference", expected_epoch=pending_epoch))
        self.assert_error("conversation_changed", lambda: self.store.add_message(
            self.cid, "assistant", "Stale handoff reply", "human_handoff", expected_epoch=pending_epoch))
        self.assert_error("handoff_closed", lambda: self.store.resolve_handoff(handoff["id"], "Duplicate"))
        latest = self.store.get_admin_conversation(self.cid)
        self.assertEqual((latest["status"], latest["ai_epoch"]), ("human", pending_epoch + 1))
        self.assertEqual([m["content"] for m in latest["messages"]], ["Resolved"])
        self.assertEqual(self.store.list_handoffs(status="pending"), [])

    def test_epoch_suppresses_late_response_even_after_human_to_ai_resume(self):
        old_epoch = self.store.get_admin_conversation(self.cid)["ai_epoch"]
        handoff = self.store.create_handoff(self.cid, "help")
        self.store.resolve_handoff(handoff["id"], "Resolved")
        self.store.resume_ai(self.cid)
        self.assertFalse(self.store.add_ai_message_if_active(self.cid, "Stale generation", "mock", old_epoch))
        self.assertTrue(self.store.add_ai_message_if_active(self.cid, "Compatible legacy writer", "mock"))

    def test_real_connections_prevent_late_inference_after_takeover_resume(self):
        inference = Store(self.path)
        operator = Store(self.path)
        started = threading.Event()
        resumed = threading.Event()

        def model_work():
            epoch = inference.get_admin_conversation(self.cid)["ai_epoch"]
            started.set()
            self.assertTrue(resumed.wait(5))
            return inference.add_ai_message_if_active(self.cid, "Stale AI", "mock", epoch)

        with ThreadPoolExecutor(max_workers=2) as pool:
            future = pool.submit(model_work)
            self.assertTrue(started.wait(5))
            handoff = operator.create_handoff(self.cid, "help")
            operator.resolve_handoff(handoff["id"], "Human resolved")
            operator.resume_ai(self.cid)
            resumed.set()
            self.assertFalse(future.result(timeout=5))
        self.assertEqual([m["content"] for m in self.store.get_admin_conversation(self.cid)["messages"]], ["Human resolved"])

    def test_concurrent_resume_increments_epoch_once(self):
        handoff = self.store.create_handoff(self.cid, "help")
        self.store.resolve_handoff(handoff["id"], "Resolved")
        stores = [Store(self.path) for _ in range(5)]
        barrier = threading.Barrier(len(stores))

        def resume(store):
            barrier.wait(timeout=5)
            try:
                store.resume_ai(self.cid)
                return "resumed"
            except SupportError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=len(stores)) as pool:
            results = list(pool.map(resume, stores))
        self.assertEqual(results.count("resumed"), 1)
        self.assertEqual(results.count("invalid_state"), 4)
        self.assertEqual(self.store.get_admin_conversation(self.cid)["ai_epoch"], 3)

    def test_concurrent_close_and_message_serialize_without_post_close_write(self):
        stores = [Store(self.path), Store(self.path)]
        barrier = threading.Barrier(2)

        def close():
            barrier.wait(timeout=5)
            return stores[0].close_conversation(self.cid)

        def write():
            barrier.wait(timeout=5)
            try:
                stores[1].add_message(self.cid, "user", "Racing message", "customer")
                return "written_before_close"
            except SupportError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            closing, writing = pool.submit(close), pool.submit(write)
            self.assertEqual(closing.result(timeout=5)["status"], "closed")
            self.assertIn(writing.result(timeout=5), ("written_before_close", "conversation_closed"))
        self.assertFalse(stores[1].add_ai_message_if_active(self.cid, "Too late", "mock", 0))
        self.assertEqual(self.store.get_admin_conversation(self.cid)["ai_epoch"], 1)

    def test_delete_removes_all_customer_data_and_cascading_extensions(self):
        self.populated()
        self.store.upsert_product("keep", name="Product", description="", price_cents=1)
        with self.store.connection() as conn:
            conn.execute("""CREATE TABLE extension_reservations (
                id TEXT, conversation_id TEXT REFERENCES conversations(id) ON DELETE CASCADE)""")
            conn.execute("INSERT INTO extension_reservations VALUES('r',?)", (self.cid,))
        self.assertEqual(self.store.delete_conversation(self.cid, actor="visitor"), {"deleted": True, "id": self.cid})
        with self.store.connection() as conn:
            for table in ("messages", "handoffs", "leads", "model_usage", "conversations", "extension_reservations"):
                self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
        self.assertIsNotNone(self.store.get_product("keep"))
        self.assert_error("forbidden", lambda: self.store.authorize(self.cid, self.session["session_token"]))

    def test_withdraw_consent_deletes_lead_but_keeps_conversation(self):
        self.lead()
        result = self.store.delete_lead(self.cid, actor="visitor")
        self.assertEqual(result, {"deleted": True, "conversation_id": self.cid, "consent_withdrawn": True})
        self.assertEqual(self.store.list_leads(), [])
        self.assertEqual(self.store.get_admin_conversation(self.cid)["status"], "ai")
        self.assertFalse(self.store.delete_lead(self.cid, actor="visitor")["deleted"])
        self.store.close_conversation(self.cid)
        self.assertFalse(self.store.delete_lead(self.cid)["deleted"])

    def test_withdrawal_allows_new_explicit_consent(self):
        self.lead()
        self.store.delete_lead(self.cid, actor="visitor")
        self.assertTrue(self.lead()["saved"])
        self.assertEqual(len(self.store.list_leads()), 1)

    def test_mutation_actor_is_not_arbitrary_audit_text(self):
        for method in (self.store.close_conversation, self.store.delete_conversation, self.store.delete_lead):
            self.assert_error("invalid_actor", lambda method=method: method(self.cid, actor="private@example.test"))
        self.assertEqual(self.store.get_admin_conversation(self.cid)["status"], "ai")

    def test_audit_contains_fixed_metadata_and_no_personal_information(self):
        self.populated()
        self.store.delete_lead(self.cid, actor="visitor")
        self.store.close_conversation(self.cid, actor="visitor")
        self.store.delete_conversation(self.cid)
        events = self.store.list_audit_events()
        serialized = json.dumps(events)
        for forbidden in ("Test Person", "private@example.test", "Private need", "Private message", self.session["session_token"], self.cid):
            self.assertNotIn(forbidden, serialized)
        self.assertTrue(any(event["event"] == "conversation_deleted" for event in events))
        self.assertTrue(any(event["event"] == "lead_deleted" for event in events))
        for event in events:
            self.assertEqual(set(event), {"id", "event", "actor", "object_id", "created_at"})
            self.assertEqual(event["object_id"], hashlib.sha256(self.cid.encode()).hexdigest())
            self.assertIn(event["actor"], ("admin", "visitor", "system"))

    def test_purge_defaults_to_preview_and_reports_related_counts(self):
        self.populated()
        self.store.close_conversation(self.cid)
        self.set_age(self.cid, "2020-01-01T00:00:00+00:00")
        now = datetime(2026, 10, 15, tzinfo=timezone.utc)
        preview = self.store.purge_expired(30, now=now)
        self.assertTrue(preview["dry_run"])
        self.assertEqual(preview["eligible_count"], 1)
        self.assertEqual(preview["deleted_count"], 0)
        self.assertEqual(preview["counts"], dict(conversations=1, messages=1, handoffs=1, leads=1, model_usage=1))
        self.assertEqual(self.store.get_admin_conversation(self.cid)["status"], "closed")
        result = self.store.purge_expired(30, dry_run=False, now=now)
        self.assertEqual(result["counts"], preview["counts"])
        self.assertEqual(result["deleted_count"], 1)
        self.assert_error("not_found", lambda: self.store.get_admin_conversation(self.cid))
        self.assertTrue(any(e["event"] == "retention_purged" for e in self.store.list_audit_events()))

    def test_purge_preserves_open_recent_and_pending_records(self):
        now = datetime(2026, 10, 15, tzinfo=timezone.utc)
        self.set_age(self.cid, "2020-01-01T00:00:00+00:00")
        human = self.store.create_conversation()["id"]
        self.store.create_handoff(human, "help")
        self.set_age(human, "2020-01-01T00:00:00+00:00")
        recent = self.store.create_conversation()["id"]
        self.store.close_conversation(recent)
        self.set_age(recent, now.isoformat())
        inconsistent = self.store.create_conversation()["id"]
        self.store.create_handoff(inconsistent, "help")
        # Legacy inconsistency: never purge a pending request even if status says closed.
        with self.store.connection() as conn:
            conn.execute("UPDATE conversations SET status='closed' WHERE id=?", (inconsistent,))
        self.set_age(inconsistent, "2020-01-01T00:00:00+00:00")
        result = self.store.purge_expired(30, dry_run=False, now=now)
        self.assertEqual(result["deleted_count"], 0)
        self.assertEqual(len(self.store.list_conversations()), 4)

    def test_purge_uses_utc_instant_and_strict_cutoff(self):
        now = datetime(2026, 10, 15, tzinfo=timezone.utc)
        self.store.close_conversation(self.cid)
        cutoff = now - timedelta(days=30)
        self.set_age(self.cid, cutoff.isoformat())
        self.assertEqual(self.store.purge_expired(30, now=now)["eligible_count"], 0)
        self.set_age(self.cid, (cutoff - timedelta(seconds=1)).isoformat())
        self.assertEqual(self.store.purge_expired(30, now=now)["eligible_count"], 1)

    def test_retention_and_audit_parameters_are_strict(self):
        for days in (True, 0, -1, 1.5, "30", 36501):
            self.assert_error("invalid_retention", lambda days=days: self.store.purge_expired(days))
        for dry_run in (0, 1, "false", None):
            self.assert_error("invalid_retention", lambda dry_run=dry_run: self.store.purge_expired(30, dry_run=dry_run))
        for now in ("2026-10-15", datetime(2026, 10, 15), True):
            self.assert_error("invalid_retention", lambda now=now: self.store.purge_expired(30, now=now))
        for limit in (True, 0, -1, 1001, 1.5, "3"):
            self.assert_error("invalid_input", lambda limit=limit: self.store.list_audit_events(limit))
        for epoch in (True, -1, "0", 1.5):
            self.assert_error("invalid_input", lambda epoch=epoch: self.store.add_ai_message_if_active(self.cid, "x", "mock", epoch))

    def test_unknown_conversations_are_explicit_errors(self):
        for callback in (
            lambda: self.store.close_conversation("missing"),
            lambda: self.store.resume_ai("missing"),
            lambda: self.store.delete_conversation("missing"),
            lambda: self.store.delete_lead("missing"),
            lambda: self.store.create_handoff("missing", "help"),
            lambda: self.store.add_message("missing", "user", "x", "customer"),
        ):
            self.assert_error("not_found", callback)


class LegacyMigrationTests(unittest.TestCase):
    def test_additive_migration_preserves_v1_data_and_latest_activity(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "legacy.db")
            with sqlite3.connect(path) as conn:
                conn.executescript("""
                    CREATE TABLE conversations(id TEXT PRIMARY KEY,token_hash TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'ai',need_summary TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL);
                    CREATE TABLE messages(id INTEGER PRIMARY KEY AUTOINCREMENT,
                        conversation_id TEXT NOT NULL REFERENCES conversations(id),role TEXT NOT NULL,
                        content TEXT NOT NULL,source TEXT NOT NULL,created_at TEXT NOT NULL);
                """)
                conn.execute("INSERT INTO conversations VALUES(?,?,?,?,?)", ("legacy", "hashed", "ai", "keep summary", "2020-01-01T00:00:00+00:00"))
                conn.execute("INSERT INTO messages(conversation_id,role,content,source,created_at) VALUES(?,?,?,?,?)",
                             ("legacy", "user", "keep message", "customer", "2020-02-01T00:00:00+00:00"))
                conn.execute("INSERT INTO conversations VALUES(?,?,?,?,?)", ("empty", "hashed", "ai", "", "2020-03-01T00:00:00+00:00"))
            store = Store(path)
            result = store.get_admin_conversation("legacy")
            self.assertEqual(result["need_summary"], "keep summary")
            self.assertEqual(result["messages"][0]["content"], "keep message")
            self.assertEqual(result["ai_epoch"], 0)
            self.assertEqual(result["updated_at"], "2020-01-01T00:00:00+00:00")
            self.assertEqual(result["last_activity_at"], "2020-02-01T00:00:00+00:00")
            self.assertEqual(store.get_admin_conversation("empty")["last_activity_at"], "2020-03-01T00:00:00+00:00")
            # A second startup neither resets activity nor duplicates/deletes data.
            store.add_message("legacy", "user", "new message", "customer")
            updated = store.get_admin_conversation("legacy")["last_activity_at"]
            Store(path)
            self.assertEqual(store.get_admin_conversation("legacy")["last_activity_at"], updated)
            self.assertEqual(len(store.get_admin_conversation("legacy")["messages"]), 2)


if __name__ == "__main__":
    unittest.main()
