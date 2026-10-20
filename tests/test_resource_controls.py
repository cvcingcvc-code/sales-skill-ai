"""Offline resource-control tests; no provider requests or wall-clock sleeps."""

from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from customer_service.controls import ModelBudget, RateLimiter
from customer_service.store import Store, SupportError


NOW = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)


def _process_reserve(arguments):
    path, cid = arguments
    try:
        ModelBudget(Store(path)).reserve(cid, "ollama", "test-local", now=NOW)
        return "reserved"
    except SupportError as error:
        return error.code


def _process_rate(arguments):
    path, limit = arguments
    try:
        RateLimiter(Store(path)).check("process", "opaque-client", limit, 60, now=NOW)
        return "accepted"
    except SupportError as error:
        return error.code


class ResourceControlsCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = self.temp.name + "/support.db"
        self.store = Store(self.path)
        self.session = self.store.create_conversation()
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def assert_error(self, code, function, *args, **kwargs):
        with self.assertRaises(SupportError) as raised:
            function(*args, **kwargs)
        self.assertEqual(raised.exception.code, code)
        return raised.exception


class TestRateLimiter(ResourceControlsCase):
    def test_threshold_has_retry_after_without_consuming_more_requests(self):
        limiter = RateLimiter(self.store)
        limiter.check("message", "token", 2, 60, now=100)
        limiter.check("message", "token", 2, 60, now=101)
        error = self.assert_error("rate_limited", limiter.check, "message", "token", 2, 60, now=104)
        self.assertEqual(error.status_code, 429)
        self.assertEqual(error.retry_after, 56)
        with self.store.connection() as conn:
            self.assertEqual(conn.execute("SELECT hits FROM rate_limit_buckets").fetchone()[0], 2)

    def test_reset_exactly_at_window_boundary(self):
        limiter = RateLimiter(self.store)
        limiter.check("message", "token", 1, 60, now=100)
        self.assert_error("rate_limited", limiter.check, "message", "token", 1, 60, now=159.1)
        limiter.check("message", "token", 1, 60, now=160)

    def test_bucket_keys_and_window_sizes_are_independent(self):
        limiter = RateLimiter(self.store)
        for bucket, key, window in [("one", "a", 60), ("one", "b", 60), ("two", "a", 60), ("one", "a", 120)]:
            limiter.check(bucket, key, 1, window, now=NOW)

    def test_only_key_hash_is_persisted(self):
        key = "raw-customer-ip-or-session-token"
        RateLimiter(self.store).check("create", key, 2, 60, now=NOW)
        with self.store.connection() as conn:
            rows = [dict(row) for row in conn.execute("SELECT * FROM rate_limit_buckets")]
        self.assertNotIn(key, json.dumps(rows))
        self.assertEqual(rows[0]["key_hash"], hashlib.sha256(key.encode()).hexdigest())

    def test_zero_limit_disables_requests(self):
        error = self.assert_error("rate_limited", RateLimiter(self.store).check, "create", "key", 0, 60, now=NOW)
        self.assertEqual(error.retry_after, 60)

    def test_invalid_config_fails_closed(self):
        limiter = RateLimiter(self.store)
        for limit, window in [(-1, 60), (True, 60), (1, 0), (1, -1), (1, True)]:
            with self.subTest(limit=limit, window=window):
                self.assert_error("configuration", limiter.check, "create", "key", limit, window, now=NOW)

    def test_atomic_limit_across_store_instances_and_threads(self):
        RateLimiter(self.store)
        def check(_):
            try:
                RateLimiter(Store(self.path)).check("create", "shared", 5, 60, now=NOW)
                return "accepted"
            except SupportError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(check, range(24)))
        self.assertEqual(results.count("accepted"), 5)
        self.assertEqual(results.count("rate_limited"), 19)

    def test_atomic_limit_across_processes(self):
        RateLimiter(self.store)
        with ProcessPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(_process_rate, [(self.path, 3)] * 12))
        self.assertEqual(results.count("accepted"), 3)
        self.assertEqual(results.count("rate_limited"), 9)

    def test_expired_bucket_cleanup_is_bounded_and_does_not_block_current_key(self):
        limiter = RateLimiter(self.store)
        with self.store.connection() as conn:
            conn.executemany("INSERT INTO rate_limit_buckets VALUES(?,?,?,?,?)",
                             [("old", str(i), 60, 10, 1) for i in range(300)])
            conn.execute("INSERT INTO rate_limit_buckets VALUES(?,?,?,?,?)",
                         ("create", hashlib.sha256(b"target").hexdigest(), 60, 10, 99))
        limiter.check("create", "target", 1, 60, now=100)
        with self.store.connection() as conn:
            count = conn.execute("SELECT COUNT(*) FROM rate_limit_buckets WHERE expires_at<=100").fetchone()[0]
            self.assertEqual(count, 44)
        limiter.check("other", "new", 1, 60, now=100)
        with self.store.connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM rate_limit_buckets WHERE expires_at<=100").fetchone()[0], 0)


