"""Isolated SQLite support data. No customer authentication token is persisted raw."""

import hashlib
import hmac
import json
import math
import os
import secrets
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse


class SupportError(Exception):
    def __init__(self, code, public_message, status_code=400):
        super().__init__(public_message)
        self.code = code
        self.public_message = public_message
        self.status_code = status_code


def _now():
    return datetime.now(timezone.utc).isoformat()


def _id():
    return secrets.token_hex(16)


def _text(value, field, limit, required=False):
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise SupportError("invalid_input", f"{field} 内容无效或超过长度限制")
    return value.strip()


class Store:
    def __init__(self, db_path=None):
        self.db_path = str(db_path or os.getenv("SUPPORT_DB_PATH", "./data/db/support.db"))
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        # WAL is persistent per database file; configure it once at Store
        # startup instead of repeating a journal-mode check for every query.
        with closing(sqlite3.connect(self.db_path, timeout=10)) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
        with self.connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS products (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL,
                    price_cents INTEGER NOT NULL CHECK(price_cents >= 0),
                    currency TEXT NOT NULL DEFAULT 'CNY', url TEXT NOT NULL DEFAULT '',
                    active INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS knowledge (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, content TEXT NOT NULL,
                    kind TEXT NOT NULL CHECK(kind IN ('product','faq','policy')),
                    product_id TEXT REFERENCES products(id), active INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL,
                    review_status TEXT NOT NULL DEFAULT 'draft' CHECK(review_status IN ('draft','approved')),
                    reviewed_at TEXT, source_note TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY, token_hash TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'ai',
                    need_summary TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, last_activity_at TEXT NOT NULL,
                    ai_epoch INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id),
                    role TEXT NOT NULL, content TEXT NOT NULL, source TEXT NOT NULL,
                    created_at TEXT NOT NULL, evidence_json TEXT
                );
                CREATE TABLE IF NOT EXISTS handoffs (
                    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id),
                    reason TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL, resolved_at TEXT
                );
                CREATE TABLE IF NOT EXISTS leads (
                    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL UNIQUE REFERENCES conversations(id),
                    name TEXT NOT NULL, contact TEXT NOT NULL, need TEXT NOT NULL,
                    consent_at TEXT NOT NULL, consent_version TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit_events (
                    id TEXT PRIMARY KEY,
                    event TEXT NOT NULL CHECK(event IN ('conversation_created','conversation_closed',
                        'conversation_resumed','conversation_deleted','handoff_created',
                        'handoff_resolved','lead_saved','lead_deleted','retention_purged')),
                    actor TEXT NOT NULL CHECK(actor IN ('admin','visitor','system')),
                    object_id TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS model_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id),
                    provider TEXT NOT NULL, model TEXT NOT NULL, status TEXT NOT NULL,
                    prompt_tokens INTEGER, completion_tokens INTEGER, estimated_cost REAL,
                    created_at TEXT NOT NULL
                );
            """)
            # Additive migration for existing V1 databases; retain all legacy rows.
            conn.execute("BEGIN IMMEDIATE")
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(conversations)")}
            for name, declaration in (("updated_at", "TEXT NOT NULL DEFAULT ''"),
                                      ("last_activity_at", "TEXT NOT NULL DEFAULT ''"),
                                      ("ai_epoch", "INTEGER NOT NULL DEFAULT 0")):
                if name not in columns:
                    conn.execute(f"ALTER TABLE conversations ADD COLUMN {name} {declaration}")
            conn.execute("UPDATE conversations SET updated_at=created_at WHERE updated_at=''")
            conn.execute("""UPDATE conversations SET last_activity_at=COALESCE(
                (SELECT MAX(created_at) FROM messages WHERE conversation_id=conversations.id),created_at)
                WHERE last_activity_at=''""")
            knowledge_columns = {row["name"] for row in conn.execute("PRAGMA table_info(knowledge)")}
            if "review_status" not in knowledge_columns:
                conn.execute("ALTER TABLE knowledge ADD COLUMN review_status TEXT NOT NULL DEFAULT 'draft' CHECK(review_status IN ('draft','approved'))")
            if "reviewed_at" not in knowledge_columns:
                conn.execute("ALTER TABLE knowledge ADD COLUMN reviewed_at TEXT")
            if "source_note" not in knowledge_columns:
                conn.execute("ALTER TABLE knowledge ADD COLUMN source_note TEXT NOT NULL DEFAULT ''")
            message_columns = {row["name"] for row in conn.execute("PRAGMA table_info(messages)")}
            if "evidence_json" not in message_columns:
                conn.execute("ALTER TABLE messages ADD COLUMN evidence_json TEXT")

    @staticmethod
    def _actor(actor):
        if actor not in ("admin", "visitor"):
            raise SupportError("invalid_actor", "操作身份无效")
        return actor

    @staticmethod
    def _require_conversation(conn, conversation_id, *, active=False):
        row = conn.execute("SELECT * FROM conversations WHERE id=?", (conversation_id,)).fetchone()
        if row is None:
            raise SupportError("not_found", "会话不存在", 404)
        if active and row["status"] == "closed":
            raise SupportError("conversation_closed", "会话已关闭，请新建会话", 409)
        return row

    @staticmethod
    def _audit(conn, event, actor, object_id):
        # Audit metadata must never copy raw customer content or identifiers.
        opaque_id = hashlib.sha256(str(object_id).encode()).hexdigest()
        conn.execute("INSERT INTO audit_events VALUES(?,?,?,?,?)",
                     (_id(), event, actor, opaque_id, _now()))

    @staticmethod
    def _touch(conn, conversation_id, now):
        conn.execute("UPDATE conversations SET updated_at=?,last_activity_at=? WHERE id=?",
                     (now, now, conversation_id))

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            with conn:
                yield conn
        finally:
            conn.close()

    def upsert_product(self, id=None, *, name, description, price_cents, url="", active=True, currency="CNY"):
        name = _text(name, "商品名称", 200, True)
        description = _text(description, "商品描述", 8000)
        url = _text(url, "商品链接", 2000)
        if type(price_cents) is not int or price_cents < 0 or price_cents > 100_000_000:
            raise SupportError("invalid_price", "价格必须为非负整数分")
        if currency != "CNY":
            raise SupportError("invalid_currency", "第一版仅支持 CNY")
        if type(active) is not bool:
            raise SupportError("invalid_input", "上下架状态必须为布尔值")
        parsed = urlparse(url)
        if url and (parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username or parsed.password):
            raise SupportError("invalid_url", "商品链接必须是无凭据的 HTTP(S) 地址")
        pid = _text(id, "商品 ID", 100, True) if id is not None else _id()
        with self.connection() as conn:
            conn.execute("""INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name, description=excluded.description,
                price_cents=excluded.price_cents, currency=excluded.currency, url=excluded.url,
                active=excluded.active, updated_at=excluded.updated_at""",
                (pid, name, description, price_cents, currency, url, int(active), _now()))
        return self.get_product(pid)

    def get_product(self, product_id):
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
        return self._item(row)

    @staticmethod
    def _item(row):
        if row is None:
            return None
        item = dict(row)
        if "active" in item:
            item["active"] = bool(item["active"])
        return item

    def list_products(self, active_only=False):
        with self.connection() as conn:
            rows = conn.execute("SELECT * FROM products WHERE (?=0 OR active=1) ORDER BY name, id", (int(active_only),)).fetchall()
        return [self._item(row) for row in rows]

    def upsert_knowledge(self, id=None, *, title, content, kind="faq", product_id=None, active=True, source_note=None):
        title = _text(title, "知识标题", 200, True)
        content = _text(content, "知识正文", 12000, True)
        if source_note is not None:
            source_note = _text(source_note, "资料来源", 1000)
        if kind not in ("product", "faq", "policy") or type(active) is not bool:
            raise SupportError("invalid_input", "知识类型或状态无效")
        kid = _text(id, "知识 ID", 100, True) if id is not None else _id()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if product_id is not None and conn.execute("SELECT 1 FROM products WHERE id=?", (product_id,)).fetchone() is None:
                raise SupportError("unknown_product", "知识关联的商品不存在")
            prior = conn.execute("SELECT * FROM knowledge WHERE id=?", (kid,)).fetchone()
            # Older clients omit the field: keep existing provenance on updates.
            if source_note is None:
                source_note = prior["source_note"] if prior is not None else ""
            unchanged = prior is not None and all(prior[key] == value for key, value in (
                ("title", title), ("content", content), ("kind", kind), ("product_id", product_id),
                ("source_note", source_note)))
            review_status = prior["review_status"] if unchanged else "draft"
            reviewed_at = prior["reviewed_at"] if unchanged else None
            conn.execute("""INSERT INTO knowledge
                (id,title,content,kind,product_id,active,updated_at,review_status,reviewed_at,source_note)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET title=excluded.title, content=excluded.content,
                kind=excluded.kind, product_id=excluded.product_id, active=excluded.active,
                updated_at=excluded.updated_at,review_status=excluded.review_status,reviewed_at=excluded.reviewed_at,
                source_note=excluded.source_note""",
                (kid, title, content, kind, product_id, int(active), _now(), review_status, reviewed_at, source_note))
            row = conn.execute("SELECT * FROM knowledge WHERE id=?", (kid,)).fetchone()
        return self._item(row)

    def approve_knowledge(self, knowledge_id, *, expected_updated_at):
        expected_updated_at = _text(expected_updated_at, "待审核版本", 64, True)
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM knowledge WHERE id=?", (knowledge_id,)).fetchone()
            if row is None:
                raise SupportError("not_found", "知识不存在", 404)
            if row["updated_at"] != expected_updated_at:
                raise SupportError("knowledge_changed", "知识已更新，请重新核对当前保存版本后审核", 409)
            if row["review_status"] != "approved":
                conn.execute("UPDATE knowledge SET review_status='approved',reviewed_at=? WHERE id=?",
                             (_now(), knowledge_id))
            row = conn.execute("SELECT * FROM knowledge WHERE id=?", (knowledge_id,)).fetchone()
        return self._item(row)

    def list_knowledge(self, active_only=False):
        with self.connection() as conn:
            rows = conn.execute("""SELECT k.* FROM knowledge k LEFT JOIN products p ON p.id=k.product_id
                WHERE (?=0 OR (k.active=1 AND k.review_status='approved' AND (k.product_id IS NULL OR p.active=1)))
                ORDER BY k.title, k.id""", (int(active_only),)).fetchall()
        return [self._item(row) for row in rows]

    def create_conversation(self):
        cid, token = _id(), secrets.token_urlsafe(32)
        with self.connection() as conn:
            now = _now()
            conn.execute("""INSERT INTO conversations(id,token_hash,created_at,updated_at,last_activity_at)
                VALUES (?,?,?,?,?)""", (cid, hashlib.sha256(token.encode()).hexdigest(), now, now, now))
            self._audit(conn, "conversation_created", "visitor", cid)
        return {"id": cid, "session_token": token, "status": "ai", "ai_epoch": 0,
                "created_at": now, "updated_at": now, "last_activity_at": now}

    def authorize(self, conversation_id, token):
        with self.connection() as conn:
            row = conn.execute("SELECT token_hash FROM conversations WHERE id=?", (conversation_id,)).fetchone()
        if not isinstance(token, str) or not row or not hmac.compare_digest(row["token_hash"], hashlib.sha256(token.encode()).hexdigest()):
            raise SupportError("forbidden", "会话不存在或访问凭证无效", 403)

    def get_conversation(self, conversation_id, token):
        self.authorize(conversation_id, token)
        return self.get_admin_conversation(conversation_id, include_evidence=False)

    def get_admin_conversation(self, conversation_id, *, include_evidence=True):
        with self.connection() as conn:
            row = conn.execute("SELECT id,status,need_summary,created_at,updated_at,last_activity_at,ai_epoch FROM conversations WHERE id=?", (conversation_id,)).fetchone()
            if row is None:
                raise SupportError("not_found", "会话不存在", 404)
            result = dict(row)
            # Visitor reads do not select historical merchant snapshots at all.
            columns = "id,role,content,source,created_at" + (",evidence_json" if include_evidence else "")
            result["messages"] = [dict(r) for r in conn.execute(
                f"SELECT {columns} FROM messages WHERE conversation_id=? ORDER BY id", (conversation_id,))]
            if include_evidence:
                for message in result["messages"]:
                    encoded = message.pop("evidence_json")
                    message["evidence"] = json.loads(encoded) if encoded is not None else None
            result["handoffs"] = [dict(r) for r in conn.execute(
                "SELECT * FROM handoffs WHERE conversation_id=? ORDER BY created_at", (conversation_id,))]
        return result

    def admin_get_conversation(self, conversation_id):
        """For authenticated backend callers only; never returns token hashes."""
        return self.get_admin_conversation(conversation_id)

    def list_conversations(self):
        with self.connection() as conn:
            return [dict(r) for r in conn.execute("SELECT id,status,need_summary,created_at,updated_at,last_activity_at,ai_epoch FROM conversations ORDER BY created_at DESC")]

    def add_message(self, conversation_id, role, content, source, expected_epoch=None):
        if expected_epoch is not None and (type(expected_epoch) is not int or expected_epoch < 0):
            raise SupportError("invalid_input", "AI 会话版本无效")
        if role not in ("user", "assistant", "human"):
            raise SupportError("invalid_input", "消息角色无效")
        content = _text(content, "消息", 16000, True)
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._require_conversation(conn, conversation_id, active=True)
            if expected_epoch is not None and row["ai_epoch"] != expected_epoch:
                raise SupportError("conversation_changed", "会话状态已更新，请重新发送问题。", 409)
            now = _now()
            conn.execute("INSERT INTO messages(conversation_id,role,content,source,created_at) VALUES(?,?,?,?,?)",
                         (conversation_id, role, content, source, now))
            self._touch(conn, conversation_id, now)

    @staticmethod
    def _encode_evidence(evidence, source):
        if evidence is None:
            return None
        if source not in ("managed_catalog", "mock_selection") or not isinstance(evidence, dict):
            raise SupportError("invalid_input", "答复依据无效")
        # Copy managed facts and merchant provenance; never raw model output or prompts.
        fields = {
            "products": ("id", "name", "description", "price_cents", "currency", "url", "active", "updated_at"),
            "citations": ("id", "title", "content", "kind", "product_id", "product_name", "active",
                          "updated_at", "review_status", "reviewed_at", "source_note"),
        }
        snapshot = {"version": 1}
        for key, limit in (("products", 6), ("citations", 8)):
            rows = evidence.get(key)
            if not isinstance(rows, list) or len(rows) > limit or not all(isinstance(row, dict) for row in rows):
                raise SupportError("invalid_input", "答复依据无效")
            snapshot[key] = [{field: row[field] for field in fields[key] if field in row} for row in rows]
        return json.dumps(snapshot, ensure_ascii=False)

    def add_ai_message_if_active(self, conversation_id, content, source, expected_epoch=None, *, evidence=None):
        """Atomically suppress a response after takeover, closure or an AI generation change.

        Callers retaining the original three-argument signature keep the status guard.
        Inference callers should pass the epoch captured before the model request.
        """
        content = _text(content, "消息", 16000, True)
        encoded = self._encode_evidence(evidence, source)
        if expected_epoch is not None and (type(expected_epoch) is not int or expected_epoch < 0):
            raise SupportError("invalid_input", "AI 会话版本无效")
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            now = _now()
            cursor = conn.execute("""INSERT INTO messages(conversation_id,role,content,source,created_at,evidence_json)
                SELECT ?, 'assistant', ?, ?, ?, ?
                WHERE EXISTS(SELECT 1 FROM conversations WHERE id=? AND status='ai'
                    AND (? IS NULL OR ai_epoch=?))""",
                (conversation_id, content, source, now, encoded, conversation_id, expected_epoch, expected_epoch))
            if cursor.rowcount == 1:
                self._touch(conn, conversation_id, now)
                return True
            return False

    def update_need_summary(self, conversation_id, summary):
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._require_conversation(conn, conversation_id, active=True)
            conn.execute("UPDATE conversations SET need_summary=?,updated_at=? WHERE id=?",
                         (summary[:600], _now(), conversation_id))

    def create_handoff(self, conversation_id, reason, expected_epoch=None):
        if expected_epoch is not None and (type(expected_epoch) is not int or expected_epoch < 0):
            raise SupportError("invalid_input", "AI 会话版本无效")
        reason = _text(reason, "接管理由", 200, True)
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conversation = self._require_conversation(conn, conversation_id, active=True)
            if expected_epoch is not None and conversation["ai_epoch"] != expected_epoch:
                raise SupportError("conversation_changed", "会话状态已更新，请重新发送问题。", 409)
            existing = conn.execute("SELECT * FROM handoffs WHERE conversation_id=? AND status='pending'", (conversation_id,)).fetchone()
            if existing:
                return {**dict(existing), "conversation_epoch": conversation["ai_epoch"]}
            hid, now = _id(), _now()
            conn.execute("""UPDATE conversations SET status='human',updated_at=?,last_activity_at=?,
                ai_epoch=ai_epoch+CASE WHEN status!='human' THEN 1 ELSE 0 END WHERE id=?""",
                (now, now, conversation_id))
            conn.execute("INSERT INTO handoffs(id,conversation_id,reason,created_at) VALUES(?,?,?,?)", (hid, conversation_id, reason, now))
            self._audit(conn, "handoff_created", "system", conversation["id"])
        epoch = conversation["ai_epoch"] + (conversation["status"] != "human")
        return {"id": hid, "conversation_id": conversation_id, "reason": reason, "status": "pending", "created_at": now, "resolved_at": None,
                "conversation_epoch": epoch}

    def list_handoffs(self, status=None):
        with self.connection() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM handoffs WHERE (? IS NULL OR status=?) ORDER BY created_at", (status, status))]

    def resolve_handoff(self, handoff_id, reply):
        reply = _text(reply, "人工回复", 8000, True)
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM handoffs WHERE id=?", (handoff_id,)).fetchone()
            if not row:
                raise SupportError("not_found", "人工接管请求不存在", 404)
            self._require_conversation(conn, row["conversation_id"], active=True)
            if row["status"] != "pending":
                raise SupportError("handoff_closed", "该人工接管请求已处理", 409)
            now = _now()
            conn.execute("INSERT INTO messages(conversation_id,role,content,source,created_at) VALUES(?,?,?,?,?)",
                         (row["conversation_id"], "human", reply, "human", now))
            conn.execute("UPDATE handoffs SET status='resolved',resolved_at=? WHERE id=?", (now, handoff_id))
            # Invalidate writers that saw this pending task before resolution.
            # Otherwise an old inference can recreate it while status is human.
            conn.execute("""UPDATE conversations SET ai_epoch=ai_epoch+1,
                updated_at=?,last_activity_at=? WHERE id=?""", (now, now, row["conversation_id"]))
            self._audit(conn, "handoff_resolved", "admin", row["conversation_id"])
            result = dict(conn.execute("SELECT * FROM handoffs WHERE id=?", (handoff_id,)).fetchone())
        return result

    def close_conversation(self, conversation_id, actor="admin"):
        actor = self._actor(actor)
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._require_conversation(conn, conversation_id)
            if row["status"] != "closed":
                now = _now()
                conn.execute("""UPDATE conversations SET status='closed',updated_at=?,last_activity_at=?,
                    ai_epoch=ai_epoch+1 WHERE id=?""", (now, now, conversation_id))
                conn.execute("""UPDATE handoffs SET status='closed',resolved_at=?
                    WHERE conversation_id=? AND status='pending'""", (now, conversation_id))
                self._audit(conn, "conversation_closed", actor, conversation_id)
        return self.get_admin_conversation(conversation_id)

    def resume_ai(self, conversation_id):
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._require_conversation(conn, conversation_id, active=True)
            if row["status"] != "human":
                raise SupportError("invalid_state", "只有人工接管中的会话可以恢复 AI", 409)
            pending = conn.execute("SELECT 1 FROM handoffs WHERE conversation_id=? AND status='pending'", (conversation_id,)).fetchone()
            if pending:
                raise SupportError("handoff_pending", "请先处理待办人工接管请求", 409)
            now = _now()
            conn.execute("""UPDATE conversations SET status='ai',ai_epoch=ai_epoch+1,
                updated_at=?,last_activity_at=? WHERE id=?""", (now, now, conversation_id))
            self._audit(conn, "conversation_resumed", "admin", conversation_id)
        return self.get_admin_conversation(conversation_id)

    def save_lead(self, conversation_id, *, consent, name, contact, need, consent_version):
        if consent is not True:
            raise SupportError("consent_required", "只有在阅读告知并明确同意后才会保存联系线索")
        name = _text(name, "姓名", 100)
        contact = _text(contact, "联系方式", 200, True)
        need = _text(need, "需求", 1000)
        consent_version = _text(consent_version, "同意版本", 100, True)
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._require_conversation(conn, conversation_id, active=True)
            prior = conn.execute("SELECT id FROM leads WHERE conversation_id=?", (conversation_id,)).fetchone()
            if prior:
                raise SupportError("lead_exists", "联系线索已提交，修改或删除请联系人工", 409)
            lid, now = _id(), _now()
            conn.execute("INSERT INTO leads VALUES(?,?,?,?,?,?,?)", (lid, conversation_id, name, contact, need, now, consent_version))
            self._audit(conn, "lead_saved", "visitor", conversation_id)
        return {"id": lid, "conversation_id": conversation_id, "consent_at": now, "consent_version": consent_version, "saved": True}

    def delete_lead(self, conversation_id, actor="admin"):
        actor = self._actor(actor)
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._require_conversation(conn, conversation_id)
            deleted = conn.execute("DELETE FROM leads WHERE conversation_id=?", (conversation_id,)).rowcount > 0
            if deleted:
                self._audit(conn, "lead_deleted", actor, conversation_id)
        return {"deleted": deleted, "conversation_id": conversation_id, "consent_withdrawn": True}

    @staticmethod
    def _delete_conversation_rows(conn, conversation_id):
        counts = {}
        for table in ("messages", "handoffs", "leads", "model_usage"):
            counts[table] = conn.execute(f"DELETE FROM {table} WHERE conversation_id=?", (conversation_id,)).rowcount
        counts["conversations"] = conn.execute("DELETE FROM conversations WHERE id=?", (conversation_id,)).rowcount
        return counts

    def delete_conversation(self, conversation_id, actor="admin"):
        actor = self._actor(actor)
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._require_conversation(conn, conversation_id)
            self._delete_conversation_rows(conn, conversation_id)
            self._audit(conn, "conversation_deleted", actor, conversation_id)
        return {"deleted": True, "id": conversation_id}

    def list_audit_events(self, limit=100):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise SupportError("invalid_input", "审计数量必须为 1 到 1000 的整数")
        with self.connection() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT id,event,actor,object_id,created_at FROM audit_events ORDER BY created_at DESC,id DESC LIMIT ?", (limit,))]

    def purge_expired(self, days, dry_run=True, now=None):
        if type(days) is not int or not 1 <= days <= 36500:
            raise SupportError("invalid_retention", "保留天数必须为 1 到 36500 的整数")
        if type(dry_run) is not bool:
            raise SupportError("invalid_retention", "预览标记必须为布尔值")
        if now is None:
            now = datetime.now(timezone.utc)
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise SupportError("invalid_retention", "参考时间必须是带时区的日期时间")
        try:
            cutoff = (now.astimezone(timezone.utc) - timedelta(days=days)).isoformat()
        except (OverflowError, ValueError):
            raise SupportError("invalid_retention", "保留时间超出有效范围") from None
        counts = {table: 0 for table in ("conversations", "messages", "handoffs", "leads", "model_usage")}
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute("""SELECT id FROM conversations WHERE status='closed'
                AND julianday(last_activity_at)<julianday(?)
                AND NOT EXISTS(SELECT 1 FROM handoffs WHERE conversation_id=conversations.id AND status='pending')""", (cutoff,)).fetchall()
            for row in rows:
                cid = row["id"]
                if dry_run:
                    counts["conversations"] += 1
                    for table in ("messages", "handoffs", "leads", "model_usage"):
                        counts[table] += conn.execute(f"SELECT COUNT(*) FROM {table} WHERE conversation_id=?", (cid,)).fetchone()[0]
                else:
                    removed = self._delete_conversation_rows(conn, cid)
                    for table, count in removed.items():
                        counts[table] += count
                    self._audit(conn, "retention_purged", "system", cid)
        return {"dry_run": dry_run, "cutoff": cutoff, "counts": counts,
                "eligible_count": len(rows), "deleted_count": 0 if dry_run else len(rows)}

    def list_leads(self):
        with self.connection() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM leads ORDER BY consent_at DESC")]

    def record_usage(self, conversation_id, provider, model, status, prompt_tokens=None, completion_tokens=None, estimated_cost=None):
        for value in (prompt_tokens, completion_tokens):
            if value is not None and (type(value) is not int or value < 0):
                raise SupportError("invalid_usage", "模型用量无效")
        if estimated_cost is not None and (type(estimated_cost) not in (int, float) or not math.isfinite(estimated_cost) or estimated_cost < 0):
            raise SupportError("invalid_usage", "模型成本无效")
        with self.connection() as conn:
            cursor = conn.execute("""INSERT INTO model_usage(conversation_id,provider,model,status,prompt_tokens,completion_tokens,estimated_cost,created_at)
                SELECT ?,?,?,?,?,?,?,? WHERE EXISTS(SELECT 1 FROM conversations WHERE id=?)""",
                (conversation_id, provider, model, status, prompt_tokens, completion_tokens, estimated_cost, _now(), conversation_id))
            return cursor.rowcount == 1

    def usage_summary(self):
        with self.connection() as conn:
            # Keep the aggregate and recent-call list on one SQLite snapshot so
            # the admin page cannot combine statistics from different writes.
            conn.execute("BEGIN")
            row = conn.execute("""SELECT COUNT(*) AS calls,
                COALESCE(SUM(prompt_tokens),0) AS prompt_tokens,
                COALESCE(SUM(completion_tokens),0) AS completion_tokens,
                SUM(estimated_cost) AS estimated_cost,
                SUM(CASE WHEN estimated_cost IS NULL THEN 1 ELSE 0 END) AS unknown_cost_calls,
                SUM(CASE WHEN status='BLOCKED' THEN 1 ELSE 0 END) AS blocked_calls
                FROM model_usage""").fetchone()
            recent = [dict(r) for r in conn.execute("SELECT provider,model,status,prompt_tokens,completion_tokens,estimated_cost,created_at FROM model_usage ORDER BY id DESC LIMIT 30")]
        summary = dict(row)
        summary["known_estimated_cost"] = summary["estimated_cost"]
        if summary["unknown_cost_calls"]:
            summary["estimated_cost"] = None
        return {**summary, "total_calls": row["calls"], "failed_calls": row["blocked_calls"], "currency": "configured",
                "cost_note": "按管理员配置的同一币种单价估算，非实际账单；含未知成本时总估算为 null", "recent": recent}
