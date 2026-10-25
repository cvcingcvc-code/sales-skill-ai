#!/usr/bin/env python3
"""Run deterministic offline regression without live credentials or network.

The runtime may deliberately have MODEL_ENABLED=false. This isolated test
process enables only mocked provider contracts; socket connections are denied.
"""

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


TEST_GROUPS = {
    "support": (
        "test_customer_service.py", "test_support_api.py", "test_retrieval_quality.py",
        "test_preflight_budget.py", "test_data_lifecycle.py", "test_resource_controls.py",
    ),
    "training": (
        "test_roleplay.py", "test_scorer.py", "test_score_scales.py",
        "test_training_api.py", "test_training_regressions.py", "test_workflow_v2.py",
    ),
    "models": (
        "test_llm_client.py", "test_model_gateway.py", "test_model_offline.py",
        "test_model_preflight.py", "test_preflight_budget.py", "test_resource_controls.py",
    ),
    "ui": ("test_demo_v2.py", "test_streamlit_regressions.py", "test_workbench_v2.py", "test_catalog_editor.py"),
}


def _patterns(args):
    if args.group:
        if args.pattern != "test_*.py":
            raise ValueError("--group and a custom --pattern cannot be used together")
        return tuple(dict.fromkeys(pattern for group in args.group for pattern in TEST_GROUPS[group]))
    return (args.pattern,)


def _write_report(path, report):
    """Replace a complete report atomically without exposing partial JSON."""
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".offline-report-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(report, stream, sort_keys=True, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pattern", default="test_*.py")
    parser.add_argument("--group", choices=tuple(TEST_GROUPS), action="append",
                        help="Run a focused module group; repeat to combine groups.")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--fail-on-skip", action="store_true",
                        help="Fail when any test is skipped; preserves expected-failure semantics.")
    parser.add_argument("--strict", action="store_true",
                        help="CI gate: reject both skipped tests and expected failures.")
    parser.add_argument("--report-json", type=Path,
                        help="Write fixed offline status and counts, without test or environment details.")
    args = parser.parse_args(argv)
    try:
        patterns = _patterns(args)
    except ValueError as error:
        parser.error(str(error))
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    blocked_attempts = []

    def deny_network(*_args, **_kwargs):
        blocked_attempts.append(True)
        raise RuntimeError("Network connections are disabled in offline regression.")

    with tempfile.TemporaryDirectory(prefix="sales-offline-tests-") as directory:
        safe_configuration = {
            "MODEL_ENABLED": "true", "LLM_PROVIDER": "deepseek", "DEEPSEEK_API_KEY": "",
            "GOOGLE_API_KEY": "", "ANTHROPIC_API_KEY": "", "OLLAMA_MODEL": "",
            "DEEPSEEK_MODEL": "deepseek-chat", "DEEPSEEK_BASE_URL": "https://api.deepseek.com/v1",
            "OLLAMA_BASE_URL": "http://127.0.0.1:11434", "MODEL_TIMEOUT_SECONDS": "30",
            "MODEL_MAX_INPUT_TOKENS": "16000", "MODEL_MAX_OUTPUT_TOKENS": "4096",
            "MODEL_DAILY_CALL_LIMIT": "200", "MODEL_DAILY_TOKEN_LIMIT": "200000",
            "MODEL_DAILY_COST_LIMIT": "", "DEEPSEEK_INPUT_PRICE_PER_MILLION": "",
            "DEEPSEEK_OUTPUT_PRICE_PER_MILLION": "", "OLLAMA_INPUT_PRICE_PER_MILLION": "",
            "OLLAMA_OUTPUT_PRICE_PER_MILLION": "", "SUPPORT_CREATE_LIMIT_PER_HOUR": "20",
            "SUPPORT_MESSAGE_LIMIT_PER_MINUTE": "20", "SUPPORT_RETENTION_DAYS": "30",
            "ADMIN_API_TOKEN": "offline-test-credential-not-for-runtime",
            "DB_PATH": str(Path(directory) / "learners.db"),
            "SUPPORT_DB_PATH": str(Path(directory) / "support.db"),
            "SUPPORT_API_URL": "http://127.0.0.1:8080",
        }
        with patch.dict(os.environ, safe_configuration), \
                patch("socket.create_connection", side_effect=deny_network), \
                patch("socket.socket.connect", side_effect=deny_network):
            suite = unittest.TestSuite(
                unittest.defaultTestLoader.discover(str(root / "tests"), pattern=pattern,
                                                    top_level_dir=str(root))
                for pattern in patterns
            )
            result = unittest.TextTestRunner(verbosity=1 if args.quiet else 2).run(suite)
    print(f"Offline network guard: {len(blocked_attempts)} connection attempts; no live model validation.")
    fail_on_skip = args.fail_on_skip or args.strict
    successful = (result.testsRun > 0 and result.wasSuccessful() and not blocked_attempts
                  and (not fail_on_skip or not result.skipped)
                  and (not args.strict or not result.expectedFailures))
    report = {
        "scope": "offline_mock_regression", "model_validation": "NOT_RUN",
        "status": "PASS" if successful else "FAIL", "strict": args.strict, "fail_on_skip": fail_on_skip,
        "run": result.testsRun, "fail": len(result.failures), "error": len(result.errors),
        "skipped": len(result.skipped), "expected_failures": len(result.expectedFailures),
        "unexpected_successes": len(result.unexpectedSuccesses),
        "network_attempts": len(blocked_attempts),
    }
    if not result.testsRun:
        print("Offline regression failed: no tests ran.", file=sys.stderr)
    if fail_on_skip and result.skipped:
        print("Offline regression failed: skipped tests are not allowed.", file=sys.stderr)
    if args.strict and result.expectedFailures:
        print("Offline regression failed: expected failures are not allowed in strict mode.", file=sys.stderr)
    if args.report_json is not None:
        # Fixed fields only: no names, paths, exceptions, customer data or secrets.
        try:
            _write_report(args.report_json, report)
        except (OSError, ValueError):
            report["status"] = "FAIL"
            print(json.dumps(report, sort_keys=True))
            print("Offline regression failed: JSON report could not be written.", file=sys.stderr)
            return 1
        print(json.dumps(report, sort_keys=True))
    return 0 if successful else 1


if __name__ == "__main__":
    raise SystemExit(main())
