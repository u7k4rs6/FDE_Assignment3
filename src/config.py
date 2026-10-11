"""Runtime configuration resolved at call time (never at import time).

Everything that can differ between the public data and hidden/perturbed data is
looked up here on every call, so tests and the eval harness can point the whole
system at a temporary copy of the data via ``PROCUREMENT_DATA_DIR``.
"""
from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = ROOT / "data"

# Only used if the policy file does not state a reference date.
FALLBACK_REFERENCE_DATE = date(2026, 9, 30)

_REF_DATE_RE = re.compile(r"reference date[^0-9]*(\d{4}-\d{2}-\d{2})", re.IGNORECASE)


def data_dir() -> Path:
    override = os.getenv("PROCUREMENT_DATA_DIR")
    return Path(override).resolve() if override else DEFAULT_DATA_DIR


def runtime_dir() -> Path:
    """Local, git-ignored directory for the human-action audit log."""
    override = os.getenv("PROCUREMENT_RUNTIME_DIR")
    path = Path(override) if override else DEFAULT_DATA_DIR / "runtime"
    path.mkdir(parents=True, exist_ok=True)
    return path


def reference_date(policy_text: str | None = None) -> date:
    """Policy reference date for ALL date logic. Never ``date.today()``."""
    if policy_text is None:
        try:
            policy_text = (data_dir() / "procurement_policy.md").read_text(encoding="utf-8")
        except OSError:
            return FALLBACK_REFERENCE_DATE
    match = _REF_DATE_RE.search(policy_text)
    if not match:
        return FALLBACK_REFERENCE_DATE
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return FALLBACK_REFERENCE_DATE


def vendor_risk_base_url() -> str:
    return os.getenv("VENDOR_RISK_BASE_URL", "http://127.0.0.1:8001").rstrip("/")


def model_name() -> str:
    return os.getenv("MODEL_NAME", "claude-sonnet-5-5")


def llm_effort() -> str:
    return os.getenv("LLM_EFFORT", "medium")


def llm_timeout_seconds() -> float:
    return float(os.getenv("LLM_TIMEOUT_SECONDS", "90"))


def llm_disabled() -> bool:
    """Force the rules-only fallback (used by the control row and offline tests)."""
    return os.getenv("COPILOT_DISABLE_LLM", "").strip().lower() in {"1", "true", "yes"}
