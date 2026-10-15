"""Isolated SQLite support data. No customer authentication token is persisted raw."""

import hashlib
import hmac
import math
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
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
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY, token_hash TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'ai',
                    need_summary TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id),
                    role TEXT NOT NULL, content TEXT NOT NULL, source TEXT NOT NULL,
                    created_at TEXT NOT NULL
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
                CREATE TABLE IF NOT EXISTS model_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id),
                    provider TEXT NOT NULL, model TEXT NOT NULL, status TEXT NOT NULL,
                    prompt_tokens INTEGER, completion_tokens INTEGER, estimated_cost REAL,
                    created_at TEXT NOT NULL
                );
            """)

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        try:
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

    def upsert_knowledge(self, id=None, *, title, content, kind="faq", product_id=None, active=True):
        title = _text(title, "知识标题", 200, True)
        content = _text(content, "知识正文", 12000, True)
        if kind not in ("product", "faq", "policy") or type(active) is not bool:
            raise SupportError("invalid_input", "知识类型或状态无效")
        if product_id is not None and not self.get_product(product_id):
            raise SupportError("unknown_product", "知识关联的商品不存在")
        kid = _text(id, "知识 ID", 100, True) if id is not None else _id()
        with self.connection() as conn:
            conn.execute("""INSERT INTO knowledge VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET title=excluded.title, content=excluded.content,
                kind=excluded.kind, product_id=excluded.product_id, active=excluded.active,
                updated_at=excluded.updated_at""", (kid, title, content, kind, product_id, int(active), _now()))
            row = conn.execute("SELECT * FROM knowledge WHERE id=?", (kid,)).fetchone()
        return self._item(row)

    def list_knowledge(self, active_only=False):
        with self.connection() as conn:
            rows = conn.execute("""SELECT k.* FROM knowledge k LEFT JOIN products p ON p.id=k.product_id
                WHERE (?=0 OR (k.active=1 AND (k.product_id IS NULL OR p.active=1)))
                ORDER BY k.title, k.id""", (int(active_only),)).fetchall()
        return [self._item(row) for row in rows]

    def create_conversation(self):
        cid, token = _id(), secrets.token_urlsafe(32)
        with self.connection() as conn:
            conn.execute("INSERT INTO conversations(id,token_hash,created_at) VALUES (?,?,?)",
                         (cid, hashlib.sha256(token.encode()).hexdigest(), _now()))
        return {"id": cid, "session_token": token, "status": "ai"}

    def authorize(self, conversation_id, token):
        with self.connection() as conn:
            row = conn.execute("SELECT token_hash FROM conversations WHERE id=?", (conversation_id,)).fetchone()
        if not isinstance(token, str) or not row or not hmac.compare_digest(row["token_hash"], hashlib.sha256(token.encode()).hexdigest()):
            raise SupportError("forbidden", "会话不存在或访问凭证无效", 403)

    def get_conversation(self, conversation_id, token):
        self.authorize(conversation_id, token)
        return self.get_admin_conversation(conversation_id)

    def get_admin_conversation(self, conversation_id):
        with self.connection() as conn:
            row = conn.execute("SELECT id,status,need_summary,created_at FROM conversations WHERE id=?", (conversation_id,)).fetchone()
            if row is None:
                raise SupportError("not_found", "会话不存在", 404)
            result = dict(row)
            result["messages"] = [dict(r) for r in conn.execute(
                "SELECT id,role,content,source,created_at FROM messages WHERE conversation_id=? ORDER BY id", (conversation_id,))]
            result["handoffs"] = [dict(r) for r in conn.execute(
                "SELECT * FROM handoffs WHERE conversation_id=? ORDER BY created_at", (conversation_id,))]
        return result

    def admin_get_conversation(self, conversation_id):
        """For authenticated backend callers only; never returns token hashes."""
        return self.get_admin_conversation(conversation_id)

    def list_conversations(self):
        with self.connection() as conn:
            return [dict(r) for r in conn.execute("SELECT id,status,need_summary,created_at FROM conversations ORDER BY created_at DESC")]

    def add_message(self, conversation_id, role, content, source):
        if role not in ("user", "assistant", "human"):
            raise SupportError("invalid_input", "消息角色无效")
        content = _text(content, "消息", 16000, True)
        with self.connection() as conn:
            conn.execute("INSERT INTO messages(conversation_id,role,content,source,created_at) VALUES(?,?,?,?,?)",
                         (conversation_id, role, content, source, _now()))

    def add_ai_message_if_active(self, conversation_id, content, source):
        """A single SQLite write prevents a late AI reply after human takeover.

        The status predicate and insert serialize with handoff's status update,
        including writers using another Store instance or another API worker.
        """
        content = _text(content, "消息", 16000, True)
        with self.connection() as conn:
            cursor = conn.execute("""INSERT INTO messages(conversation_id,role,content,source,created_at)
                SELECT ?, 'assistant', ?, ?, ?
                WHERE EXISTS(SELECT 1 FROM conversations WHERE id=? AND status='ai')""",
                (conversation_id, content, source, _now(), conversation_id))
            return cursor.rowcount == 1

    def update_need_summary(self, conversation_id, summary):
        with self.connection() as conn:
            conn.execute("UPDATE conversations SET need_summary=? WHERE id=?", (summary[:600], conversation_id))

    def create_handoff(self, conversation_id, reason):
        reason = _text(reason, "接管理由", 200, True)
        with self.connection() as conn:
            # Serialize the read/decision/write across connections and workers.
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT * FROM handoffs WHERE conversation_id=? AND status='pending'", (conversation_id,)).fetchone()
            conn.execute("UPDATE conversations SET status='human' WHERE id=?", (conversation_id,))
            if existing:
                return dict(existing)
            hid, now = _id(), _now()
            conn.execute("INSERT INTO handoffs(id,conversation_id,reason,created_at) VALUES(?,?,?,?)", (hid, conversation_id, reason, now))
        return {"id": hid, "conversation_id": conversation_id, "reason": reason, "status": "pending", "created_at": now, "resolved_at": None}

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
            if row["status"] != "pending":
                raise SupportError("handoff_closed", "该人工接管请求已处理", 409)
            conn.execute("INSERT INTO messages(conversation_id,role,content,source,created_at) VALUES(?,?,?,?,?)",
                         (row["conversation_id"], "human", reply, "human", _now()))
            conn.execute("UPDATE handoffs SET status='resolved',resolved_at=? WHERE id=?", (_now(), handoff_id))
            result = dict(conn.execute("SELECT * FROM handoffs WHERE id=?", (handoff_id,)).fetchone())
        return result

    def save_lead(self, conversation_id, *, consent, name, contact, need, consent_version):
        if consent is not True:
            raise SupportError("consent_required", "只有在阅读告知并明确同意后才会保存联系线索")
        name = _text(name, "姓名", 100)
        contact = _text(contact, "联系方式", 200, True)
        need = _text(need, "需求", 1000)
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            prior = conn.execute("SELECT id FROM leads WHERE conversation_id=?", (conversation_id,)).fetchone()
            if prior:
                raise SupportError("lead_exists", "联系线索已提交，修改或删除请联系人工", 409)
            lid, now = _id(), _now()
            conn.execute("INSERT INTO leads VALUES(?,?,?,?,?,?,?)", (lid, conversation_id, name, contact, need, now, consent_version))
        return {"id": lid, "conversation_id": conversation_id, "consent_at": now, "consent_version": consent_version, "saved": True}

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
            conn.execute("""INSERT INTO model_usage(conversation_id,provider,model,status,prompt_tokens,completion_tokens,estimated_cost,created_at)
                VALUES(?,?,?,?,?,?,?,?)""", (conversation_id, provider, model, status, prompt_tokens, completion_tokens, estimated_cost, _now()))

    def usage_summary(self):
        with self.connection() as conn:
            row = conn.execute("""SELECT COUNT(*) AS calls,
                COALESCE(SUM(prompt_tokens),0) AS prompt_tokens,
                COALESCE(SUM(completion_tokens),0) AS completion_tokens,
                SUM(estimated_cost) AS estimated_cost,
                SUM(CASE WHEN estimated_cost IS NULL THEN 1 ELSE 0 END) AS unknown_cost_calls
                FROM model_usage""").fetchone()
            recent = [dict(r) for r in conn.execute("SELECT provider,model,status,prompt_tokens,completion_tokens,estimated_cost,created_at FROM model_usage ORDER BY id DESC LIMIT 30")]
        with self.connection() as conn:
            blocked = conn.execute("SELECT COUNT(*) FROM model_usage WHERE status='BLOCKED'").fetchone()[0]
        summary = dict(row)
        summary["known_estimated_cost"] = summary["estimated_cost"]
        if summary["unknown_cost_calls"]:
            summary["estimated_cost"] = None
        return {**summary, "total_calls": row["calls"], "blocked_calls": blocked,
                "failed_calls": blocked, "currency": "configured",
                "cost_note": "按管理员配置的同一币种单价估算，非实际账单；含未知成本时总估算为 null", "recent": recent}
