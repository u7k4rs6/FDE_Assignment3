"""Deterministic data tools. Each returns a structured dict and never raises."""
from __future__ import annotations

import re
from datetime import date

import requests

from src import data_access as da
from src.config import reference_date
from src.tools.runtime import ToolContext
from src.vendor_client import get_vendor_risk as _raw_vendor_risk


def _norm(text: object) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def _parse_date(value: object) -> date | None:
    value = da.clean(value)
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _employee_summary(row: dict | None) -> dict | None:
    if not row:
        return None
    return {k: row.get(k) for k in ("employee_id", "name", "department", "level", "manager_id", "country")}


# --------------------------------------------------------------------------- #
# 1. get_request_context
# --------------------------------------------------------------------------- #
def get_request_context(request_id: str, ctx: ToolContext | None = None) -> dict:
    """Request + requester + manager + department head + department budget row."""
    override = ctx.request_override if ctx else None
    if override and override.get("request_id") == request_id:
        request = dict(override)
    else:
        try:
            request = da.get_request(request_id)
        except KeyError:
            return {"status": "not_found", "reason": f"No request with id '{request_id}'"}

    employees = da.records(da.load_employees())
    by_id = {e["employee_id"]: e for e in employees if e.get("employee_id")}
    requester = by_id.get(da.clean(request.get("requester_id")) or "")
    notes: list[str] = []

    manager = None
    department_head = None
    dh_resolution = "unresolved"
    budget_row = None
    if requester is None:
        notes.append(f"Requester '{request.get('requester_id')}' not found in the employee directory.")
    else:
        manager = by_id.get(requester.get("manager_id") or "")
        if manager is None:
            notes.append("Requester has no manager on record; Manager approver is unresolved.")
        dept = requester.get("department")
        # Department Head = a Director *of the requester's own department*.
        # Managers can sit in other departments (e.g. a director of one org
        # managing reports in two other departments), so the manager is NOT assumed
        # to be the department head.
        directors = [e for e in employees if e.get("department") == dept and _norm(e.get("level")) == "director"]
        if len(directors) == 1:
            department_head = directors[0]
            dh_resolution = "resolved"
            if department_head["employee_id"] == requester["employee_id"]:
                dh_resolution = "requester_is_department_head"
                notes.append("Requester is the department head; self-approval is not allowed, escalate to their manager.")
        elif len(directors) > 1:
            dh_resolution = "ambiguous"
            notes.append(f"Multiple Directors found in {dept}; Department Head approver is ambiguous.")
        else:
            notes.append(f"No Director found for department '{dept}'; Department Head approver is unresolved.")
        budgets = da.records(da.load_budgets())
        budget_row = next((b for b in budgets if b.get("department") == dept), None)
        if budget_row is None:
            notes.append(f"No software budget row for department '{dept}'.")

    return {
        "status": "ok",
        "reference_date": reference_date().isoformat(),
        "request": request,
        "requester": _employee_summary(requester),
        "manager": _employee_summary(manager),
        "department_head": _employee_summary(department_head),
        "department_head_resolution": dh_resolution,
        "department_budget": budget_row,
        "notes": notes,
    }


# --------------------------------------------------------------------------- #
# 2. check_budget (deterministic)
# --------------------------------------------------------------------------- #
def check_budget(department: str, annual_cost: float | None = None, ctx: ToolContext | None = None) -> dict:
    budgets = da.records(da.load_budgets())
    row = next((b for b in budgets if _norm(b.get("department")) == _norm(department)), None)
    cost = da.to_number(annual_cost)
    if row is None:
        return {"status": "not_found", "department": department, "annual_cost_usd": cost, "within_budget": None,
                "reason": f"No software budget row for department '{department}'; budget cannot be verified."}
    available = da.to_number(row.get("available_usd"))
    result = {
        "status": "ok",
        "department": row.get("department"),
        "annual_software_budget_usd": da.to_number(row.get("annual_software_budget_usd")),
        "committed_usd": da.to_number(row.get("committed_usd")),
        "available_usd": available,
        "annual_cost_usd": cost,
    }
    if cost is None or available is None:
        result.update(within_budget=None, reason="Annual cost (or available budget) is missing; budget cannot be checked.")
        return result
    within = cost <= available
    result.update(
        within_budget=within,
        remaining_after_usd=round(available - cost, 2) if within else 0.0,
        shortfall_usd=0.0 if within else round(cost - available, 2),
        reason=("Cost is within available budget (a positive budget check is not an approval)." if within
                else "Cost exceeds available budget; Finance/budget exception review required."),
    )
    return result


# --------------------------------------------------------------------------- #
# 3. search_software_catalog (deterministic matching + candidates for the LLM)
# --------------------------------------------------------------------------- #
_STOP = {"the", "and", "for", "with", "to", "of", "a", "an", "in", "on", "our", "team", "teams", "needs", "need",
         "wants", "want", "new", "tool", "users", "user", "software"}


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z][a-z0-9]+", _norm(text)) if t not in _STOP and len(t) > 2}


def _name_in(needle: str, haystack: str) -> bool:
    needle, haystack = _norm(needle), _norm(haystack)
    return bool(needle) and re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", haystack) is not None


