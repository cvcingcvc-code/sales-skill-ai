#!/usr/bin/env python3
"""Run deterministic offline regression without live credentials or network.

The runtime may deliberately have MODEL_ENABLED=false. This isolated test
process enables only mocked provider contracts; socket connections are denied.
"""

import argparse
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pattern", default="test_*.py")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
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
            suite = unittest.defaultTestLoader.discover(str(root / "tests"), pattern=args.pattern,
                                                        top_level_dir=str(root))
            result = unittest.TextTestRunner(verbosity=1 if args.quiet else 2).run(suite)
    print(f"Offline network guard: {len(blocked_attempts)} connection attempts; no live model validation.")
    return 0 if result.wasSuccessful() and not blocked_attempts else 1


if __name__ == "__main__":
    raise SystemExit(main())
