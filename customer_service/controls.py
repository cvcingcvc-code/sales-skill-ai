"""SQLite resource controls shared by API workers without storing request data.

Rate limits use a rolling window that begins with its first accepted request.
Model limits count attempted calls and conservatively reserve the configured
maximum input plus output tokens before the provider is called. Only complete,
valid provider usage can release unused tokens. A crashed or failed call keeps
its reservation. Daily aggregate counters intentionally outlive conversation
deletion, so deleting customer records cannot reset a shared budget.

Daily limits accept zero to disable model calls. Per-request token maxima must
be positive and must also bound the actual provider payload. Cost estimates use
configured currency prices, never a claim about a provider's actual bill.
"""

import hashlib
import math
import os
import re
import secrets
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING

from customer_service.store import SupportError


_COST_SCALE = 1_000_000_000
_MAX_SQLITE_INTEGER = 9_223_372_036_854_775_807


def _timestamp(now=None):
    if now is None:
        return datetime.now(timezone.utc).timestamp()
    if isinstance(now, datetime):
        if now.tzinfo is None:
            raise ValueError("Injected timestamps must have an explicit timezone")
        return now.timestamp()
    if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now):
        raise ValueError("Invalid timestamp")
    return float(now)


def _budget_error(message="今日模型调用预算已用尽，请稍后重试或联系人工。"):
    return SupportError("budget_exceeded", message, 429)


def _configuration_error():
    return SupportError("configuration", "模型预算配置无效，请联系管理员检查。", 503)


def _integer_setting(name, default, minimum=0):
    raw = os.getenv(name, str(default)).strip()
    try:
        if not re.fullmatch(r"[0-9]+", raw):
            raise ValueError
        value = int(raw)
        if value < minimum or value > _MAX_SQLITE_INTEGER:
            raise ValueError
        return value
    except ValueError:
        raise _configuration_error() from None


def _decimal_value(raw):
    try:
        value = Decimal(str(raw))
        return value if value.is_finite() and 0 <= value <= _MAX_SQLITE_INTEGER else None
    except (InvalidOperation, ValueError):
        return None


def _cost_units(input_tokens, output_tokens, input_price, output_price):
    if input_price is None or output_price is None:
        return None
    amount = ((input_tokens * Decimal(input_price) + output_tokens * Decimal(output_price))
              * _COST_SCALE / 1_000_000)
    units = int(amount.to_integral_value(rounding=ROUND_CEILING))
    if units > _MAX_SQLITE_INTEGER:
        raise _configuration_error()
    return units


def _count(value):
    # Provider usage is untrusted. This comfortably exceeds supported contexts
    # while rejecting absurd counts before SQLite arithmetic can overflow.
    return value if type(value) is int and 0 <= value <= 1_000_000_000 else None