def search_software_catalog(product: str | None = None, vendor: str | None = None, category: str | None = None,
                            use_case: str | None = None, department: str | None = None,
                            ctx: ToolContext | None = None) -> dict:
    catalog = da.records(da.load_software_catalog())
    history = da.records(da.load_purchase_history())
    matches, candidates = [], []
    use_tokens = _tokens(use_case or "")
    for item in catalog:
        match_types = []
        if product and (_norm(item.get("product_name")) == _norm(product)
                        or _name_in(item.get("product_name") or "", product)
                        or _name_in(product, item.get("product_name") or "")):
            match_types.append("same_product_family")
        if vendor and _norm(item.get("vendor_name")) == _norm(vendor):
            match_types.append("same_vendor")
        if category and _norm(item.get("category")) == _norm(category):
            match_types.append("same_category")
        scope = item.get("scope") or ""
        covers = _norm(scope) == "company-wide" or (department is not None and _norm(scope) == _norm(department))
        summary = {
            "software_id": item.get("software_id"),
            "product_name": item.get("product_name"),
            "vendor_name": item.get("vendor_name"),
            "category": item.get("category"),
            "status": item.get("status"),
            "licensed_seats": da.to_number(item.get("licensed_seats")),
            "annual_cost_usd": da.to_number(item.get("annual_cost_usd")),
            "scope": scope,
            "covers_requester_department": covers,
            "notes": item.get("notes"),
        }
        if match_types:
            same_vendor = "same_vendor" in match_types or "same_product_family" in match_types
            summary["match_types"] = match_types
            summary["relation"] = ("existing_vendor_expansion_or_add_on" if same_vendor
                                   else "potential_competing_tool_same_category")
            matches.append(summary)
        else:
            overlap = use_tokens & _tokens(" ".join(str(item.get(k) or "") for k in ("product_name", "category", "notes")))
            if overlap:
                candidates.append({**summary, "keyword_overlap": sorted(overlap)})
    candidates.sort(key=lambda c: -len(c["keyword_overlap"]))
    vendor_history = [h for h in history if vendor and _norm(h.get("vendor_name")) == _norm(vendor)]
    for h in vendor_history:
        h["annual_amount_usd"] = da.to_number(h.get("annual_amount_usd"))
    return {
        "status": "ok",
        "query": {"product": product, "vendor": vendor, "category": category, "department": department},
        "matches": matches,
        "possible_use_case_matches": candidates[:5],
        "vendor_purchase_history": vendor_history,
        "note": ("Deterministic matches are on product family, vendor and category. "
                 "'possible_use_case_matches' are keyword candidates only and need judgement."),
    }


# --------------------------------------------------------------------------- #
# 4. get_vendor_registry (internal vendors.csv)
# --------------------------------------------------------------------------- #
def _review_age(review_date: date | None, ref: date) -> dict:
    if review_date is None:
        return {"review_age_days": None, "review_current": None, "review_date_in_future": False}
    age = (ref - review_date).days
    return {
        "review_age_days": age,
        # "considered current for 365 days from its review date" -> day 365 is still current.
        "review_current": 0 <= age <= 365,
        "review_date_in_future": age < 0,
    }


def get_vendor_registry(vendor_name: str, ctx: ToolContext | None = None) -> dict:
    ref = reference_date()
    vendors = da.records(da.load_vendors())
    row = next((v for v in vendors if _norm(v.get("vendor_name")) == _norm(vendor_name)), None)
    if row is None:
        return {"status": "not_found", "vendor_name": vendor_name, "reference_date": ref.isoformat(),
                "is_new_vendor": True,
                "reason": f"'{vendor_name}' is not in the internal vendor registry (treated as a new vendor)."}
    review_date = _parse_date(row.get("security_review_date"))
    return {
        "status": "ok",
        "reference_date": ref.isoformat(),
        "record": row,
        "is_new_vendor": _norm(row.get("procurement_status")) != "approved",
        **_review_age(review_date, ref),
    }


# --------------------------------------------------------------------------- #
# 5. get_vendor_risk (external mock API, wrapped)
# --------------------------------------------------------------------------- #
def get_vendor_risk(vendor_name: str, ctx: ToolContext | None = None) -> dict:
    """Wraps the raw client: 404/503/timeouts/connection errors become a
    structured ``unavailable`` result instead of an exception (policy §10)."""
    ref = reference_date()
    last_error: dict = {}
    for attempt in range(2):  # one quick retry for transient failures
        try:
            record = _raw_vendor_risk(vendor_name)
            review_date = _parse_date(record.get("last_review_date"))
            return {"status": "ok", "reference_date": ref.isoformat(), "record": record,
                    **_review_age(review_date, ref)}
        except requests.HTTPError as exc:
            code = exc.response.status_code if exc.response is not None else None
            detail = None
            try:
                detail = exc.response.json().get("detail") if exc.response is not None else None
            except ValueError:
                pass
            last_error = {"error_type": "http_error", "http_status": code,
                          "reason": detail or f"HTTP {code} from vendor-risk service"}
            if code == 404:
                last_error["reason"] = detail or "No vendor-risk record for this vendor"
                break
        except requests.Timeout:
            last_error = {"error_type": "timeout", "reason": "Vendor-risk service timed out"}
        except requests.ConnectionError:
            last_error = {"error_type": "connection_error", "reason": "Vendor-risk service is unreachable"}
        except (requests.RequestException, ValueError) as exc:
            last_error = {"error_type": type(exc).__name__, "reason": str(exc)}
        if attempt == 0:
            import time
            time.sleep(0.2)
    return {"status": "unavailable", "vendor_name": vendor_name, "reference_date": ref.isoformat(),
            "could_not_verify": ["vendor security review status", "vendor review date",
                                 "personal-data processing", "data residency (outside-region storage)"],
            **last_error}
