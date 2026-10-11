from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

from src.config import ROOT, data_dir  # noqa: F401  (ROOT kept for backwards compatibility)

# NOTE: the data directory is resolved on every call (see src/config.py) so the
# eval harness can point the system at perturbed copies via PROCUREMENT_DATA_DIR.


def _read_csv(name: str) -> pd.DataFrame:
    # dtype=str keeps zero-padded IDs intact and avoids float coercion of
    # integer columns that contain blanks; numbers are parsed explicitly later.
    return pd.read_csv(data_dir() / name, dtype=str, keep_default_na=True)


def clean(value: Any) -> Any:
    """Normalise pandas/JSON blanks to None (NaN, NaT, '', whitespace)."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if value is pd.NaT:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    if isinstance(value, str):
        return value.strip()
    return value


def records(df: pd.DataFrame) -> list[dict]:
    return [{k: clean(v) for k, v in row.items()} for row in df.to_dict(orient="records")]


def to_number(value: Any) -> float | None:
    value = clean(value)
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).replace(",", "").replace("$", ""))
    except ValueError:
        return None
    return None if math.isnan(number) else number


def load_employees() -> pd.DataFrame:
    return _read_csv("employees.csv")


def load_budgets() -> pd.DataFrame:
    return _read_csv("department_budgets.csv")


def load_software_catalog() -> pd.DataFrame:
    return _read_csv("software_catalog.csv")


def load_vendors() -> pd.DataFrame:
    return _read_csv("vendors.csv")


def load_purchase_history() -> pd.DataFrame:
    return _read_csv("purchase_history.csv")


def load_requests() -> list[dict]:
    return json.loads((data_dir() / "requests.json").read_text(encoding="utf-8"))


def get_request(request_id: str) -> dict:
    for request in load_requests():
        if request.get("request_id") == request_id:
            return request
    raise KeyError(f"Unknown request_id: {request_id}")


def load_policy_text() -> str:
    return (data_dir() / "procurement_policy.md").read_text(encoding="utf-8")


def policy_path() -> Path:
    return data_dir() / "procurement_policy.md"