class TestModelBudget(ResourceControlsCase):
    def budget(self, **settings):
        os.environ.update({name: str(value) for name, value in settings.items()})
        return ModelBudget(self.store)

    def reserve(self, budget, now=NOW):
        return budget.reserve(self.session["id"], "ollama", "test-local", now=now)

    def test_defaults_and_safe_summary(self):
        budget = self.budget()
        result = budget.summary(now=NOW)
        self.assertEqual(result["daily_call_limit"], 200)
        self.assertEqual(result["daily_token_limit"], 200_000)
        self.assertEqual(result["max_input_tokens"] + result["max_output_tokens"], 16_512)
        self.assertEqual(result["calls"], 0)
        self.assertNotIn(self.session["session_token"], json.dumps(result))

    def test_call_limit(self):
        budget = self.budget(MODEL_DAILY_CALL_LIMIT=1)
        self.reserve(budget)
        error = self.assert_error("budget_exceeded", self.reserve, budget)
        self.assertEqual(error.status_code, 429)
        self.assertEqual(budget.summary(now=NOW)["calls"], 1)

    def test_token_limit_reserves_full_request(self):
        budget = self.budget(MODEL_DAILY_TOKEN_LIMIT=33_023)
        reservation = self.reserve(budget)
        self.assertEqual(reservation["reserved_tokens"], 16_512)
        self.assert_error("budget_exceeded", self.reserve, budget)

    def test_zero_daily_call_or_token_limit_disables_model(self):
        for name in ("MODEL_DAILY_CALL_LIMIT", "MODEL_DAILY_TOKEN_LIMIT"):
            with self.subTest(name=name), patch.dict(os.environ, {name: "0"}):
                budget = ModelBudget(self.store)
                self.assert_error("budget_exceeded", self.reserve, budget)
                self.assertEqual(budget.summary(now=NOW)["calls"], 0)

    def test_actual_usage_releases_only_token_difference(self):
        budget = self.budget(MODEL_DAILY_TOKEN_LIMIT=20_200)
        reservation = self.reserve(budget)
        budget.complete(reservation, SimpleNamespace(prompt_tokens=80, completion_tokens=20), now=NOW)
        self.assertEqual(budget.summary(now=NOW)["tokens"], 100)
        self.assertEqual(budget.summary(now=NOW)["calls"], 1)
        self.reserve(budget)

    def test_missing_usage_keeps_conservative_reservation(self):
        budget = self.budget()
        reservation = self.reserve(budget)
        budget.complete(reservation, SimpleNamespace(prompt_tokens=None, completion_tokens=None), now=NOW)
        summary = budget.summary(now=NOW)
        self.assertEqual(summary["tokens"], 16_512)
        self.assertEqual(summary["completed_calls"], 1)
        self.assertIsNone(summary["estimated_cost"])
        self.assertEqual(summary["cost_status"], "unknown")

    def test_partial_and_invalid_usage_cannot_release_budget(self):
        budget = self.budget()
        for prompt, output in [(10, None), (None, 10), (True, 2), (-1, 2), (2.1, 2),
                               (2, float("nan")), (10 ** 100, 2)]:
            with self.subTest(prompt=prompt, output=output):
                reservation = self.reserve(budget)
                budget.complete(reservation, SimpleNamespace(prompt_tokens=prompt, completion_tokens=output), now=NOW)
        self.assertEqual(budget.summary(now=NOW)["tokens"], 7 * 16_512)

    def test_failed_calls_count_and_keep_tokens(self):
        budget = self.budget()
        reservation = self.reserve(budget)
        budget.complete(reservation, error_code="timeout", now=NOW)
        result = budget.summary(now=NOW)
        self.assertEqual(result["calls"], 1)
        self.assertEqual(result["tokens"], 16_512)
        self.assertEqual(result["failed_calls"], 1)

    def test_failed_call_does_not_trust_partial_usage(self):
        budget = self.budget()
        reservation = self.reserve(budget)
        budget.complete(reservation, SimpleNamespace(prompt_tokens=2, completion_tokens=2), error_code="timeout", now=NOW)
        self.assertEqual(budget.summary(now=NOW)["tokens"], 16_512)

    def test_crashed_request_remains_reserved(self):
        budget = self.budget()
        self.reserve(budget)
        summary = ModelBudget(Store(self.path)).summary(now=NOW + timedelta(hours=1))
        self.assertEqual(summary["reserved_calls"], 1)
        self.assertEqual(summary["tokens"], 16_512)

    def test_complete_is_idempotent_and_uses_database_reservation(self):
        budget = self.budget()
        reservation = self.reserve(budget)
        reservation["reserved_tokens"] = 1_000_000
        budget.complete(reservation, SimpleNamespace(prompt_tokens=80, completion_tokens=20), now=NOW)
        budget.complete(reservation, SimpleNamespace(prompt_tokens=1, completion_tokens=1), now=NOW)
        self.assertEqual(budget.summary(now=NOW)["tokens"], 100)

    def test_utc_day_boundary_and_late_completion_update_original_day(self):
        budget = self.budget(MODEL_DAILY_CALL_LIMIT=1)
        before = datetime(2026, 10, 15, 23, 59, 59, tzinfo=timezone.utc)
        after = before + timedelta(seconds=1)
        reservation = self.reserve(budget, now=before)
        self.reserve(budget, now=after)
        budget.complete(reservation, SimpleNamespace(prompt_tokens=8, completion_tokens=2), now=after)
        self.assertEqual(budget.summary(now=before)["tokens"], 10)
        self.assertEqual(budget.summary(now=after)["tokens"], 16_512)

    def test_timezone_offset_uses_utc_day(self):
        budget = self.budget()
        local = datetime(2026, 10, 16, 0, 30, tzinfo=timezone(timedelta(hours=8)))
        self.assertEqual(self.reserve(budget, now=local)["day"], "2026-10-15")

    def test_budget_is_atomic_across_store_instances_and_threads(self):
        budget = self.budget(MODEL_DAILY_CALL_LIMIT=5)
        def reserve(_):
            try:
                self.reserve(ModelBudget(Store(self.path)))
                return "reserved"
            except SupportError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(reserve, range(24)))
        self.assertEqual(results.count("reserved"), 5)
        self.assertEqual(results.count("budget_exceeded"), 19)
        self.assertEqual(budget.summary(now=NOW)["tokens"], 5 * 16_512)

    def test_budget_is_atomic_across_processes(self):
        budget = self.budget(MODEL_DAILY_CALL_LIMIT=3)
        with ProcessPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(_process_reserve, [(self.path, self.session["id"])] * 12))
        self.assertEqual(results.count("reserved"), 3)
        self.assertEqual(results.count("budget_exceeded"), 9)
        self.assertEqual(budget.summary(now=NOW)["calls"], 3)

    def test_delete_conversation_does_not_reset_daily_quota(self):
        budget = self.budget(MODEL_DAILY_CALL_LIMIT=1)
        reservation = self.reserve(budget)
        with self.store.connection() as conn:
            conn.execute("DELETE FROM conversations WHERE id=?", (self.session["id"],))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM model_budget_reservations").fetchone()[0], 0)
        budget.complete(reservation, SimpleNamespace(prompt_tokens=1, completion_tokens=1), now=NOW)
        self.session = self.store.create_conversation()
        self.assert_error("budget_exceeded", self.reserve, budget)
        self.assertEqual(budget.summary(now=NOW)["calls"], 1)
        self.assertEqual(budget.summary(now=NOW)["tokens"], 16_512)

    def test_missing_conversation_is_not_charged(self):
        budget = self.budget()
        self.assert_error("not_found", budget.reserve, "missing", "ollama", "test-local", now=NOW)
        self.assertEqual(budget.summary(now=NOW)["calls"], 0)

    def test_invalid_budget_values_fail_closed(self):
        for name, value in [("MODEL_DAILY_CALL_LIMIT", "-1"), ("MODEL_DAILY_TOKEN_LIMIT", "oops"),
                            ("MODEL_MAX_INPUT_TOKENS", "0"), ("MODEL_MAX_OUTPUT_TOKENS", "1.5"),
                            ("SUPPORT_MODEL_MAX_OUTPUT_TOKENS", "0"),
                            ("SUPPORT_MODEL_MAX_OUTPUT_TOKENS", "4097"),
                            ("MODEL_DAILY_COST_LIMIT", "nan"), ("MODEL_DAILY_COST_LIMIT", "1e999999999")]:
            with self.subTest(name=name, value=value), patch.dict(os.environ, {name: value}):
                self.assert_error("configuration", self.reserve, ModelBudget(self.store))

    def test_optional_cost_limit_requires_explicit_prices(self):
        budget = self.budget(MODEL_DAILY_COST_LIMIT="1")
        self.assert_error("budget_exceeded", self.reserve, budget)
        self.assertEqual(budget.summary(now=NOW)["calls"], 0)

    def test_cost_reservation_and_actual_usage_release(self):
        budget = self.budget(MODEL_DAILY_COST_LIMIT="0.025", OLLAMA_INPUT_PRICE_PER_MILLION="1",
                             OLLAMA_OUTPUT_PRICE_PER_MILLION="2")
        reservation = self.reserve(budget)
        self.assertAlmostEqual(reservation["estimated_cost"], 0.017024)
        self.assert_error("budget_exceeded", self.reserve, budget)
        budget.complete(reservation, SimpleNamespace(prompt_tokens=100, completion_tokens=20), now=NOW)
        self.assertAlmostEqual(budget.summary(now=NOW)["estimated_cost"], 0.00014)
        self.reserve(budget)

    def test_cost_price_snapshot_does_not_change_in_flight(self):
        budget = self.budget(OLLAMA_INPUT_PRICE_PER_MILLION="1", OLLAMA_OUTPUT_PRICE_PER_MILLION="2")
        reservation = self.reserve(budget)
        os.environ.update(OLLAMA_INPUT_PRICE_PER_MILLION="100", OLLAMA_OUTPUT_PRICE_PER_MILLION="200")
        budget.complete(reservation, SimpleNamespace(prompt_tokens=100, completion_tokens=20), now=NOW)
        self.assertAlmostEqual(budget.summary(now=NOW)["estimated_cost"], 0.00014)

    def test_enabling_cost_limit_after_unknown_usage_blocks_more_calls(self):
        budget = self.budget()
        self.reserve(budget)
        os.environ.update(MODEL_DAILY_COST_LIMIT="100", OLLAMA_INPUT_PRICE_PER_MILLION="1", OLLAMA_OUTPUT_PRICE_PER_MILLION="2")
        self.assert_error("budget_exceeded", self.reserve, budget)
        self.assertIsNone(budget.summary(now=NOW)["estimated_cost"])

    def test_explicit_zero_prices_are_known_zero_estimates(self):
        budget = self.budget(MODEL_DAILY_COST_LIMIT="0", OLLAMA_INPUT_PRICE_PER_MILLION="0", OLLAMA_OUTPUT_PRICE_PER_MILLION="0")
        self.reserve(budget)
        self.assertEqual(budget.summary(now=NOW)["estimated_cost"], 0)
        self.assertEqual(budget.summary(now=NOW)["cost_status"], "configured_estimate")

    def test_errors_and_summaries_do_not_store_raw_messages_or_tokens(self):
        budget = self.budget()
        reservation = self.reserve(budget)
        budget.complete(reservation, error_code="raw customer message or credential", now=NOW)
        with self.store.connection() as conn:
            rows = [dict(row) for row in conn.execute("SELECT * FROM model_budget_reservations")]
        self.assertNotIn("raw customer message", json.dumps(rows))
        self.assertNotIn(self.session["session_token"], json.dumps(rows))
        self.assertIsNone(rows[0]["error_code"])


if __name__ == "__main__":
    unittest.main()
