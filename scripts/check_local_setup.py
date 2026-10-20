#!/usr/bin/env python3
"""Inspect local development prerequisites without contacting a model or network.

Run from the checkout with its virtual environment. This checks configuration
and installed distribution metadata, not model availability or answer quality.
No environment values, paths, URLs, model names or credentials are emitted.
Exit 0: OFFLINE_READY. Exit 2: CONFIGURATION_REQUIRED.
"""

import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit


DISTRIBUTIONS = (
    "fastapi", "uvicorn", "streamlit", "pydantic", "sqlalchemy", "PyYAML",
    "Jinja2", "python-dotenv", "rich", "click", "httpx", "plotly",
    "google-generativeai", "anthropic",
)


def _packages_present():
    missing = []
    for name in DISTRIBUTIONS:
        try:
            importlib.metadata.version(name)
        except (importlib.metadata.PackageNotFoundError, ValueError, OSError):
            missing.append(name)
    return missing


def _database_locations_ready(environ, root):
    """Read filesystem metadata only; never create or open a database."""
    paths = []
    try:
        for name, default in (("DB_PATH", "./data/db/learners.db"),
                              ("SUPPORT_DB_PATH", "./data/db/support.db")):
            raw = environ.get(name, default).strip()
            if not raw or raw == ":memory:" or "://" in raw or "\x00" in raw:
                return False
            path = Path(raw).expanduser()
            path = (root / path if not path.is_absolute() else path).resolve()
            if path.exists() and (not path.is_file() or not os.access(path, os.R_OK | os.W_OK)):
                return False
            parent = path.parent
            while not parent.exists():
                parent = parent.parent
            if not parent.is_dir() or not os.access(parent, os.R_OK | os.W_OK | os.X_OK):
                return False
            paths.append(path)
        return paths[0] != paths[1]
    except (OSError, ValueError, RuntimeError):
        return False


def _valid_url(value, *, local_provider=False):
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme in {"http", "https"} and bool(parsed.hostname)
                 and not parsed.username and not parsed.password
                 and not parsed.query and not parsed.fragment)
        # Accessing .port also validates malformed/non-numeric port settings.
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            valid = False
        if local_provider and parsed.path not in {"", "/"}:
            valid = False
        local = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        return bool(valid), local
    except (ValueError, TypeError):
        return False, False


def inspect_setup(environ=None, root=None):
    """Return only fixed labels and booleans; caller-provided values stay private."""
    environ = os.environ if environ is None else environ
    root = Path.cwd() if root is None else Path(root)
    raw_enabled = environ.get("MODEL_ENABLED", "").strip().lower()
    enabled_valid = raw_enabled in {"true", "false"}
    enabled = raw_enabled == "true"
    provider = environ.get("LLM_PROVIDER", "").strip().lower()
    known_provider = provider in {"ollama", "deepseek"}
    missing = _packages_present()
    checks = {
        "python_3_12_or_newer": sys.version_info >= (3, 12),
        "installed_dependencies": not missing,
        "explicit_model_enabled_boolean": enabled_valid,
        "supported_provider": known_provider,
        "admin_token_present": bool(environ.get("ADMIN_API_TOKEN", "").strip()),
        "separate_writable_database_locations": _database_locations_ready(environ, root),
    }
    notes = []
    if provider == "ollama":
        valid, local = _valid_url(environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434"), local_provider=True)
        checks["provider_address_valid"] = valid
        checks["model_name_if_enabled"] = not enabled or bool(environ.get("OLLAMA_MODEL", "").strip())
        if valid and not local:
            notes.append("custom_model_address_requires_private_network_review")
    elif provider == "deepseek":
        checks["provider_address_valid"] = _valid_url(environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"))[0]
        checks["model_name_if_enabled"] = not enabled or bool(environ.get("DEEPSEEK_MODEL", "deepseek-chat").strip())
        checks["cloud_key_if_enabled"] = not enabled or bool(environ.get("DEEPSEEK_API_KEY", "").strip())
    for variable, default in (("MODEL_MAX_INPUT_TOKENS", "16000"), ("MODEL_MAX_OUTPUT_TOKENS", "4096")):
        raw = environ.get(variable, default).strip()
        checks[variable.lower() + "_valid"] = raw.isdecimal() and 0 < len(raw) <= 18 and int(raw) > 0
    notes.append("model_and_network_not_tested")
    return {
        "status": "OFFLINE_READY" if all(checks.values()) else "CONFIGURATION_REQUIRED",
        "scope": "offline_configuration_only",
        "model_execution": "INVALID" if not enabled_valid else "ENABLED_NOT_VERIFIED" if enabled else "DISABLED",
        "model_verification": "NOT_RUN",
        "network_requests": 0,
        "checks": checks,
        "missing_dependencies": missing,
        "notes": notes,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print the sanitized machine-readable report.")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    dotenv_failed = False
    try:
        # Match server precedence: existing process variables override .env.
        from dotenv import load_dotenv
        load_dotenv(root / ".env", override=False)
    except ImportError:
        pass  # The missing dependency is reported using static package names.
    except Exception:
        dotenv_failed = True
    try:
        report = inspect_setup(root=root)
    except Exception:
        report = {"status": "CONFIGURATION_REQUIRED", "scope": "offline_configuration_only",
                  "model_verification": "NOT_RUN", "network_requests": 0,
                  "checks": {"configuration_readable": False}, "missing_dependencies": [], "notes": []}
    if dotenv_failed:
        report["checks"]["dotenv_readable"] = False
        report["status"] = "CONFIGURATION_REQUIRED"
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(report["status"])
        for label, ready in report["checks"].items():
            print(f"{'OK' if ready else 'REQUIRED'}: {label}")
        if report["missing_dependencies"]:
            print("Missing dependencies: " + ", ".join(report["missing_dependencies"]))
        print("Model availability and real answers: NOT_RUN; no network requests made.")
    return 0 if report["status"] == "OFFLINE_READY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
