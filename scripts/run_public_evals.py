"""Wrapper around the grader's evals/run_public_evals.py that guarantees the
mock vendor-risk API is up (and stops it afterwards if it started it).

Usage: python scripts/run_public_evals.py --architecture single|staged
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=False)

from scripts.mock_api import ensure_mock_api  # noqa: E402

if __name__ == "__main__":
    with ensure_mock_api():
        sys.argv = [str(ROOT / "evals" / "run_public_evals.py"), *sys.argv[1:]]
        runpy.run_path(sys.argv[0], run_name="__main__")
