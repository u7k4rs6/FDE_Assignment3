"""Tool-layer tests: NaN handling, data-dir override, API failure wrapping, cache."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pandas as pd
import pytest
import requests

from src import data_access as da
from src.config import reference_date
from src.tools import new_runner
from src.tools import data_tools

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def temp_data(tmp_path, monkeypatch):
    d = tmp_path / "data"
    shutil.copytree(ROOT / "data", d, ignore=shutil.ignore_patterns("runtime"))
    monkeypatch.setenv("PROCUREMENT_DATA_DIR", str(d))
    return d


def test_reference_date_parsed_from_policy():
    assert reference_date().isoformat() == "2026-09-30"


def test_reference_date_follows_policy_file(temp_data):
    p = temp_data / "procurement_policy.md"
    p.write_text(p.read_text().replace("2026-09-30", "2027-01-15"))
    assert reference_date().isoformat() == "2027-01-15"


def test_nan_cells_become_none():
    vendors = da.records(da.load_vendors())
    brand = next(v for v in vendors if v["security_review_date"] is None)
    assert brand["security_review_date"] is None
    emps = da.records(da.load_employees())
    assert any(e["manager_id"] is None for e in emps)
    assert not any(isinstance(v, float) and pd.isna(v) for e in emps for v in e.values())


def test_request_context_resolves_manager_across_departments():
    out = data_tools.get_request_context("REQ-1004")
    assert out["manager"]["department"] != out["requester"]["department"]
    assert out["department_head"] is None and out["department_head_resolution"] == "unresolved"


def test_unknown_employee(temp_data):
    reqs = json.loads((temp_data / "requests.json").read_text())
    reqs[0]["requester_id"] = "E999"
    (temp_data / "requests.json").write_text(json.dumps(reqs))
    out = data_tools.get_request_context(reqs[0]["request_id"])
    assert out["status"] == "ok" and out["requester"] is None


def test_unknown_request():
    assert data_tools.get_request_context("REQ-NOPE")["status"] == "not_found"


def test_ad_hoc_request_override():
    runner = new_runner({"request_id": "ADHOC-1", "requester_id": "E004", "product_name": "X", "vendor_name": "Y"})
    out = runner.call("get_request_context", {"request_id": "ADHOC-1"})
    assert out["status"] == "ok" and out["request"]["product_name"] == "X"


def test_check_budget_boundaries():
    dept = da.records(da.load_budgets())[0]
    avail = float(dept["available_usd"])
    assert data_tools.check_budget(dept["department"], avail)["within_budget"] is True
    over = data_tools.check_budget(dept["department"], avail + 0.01)
    assert over["within_budget"] is False and over["shortfall_usd"] == 0.01
    assert data_tools.check_budget("Go To Market", 10)["status"] == "not_found"
    assert data_tools.check_budget(dept["department"], None)["within_budget"] is None


def test_catalog_same_vendor_vs_same_category():
    out = data_tools.search_software_catalog(product="New Thing", vendor="Nobody", category="Design & Creative")
    assert out["matches"] and all(m["relation"] == "potential_competing_tool_same_category" for m in out["matches"])
    out = data_tools.search_software_catalog(product="TaskFlow Pro", vendor="TaskFlow", category="Project Management")
    assert out["matches"][0]["relation"] == "existing_vendor_expansion_or_add_on"


def test_registry_review_age():
    out = data_tools.get_vendor_registry("SignalWatch")
    assert out["review_age_days"] > 365 and out["review_current"] is False
    assert data_tools.get_vendor_registry("Unheard Of")["status"] == "not_found"


class _Resp:
    def __init__(self, code, detail="x"):
        self.status_code = code
        self._detail = detail

    def json(self):
        return {"detail": self._detail}


@pytest.mark.parametrize("exc,etype", [
    (requests.ConnectionError("down"), "connection_error"),
    (requests.Timeout("slow"), "timeout"),
    (requests.HTTPError(response=_Resp(503, "provider down")), "http_error"),
    (requests.HTTPError(response=_Resp(404, "no record")), "http_error"),
])
def test_vendor_risk_failures_become_unavailable(monkeypatch, exc, etype):
    def boom(name, timeout_seconds=3.0):
        raise exc
    monkeypatch.setattr(data_tools, "_raw_vendor_risk", boom)
    monkeypatch.setattr("time.sleep", lambda s: None)
    out = data_tools.get_vendor_risk("Anyone")
    assert out["status"] == "unavailable" and out["error_type"] == etype
    assert out["could_not_verify"]


def test_runner_never_raises_and_caches(monkeypatch):
    runner = new_runner()
    assert runner.call("no_such_tool", {})["status"] == "error"
    assert runner.call("check_budget", {"bogus": 1})["status"] == "error"
    runner.call("get_vendor_registry", {"vendor_name": "CodeMate"})
    runner.call("get_vendor_registry", {"vendor_name": "CodeMate"})
    assert sum(1 for c in runner.calls if c.name == "get_vendor_registry") == 1


def test_evaluate_policy_with_api_down(monkeypatch):
    monkeypatch.setenv("VENDOR_RISK_BASE_URL", "http://127.0.0.1:9")  # nothing listens here
    monkeypatch.setattr("time.sleep", lambda s: None)
    out = new_runner().call("evaluate_policy", {"request_id": "REQ-1001"})
    assert "vendor_risk_unavailable" in out["risk_flags"] and "Security" in out["required_approvals"]