class RateLimiter:
    """Atomic per-key request windows; raw client keys never enter SQLite."""

    def __init__(self, store):
        self.store = store
        with self.store.connection() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS rate_limit_buckets (
                bucket TEXT NOT NULL, key_hash TEXT NOT NULL,
                window_seconds INTEGER NOT NULL, expires_at REAL NOT NULL,
                hits INTEGER NOT NULL,
                PRIMARY KEY(bucket,key_hash,window_seconds))""")
            conn.execute("CREATE INDEX IF NOT EXISTS rate_limit_expiry ON rate_limit_buckets(expires_at)")

    def check(self, bucket, key, limit, window_seconds, now=None):
        if (not isinstance(bucket, str) or not re.fullmatch(r"[A-Za-z0-9:_-]{1,100}", bucket)
                or not isinstance(key, str) or not key or len(key) > 4096
                or type(limit) is not int or limit < 0 or limit > _MAX_SQLITE_INTEGER
                or type(window_seconds) is not int or not 0 < window_seconds <= 31_536_000):
            raise SupportError("configuration", "请求限流配置无效，请联系管理员检查。", 503)
        stamp = _timestamp(now)
        key_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()
        retry_after = None
        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            # Each request does bounded maintenance; no unbounded event history.
            conn.execute("""DELETE FROM rate_limit_buckets WHERE rowid IN (
                SELECT rowid FROM rate_limit_buckets WHERE expires_at<=? LIMIT 256)""", (stamp,))
            row = conn.execute("""SELECT hits,expires_at FROM rate_limit_buckets
                WHERE bucket=? AND key_hash=? AND window_seconds=? AND expires_at>?""",
                (bucket, key_hash, window_seconds, stamp)).fetchone()
            if limit == 0 or (row and row["hits"] >= limit):
                retry_after = max(1, math.ceil(row["expires_at"] - stamp)) if row else window_seconds
            elif row:
                conn.execute("""UPDATE rate_limit_buckets SET hits=hits+1
                    WHERE bucket=? AND key_hash=? AND window_seconds=?""",
                    (bucket, key_hash, window_seconds))
            else:
                conn.execute("""INSERT INTO rate_limit_buckets VALUES(?,?,?,?,1)
                    ON CONFLICT(bucket,key_hash,window_seconds) DO UPDATE
                    SET expires_at=excluded.expires_at,hits=1""",
                             (bucket, key_hash, window_seconds, stamp + window_seconds))
        if retry_after is not None:
            error = SupportError("rate_limited", "请求过于频繁，请稍后重试。", 429)
            error.retry_after = retry_after
            raise error


class ModelBudget:
    """Shared UTC-day call/token budgets, with optional configured-cost limits."""

    def __init__(self, store):
        self.store = store
        with self.store.connection() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS model_budget_days (
                day TEXT PRIMARY KEY, calls INTEGER NOT NULL DEFAULT 0,
                tokens INTEGER NOT NULL DEFAULT 0, cost_units INTEGER NOT NULL DEFAULT 0,
                unknown_cost_calls INTEGER NOT NULL DEFAULT 0)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS model_budget_reservations (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                day TEXT NOT NULL REFERENCES model_budget_days(day),
                provider TEXT NOT NULL, model TEXT NOT NULL,
                input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,
                charged_tokens INTEGER NOT NULL, charged_cost_units INTEGER,
                input_price TEXT, output_price TEXT,
                state TEXT NOT NULL DEFAULT 'reserved', error_code TEXT,
                created_at REAL NOT NULL, completed_at REAL)""")
            conn.execute("CREATE INDEX IF NOT EXISTS model_budget_day_state ON model_budget_reservations(day,state)")

    @staticmethod
    def _settings():
        provider_output = _integer_setting("MODEL_MAX_OUTPUT_TOKENS", 4096, 1)
        support_output = _integer_setting("SUPPORT_MODEL_MAX_OUTPUT_TOKENS", 512, 1)
        if support_output > 4096:
            raise _configuration_error()
        settings = {
            "daily_call_limit": _integer_setting("MODEL_DAILY_CALL_LIMIT", 200),
            "daily_token_limit": _integer_setting("MODEL_DAILY_TOKEN_LIMIT", 200_000),
            "max_input_tokens": _integer_setting("MODEL_MAX_INPUT_TOKENS", 16_000, 1),
            # Customer service reserves the same output ceiling passed to its
            # selector provider, never the larger training/provider ceiling.
            "max_output_tokens": min(provider_output, support_output),
        }
        if settings["max_input_tokens"] + settings["max_output_tokens"] > _MAX_SQLITE_INTEGER:
            raise _configuration_error()
        raw_limit = os.getenv("MODEL_DAILY_COST_LIMIT", "").strip()
        cost_limit = _decimal_value(raw_limit) if raw_limit else None
        if raw_limit and cost_limit is None:
            raise _configuration_error()
        if cost_limit is not None and cost_limit * _COST_SCALE > _MAX_SQLITE_INTEGER:
            raise _configuration_error()
        # Round down the cap and round up estimates to avoid a rounding bypass.
        settings["daily_cost_limit_units"] = int(cost_limit * _COST_SCALE) if cost_limit is not None else None
        return settings

    def reserve(self, conversation_id, provider, model, *, now=None):
        if (not isinstance(conversation_id, str) or not conversation_id or len(conversation_id) > 100
                or not isinstance(provider, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,50}", provider)
                or not isinstance(model, str) or not model.strip() or len(model) > 200):
            raise _configuration_error()
        settings = self._settings()
        stamp = _timestamp(now)
        day = datetime.fromtimestamp(stamp, timezone.utc).date().isoformat()
        prices = []
        for suffix in ("INPUT_PRICE_PER_MILLION", "OUTPUT_PRICE_PER_MILLION"):
            raw = os.getenv(f"{provider.upper()}_{suffix}", "").strip()
            price = _decimal_value(raw) if raw else None
            if raw and price is None:
                raise _configuration_error()
            prices.append(str(price) if price is not None else None)
        cost = _cost_units(settings["max_input_tokens"], settings["max_output_tokens"], *prices)
        cap = settings["daily_cost_limit_units"]
        if cap is not None and cost is None:
            raise _budget_error("已启用模型金额预算，但缺少有效的输入和输出单价；请联系管理员。")
        tokens = settings["max_input_tokens"] + settings["max_output_tokens"]
        rid = secrets.token_hex(16)
        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not conn.execute("SELECT 1 FROM conversations WHERE id=?", (conversation_id,)).fetchone():
                raise SupportError("not_found", "会话不存在", 404)
            conn.execute("INSERT OR IGNORE INTO model_budget_days(day) VALUES(?)", (day,))
            current = conn.execute("SELECT * FROM model_budget_days WHERE day=?", (day,)).fetchone()
            if (current["calls"] >= settings["daily_call_limit"]
                    or current["tokens"] + tokens > settings["daily_token_limit"]
                    or (cap is not None and (current["unknown_cost_calls"] > 0
                                            or current["cost_units"] + cost > cap))):
                raise _budget_error()
            conn.execute("""UPDATE model_budget_days SET calls=calls+1,tokens=tokens+?,
                cost_units=cost_units+?,unknown_cost_calls=unknown_cost_calls+? WHERE day=?""",
                (tokens, cost or 0, int(cost is None), day))
            conn.execute("""INSERT INTO model_budget_reservations
                (id,conversation_id,day,provider,model,input_tokens,output_tokens,
                 charged_tokens,charged_cost_units,input_price,output_price,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (rid, conversation_id, day, provider, model, settings["max_input_tokens"],
                 settings["max_output_tokens"], tokens, cost, *prices, stamp))
        return {"id": rid, "day": day, "provider": provider, "model": model,
                "reserved_input_tokens": settings["max_input_tokens"],
                "reserved_output_tokens": settings["max_output_tokens"],
                "reserved_tokens": tokens,
                "estimated_cost": cost / _COST_SCALE if cost is not None else None}

    def complete(self, reservation, result=None, error_code=None, now=None):
        rid = reservation.get("id") if isinstance(reservation, dict) else None
        if not isinstance(rid, str):
            raise _configuration_error()
        stamp = _timestamp(now)
        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM model_budget_reservations WHERE id=?", (rid,)).fetchone()
            # Idempotent, including when a user deleted the conversation in flight.
            if not row or row["state"] != "reserved":
                return
            prompt = _count(getattr(result, "prompt_tokens", None))
            output = _count(getattr(result, "completion_tokens", None))
            has_usage = (not error_code and prompt is not None and output is not None
                         and prompt + output <= _MAX_SQLITE_INTEGER)
            tokens = prompt + output if has_usage else row["charged_tokens"]
            cost = (_cost_units(prompt, output, row["input_price"], row["output_price"])
                    if has_usage else row["charged_cost_units"])
            # Unknown prices stay unknown; actual billing is never inferred as zero.
            conn.execute("""UPDATE model_budget_days SET tokens=tokens+?,cost_units=cost_units+?
                WHERE day=?""", (tokens - row["charged_tokens"],
                                   (cost or 0) - (row["charged_cost_units"] or 0), row["day"]))
            safe_code = error_code if isinstance(error_code, str) and re.fullmatch(r"[a-z0-9_]{1,64}", error_code) else None
            conn.execute("""UPDATE model_budget_reservations
                SET charged_tokens=?,charged_cost_units=?,state=?,error_code=?,completed_at=? WHERE id=?""",
                (tokens, cost, "failed" if error_code else "completed", safe_code, stamp, rid))

    def summary(self, now=None):
        settings = self._settings()
        stamp = _timestamp(now)
        day = datetime.fromtimestamp(stamp, timezone.utc).date().isoformat()
        with self.store.connection() as conn:
            row = conn.execute("SELECT * FROM model_budget_days WHERE day=?", (day,)).fetchone()
            counts = {r["state"]: r["n"] for r in conn.execute("""SELECT state,COUNT(*) AS n
                FROM model_budget_reservations WHERE day=? GROUP BY state""", (day,))}
        counters = dict(row) if row else {"calls": 0, "tokens": 0, "cost_units": 0, "unknown_cost_calls": 0}
        known_cost = counters["cost_units"] / _COST_SCALE
        cap = settings.pop("daily_cost_limit_units")
        return {"day": day, "timezone": "UTC", **settings,
                "calls": counters["calls"], "tokens": counters["tokens"],
                "remaining_calls": max(0, settings["daily_call_limit"] - counters["calls"]),
                "remaining_tokens": max(0, settings["daily_token_limit"] - counters["tokens"]),
                "reserved_calls": counts.get("reserved", 0),
                "completed_calls": counts.get("completed", 0), "failed_calls": counts.get("failed", 0),
                "estimated_cost": known_cost if counters["unknown_cost_calls"] == 0 else None,
                "known_estimated_cost": known_cost,
                "unknown_cost_calls": counters["unknown_cost_calls"],
                "cost_status": "configured_estimate" if counters["unknown_cost_calls"] == 0 else "unknown",
                "daily_cost_limit": cap / _COST_SCALE if cap is not None else None,
                "currency": "configured", "token_accounting": "conservative_reservation"}
