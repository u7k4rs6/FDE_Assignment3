"""Deterministic procurement policy engine.

Pure function over an evidence bundle (the outputs of the data tools). It
produces the *floor* that no LLM output can lower: required approvals, risk
flags, missing information, the policy section behind each rule, resolved
approvers, and evidence findings that cite the tool each fact came from.

Nothing here knows about specific request IDs, vendors or products; every
decision is derived from data values and the policy text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from src.data_access import clean, to_number
from src.injection import has_real_purpose, scan_fields

CANONICAL_APPROVALS = ["Manager", "Department Head", "Procurement", "Finance", "CFO", "Security", "Privacy", "Legal"]
FINANCIAL_APPROVALS = {"Manager", "Department Head", "Finance", "CFO"}  # code-owned; LLM cannot add these

UNKNOWN_TOKENS = {"", "unknown", "tbd", "tba", "n/a", "na", "null", "none?", "?", "not sure", "unsure", "unspecified"}


# --------------------------------------------------------------------------- #
# Small helpers (exported for unit tests)
# --------------------------------------------------------------------------- #
def is_unknown(value: object) -> bool:
    value = clean(value)
    return value is None or (isinstance(value, str) and value.strip().lower() in UNKNOWN_TOKENS)


def money(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"${value:,.2f}" if value != int(value) else f"${int(value):,}"


def financial_approvals(cost: float | None) -> list[str] | None:
    """Policy §4 thresholds on the annualised amount. None = cannot compute."""
    if cost is None:
        return None
    cost = round(float(cost), 2)
    if cost <= 1000:
        return ["Manager"]
    if cost <= 10000:
        return ["Department Head", "Procurement"]
    if cost <= 25000:
        return ["Department Head", "Finance", "Procurement"]
    return ["Department Head", "Finance", "CFO", "Procurement"]


def cost_tier_label(cost: float | None) -> str:
    if cost is None:
        return "unknown (annual cost missing)"
    cost = round(float(cost), 2)
    if cost <= 1000:
        return "up to $1,000"
    if cost <= 10000:
        return "$1,000.01 - $10,000"
    if cost <= 25000:
        return "$10,000.01 - $25,000"
    return "above $25,000"


def _slug(text: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")


def classify_data_level(level: object) -> dict:
    """Map a free-text data-access level to policy-relevant categories."""
    s = _slug(level)
    cats = set()
    if "source" in s and "code" in s:
        cats.add("source_code")
    if "confidential" in s:
        cats.add("confidential_documents")
    if "pii" in s or "personal" in s:
        cats.add("employee_pii" if "employee" in s or "hr" in s.split("_") else "customer_pii"
                 if "customer" in s or "client" in s else "pii")
    if "credential" in s or "secret" in s or "password" in s:
        cats.add("credentials_secrets")
    if "production" in s or s.startswith("prod_"):
        cats.add("production_access")
    return {"slug": s, "categories": sorted(cats),
            "security_sensitive": bool(cats), "pii": any("pii" in c for c in cats)}


_PROD_RE = re.compile(r"\b(?:production|prod|cloud[- ]?account|aws|azure|gcp|google cloud|kubernetes|k8s|infrastructure)\b", re.I)
_CODE_RE = re.compile(r"\b(?:git|github|gitlab|bitbucket|source[- ]code|code repositor(?:y|ies))\b", re.I)
_SECRET_RE = re.compile(r"\b(?:secrets?|vault|credentials?|passwords?|key management|kms)\b", re.I)
_HR_RE = re.compile(r"\b(?:hris|payroll|workday|employee records?)\b", re.I)


def classify_integrations(integrations: object) -> dict:
    items = [str(i) for i in integrations] if isinstance(integrations, list) else []
    hits = {"production_or_cloud": [], "source_code": [], "credentials_secrets": [], "employee_data": []}
    for i in items:
        if _PROD_RE.search(i):
            hits["production_or_cloud"].append(i)
        if _CODE_RE.search(i):
            hits["source_code"].append(i)
        if _SECRET_RE.search(i):
            hits["credentials_secrets"].append(i)
        if _HR_RE.search(i):
            hits["employee_data"].append(i)
    return hits


_STATUS_MAP = {
    "approved": "approved", "approve": "approved", "current": "approved", "pass": "approved", "passed": "approved",
    "expired": "expired", "stale": "expired", "lapsed": "expired",
    "pending": "not_completed", "not_completed": "not_completed", "in_progress": "not_completed",
    "under_review": "not_completed", "incomplete": "not_completed", "not_started": "not_completed",
    "rejected": "rejected", "denied": "rejected", "failed": "rejected",
}


def canonical_security_status(value: object) -> str:
    if is_unknown(value):
        return "unknown"
    return _STATUS_MAP.get(_slug(value), "unknown")


# --------------------------------------------------------------------------- #
# Result container
# --------------------------------------------------------------------------- #
@dataclass
class PolicyResult:
    required_approvals: list[str] = field(default_factory=list)
    approval_reasons: list[dict] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    flag_reasons: list[dict] = field(default_factory=list)
    missing_information: list[str] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    present_fields: list[str] = field(default_factory=list)
    findings: list[dict] = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)
    injection_hits: list[dict] = field(default_factory=list)
    approver_details: list[dict] = field(default_factory=list)
    overlap: list[dict] = field(default_factory=list)
    approvals_computable: bool = True
    cost_tier: str = ""
    recommendation_hint: str = ""
    next_step_hint: str = ""
    reference_date: str = ""

    # -- builders ---------------------------------------------------------
    def approve(self, name: str, reason: str, section: str) -> None:
        if name not in self.required_approvals:
            self.required_approvals.append(name)
        self.approval_reasons.append({"approval": name, "reason": reason, "policy_section": section})

    def flag(self, name: str, reason: str, section: str) -> None:
        if name not in self.risk_flags:
            self.risk_flags.append(name)
        self.flag_reasons.append({"flag": name, "reason": reason, "policy_section": section})

    def find(self, source: str, finding: str, reference: str | None = None) -> None:
        self.findings.append({"source": source, "finding": finding, "reference": reference})

    def missing(self, field_key: str, label: str) -> None:
        if field_key not in self.missing_fields:
            self.missing_fields.append(field_key)
            self.missing_information.append(label)

    def as_dict(self) -> dict:
        self.required_approvals.sort(key=CANONICAL_APPROVALS.index)
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


# --------------------------------------------------------------------------- #
# Main entry point
# --------------------------------------------------------------------------- #
def evaluate(bundle: dict, ref_date: date) -> PolicyResult:
    """``bundle`` keys: context, budget, catalog, registry, vendor_risk (tool outputs; any may be None)."""
    r = PolicyResult(reference_date=ref_date.isoformat())
    ctx = bundle.get("context") or {}
    if ctx.get("status") != "ok":
        r.approvals_computable = False
        r.approve("Procurement", "Request could not be loaded; manual triage required.", "§1, §10")
        r.flag("missing_information", ctx.get("reason", "Request not found"), "§1")
        r.missing("request", "request record (not found)")
        r.find("get_request_context", ctx.get("reason", "Request could not be loaded."), "§1")
        r.recommendation_hint = "Manual review - request could not be loaded"
        r.next_step_hint = "Procurement to confirm the request ID and resubmit; no approval routing is possible."
        return r

    req = ctx.get("request") or {}
    requester = ctx.get("requester")
    budget = bundle.get("budget") or {}
    catalog = bundle.get("catalog") or {}
    registry = bundle.get("registry") or {}
    risk = bundle.get("vendor_risk") or {}
    rid = req.get("request_id")

    # ---------------- §1 required information -------------------------------
    cost = to_number(req.get("annual_cost_usd"))
    if cost is not None and cost < 0:
        cost = None
    users = to_number(req.get("user_count"))
    if requester is None:
        r.missing("requester", "requester (employee record not found)")
    elif is_unknown(requester.get("department")):
        r.missing("requester", "requester department")
    if is_unknown(req.get("product_name")) and is_unknown(req.get("vendor_name")):
        r.missing("product", "product/vendor name")
    elif is_unknown(req.get("vendor_name")):
        r.missing("product", "vendor name")
    if cost is None:
        r.missing("annual_cost", "annual cost (or a reasonable annual estimate)")
    if users is None or users <= 0:
        r.missing("user_count", "number of users/licenses")
    if not has_real_purpose(req.get("business_justification")):
        r.missing("purpose", "business purpose (justification has no concrete business need)")
    if is_unknown(req.get("data_access_level")):
        r.missing("data_access_level", "intended data access level")
    integrations = req.get("requested_integrations")
    if integrations is None or (isinstance(integrations, list) and any(is_unknown(i) for i in integrations)) \
            or (not isinstance(integrations, list) and is_unknown(integrations)):
        r.missing("integrations", "required integrations")
    all_fields = ["requester", "product", "annual_cost", "user_count", "purpose", "data_access_level", "integrations"]
    r.present_fields = [f for f in all_fields if f not in r.missing_fields]
    if r.missing_information:
        r.flag("missing_information", "Missing: " + "; ".join(r.missing_information), "§1")
        r.find("get_request_context", f"Request {rid} is missing required information: "
               + "; ".join(r.missing_information) + ".", "Policy §1")
    else:
        r.find("get_request_context", f"Request {rid} contains all required fields (§1); "
               f"{'no integrations requested' if integrations == [] else 'integrations: ' + ', '.join(map(str, integrations))}.",
               "Policy §1")

    # ---------------- §9 prompt injection over ALL untrusted text ------------
    untrusted = {f"request.{k}": v for k, v in req.items() if k not in {"request_id", "requester_id"}}
    reg_rec = registry.get("record") or {}
    if reg_rec.get("notes"):
        untrusted["vendor_registry.notes"] = reg_rec.get("notes")
    risk_rec = risk.get("record") or {}
    if risk_rec.get("notes"):
        untrusted["vendor_risk_api.notes"] = risk_rec.get("notes")
    for m in catalog.get("matches", []) + catalog.get("possible_use_case_matches", []):
        if m.get("notes"):
            untrusted[f"catalog.{m.get('software_id')}.notes"] = m.get("notes")
    for h in catalog.get("vendor_purchase_history", []):
        if h.get("notes"):
            untrusted[f"purchase_history.{h.get('purchase_id')}.notes"] = h.get("notes")
    r.injection_hits = scan_fields(untrusted)
    if r.injection_hits:
        fields = sorted({h["field"] for h in r.injection_hits})
        r.flag("prompt_injection_detected",
               f"Instruction-like text found in untrusted data ({', '.join(fields)}); ignored.", "§9")
        src_tool = "get_request_context" if any(f.startswith("request.") for f in fields) else (
            "get_vendor_registry" if any(f.startswith("vendor_registry") for f in fields) else
            "get_vendor_risk" if any(f.startswith("vendor_risk") for f in fields) else "search_software_catalog")
        r.find(src_tool, "Untrusted text attempts to change the review (e.g. \""
               + r.injection_hits[0]["snippet"] + "\"). The instruction was ignored; real policy applied.", "Policy §9")

    # Cost-basis ambiguity (e.g. "one-time" wording on an annual field)
    just = str(req.get("business_justification") or "")
    if cost is not None and re.search(r"\bone[- ]?(?:time|off)\b|\bper month\b|\bmonthly\b|\bquarterly\b", just, re.I):
        r.find("get_request_context",
               f"Justification wording (\"{re.search(r'(one[- ]?(?:time|off)|per month|monthly|quarterly)', just, re.I).group(0)}\") "
               f"is ambiguous about the cost basis; §4 thresholds were applied to the stated annual amount {money(cost)}. "
               "Confirm whether the spend recurs.", "Policy §4")

    # ---------------- §4 financial thresholds --------------------------------
    tier = financial_approvals(cost)
    r.cost_tier = cost_tier_label(cost)
    if tier is None:
        r.approvals_computable = False
        r.approve("Procurement", "Annual cost missing: approval tier cannot be computed; Procurement triage.", "§4, §1")
        r.find("evaluate_policy", "Annual cost is missing, so the §4 approval tier cannot be computed yet; "
               "routed to Procurement for triage until the requester supplies it.", "Policy §4")
    else:
        for a in tier:
            r.approve(a, f"Annual cost {money(cost)} falls in tier {r.cost_tier}.", "§4")
        r.find("evaluate_policy", f"Annual cost {money(cost)} is in the {r.cost_tier} tier, so the minimum "
               f"business approvals are: {', '.join(tier)}.", "Policy §4")

    # ---------------- §2 budget ---------------------------------------------
    if budget.get("status") == "ok" and budget.get("within_budget") is not None:
        if budget["within_budget"]:
            r.find("check_budget", f"{budget['department']} available software budget is "
                   f"{money(budget['available_usd'])}; the request ({money(budget['annual_cost_usd'])}) fits, "
                   f"leaving {money(budget['remaining_after_usd'])}. A positive budget check is not an approval.",
                   "Policy §2")
        else:
            r.approve("Finance", "Cost exceeds available department budget.", "§2")
            r.flag("budget_insufficient", f"Cost {money(budget['annual_cost_usd'])} exceeds available "
                   f"{money(budget['available_usd'])}.", "§2")
            r.find("check_budget", f"Request cost {money(budget['annual_cost_usd'])} exceeds {budget['department']}'s "
                   f"available software budget of {money(budget['available_usd'])} (shortfall "
                   f"{money(budget['shortfall_usd'])}); Finance budget-exception review required.", "Policy §2")
    elif budget.get("status") == "not_found":
        r.approve("Finance", "Department budget could not be verified.", "§2, §10")
        r.flag("budget_unverified", budget.get("reason", "No budget row"), "§2, §10")
        r.unverified.append("department software budget")
        r.find("check_budget", budget.get("reason", "Department budget could not be verified."), "Policy §2, §10")
    elif cost is None:
        r.unverified.append("budget fit (annual cost missing)")
    else:  # no department known (e.g. requester not in directory): budget cannot be checked
        r.approve("Finance", "Department budget could not be checked (requester department unknown).", "§2, §10")
        r.flag("budget_unverified", "Requester department unknown; budget not checked.", "§2, §10")
        r.unverified.append("department software budget")
        r.find("evaluate_policy", "Budget could not be checked because the requester's department is unknown; "
               "no favourable budget status is assumed.", "Policy §2, §10")

    # ---------------- vendor evidence (registry + API) -----------------------
    vendor = req.get("vendor_name")
    reg_ok = registry.get("status") == "ok"
    risk_ok = risk.get("status") == "ok"
    is_new = bool(registry.get("is_new_vendor")) if registry else True
    security_reasons: list[str] = []

    if registry.get("status") == "not_found":
        security_reasons.append("vendor not in internal registry (no security assessment on file)")
        r.find("get_vendor_registry", f"{vendor} is not in the internal vendor registry; treated as a new vendor "
               "with no security assessment or approved terms on file.", "Policy §5, §7")
    elif reg_ok:
        reg_status = canonical_security_status(reg_rec.get("security_status"))
        age = registry.get("review_age_days")
        r.find("get_vendor_registry",
               f"Registry: {vendor} procurement status {reg_rec.get('procurement_status')}, security "
               f"{reg_rec.get('security_status')}"
               + (f" (reviewed {reg_rec.get('security_review_date')}, {age} days before the reference date)"
                  if age is not None else " (no review date)")
               + f", legal terms {reg_rec.get('legal_terms_status') or 'missing'}.", "vendors.csv / Policy §5")
        if reg_status != "approved":
            security_reasons.append(f"registry security status is {reg_rec.get('security_status') or 'missing'}")
        if age is None:
            security_reasons.append("registry has no security review date")
        elif registry.get("review_date_in_future"):
            security_reasons.append("registry review date is after the reference date (data error)")
        elif not registry.get("review_current"):
            security_reasons.append(f"registry review is {age} days old (>365)")
            r.flag("vendor_review_expired", f"Registry review dated {reg_rec.get('security_review_date')} is "
                   f"{age} days old (current for 365 days).", "§5")
    else:
        security_reasons.append("vendor registry not checked")
        r.unverified.append("internal vendor registry record")

    if risk_ok:
        api_status = canonical_security_status(risk_rec.get("security_review_status"))
        api_age = risk.get("review_age_days")
        r.find("get_vendor_risk",
               f"Vendor-risk API: {vendor} risk {risk_rec.get('risk_level')}, review status "
               f"{risk_rec.get('security_review_status')}"
               + (f" (last review {risk_rec.get('last_review_date')}, {api_age} days old)" if api_age is not None
                  else " (no review date)")
               + f"; processes personal data: {risk_rec.get('processes_personal_data')}; stores data outside region: "
               f"{risk_rec.get('stores_data_outside_region')}.", "/vendor-risk API / Policy §5")
        if api_status == "expired":
            r.flag("vendor_review_expired", "Vendor-risk service reports the assessment as expired.", "§5")
        if api_status != "approved":
            security_reasons.append(f"vendor-risk assessment status is {risk_rec.get('security_review_status') or 'missing'}")
        if api_age is None:
            security_reasons.append("vendor-risk service has no review date")
        elif risk.get("review_date_in_future"):
            security_reasons.append("vendor-risk review date is after the reference date (data error)")
        elif not risk.get("review_current"):
            security_reasons.append(f"vendor-risk review is {api_age} days old (>365)")
            r.flag("vendor_review_expired", f"Vendor-risk review dated {risk_rec.get('last_review_date')} is "
                   f"{api_age} days old.", "§5")
    else:
        reason = risk.get("reason", "vendor-risk lookup not performed")
        r.flag("vendor_risk_unavailable", f"Vendor-risk service unavailable: {reason}.", "§10")
        security_reasons.append("vendor-risk assessment could not be retrieved")
        could_not = risk.get("could_not_verify") or ["vendor security review status", "data residency"]
        r.unverified.extend(could_not)
        r.find("get_vendor_risk", f"Vendor-risk lookup for {vendor} failed ({reason}"
               + (f", HTTP {risk.get('http_status')}" if risk.get("http_status") else "")
               + "). Not verified: " + ", ".join(could_not) + ". No favourable status is assumed; manual review required.",
               "Policy §10")

    # Conflict between registry and API (do not silently pick one)
    if reg_ok and risk_ok:
        reg_status = canonical_security_status(reg_rec.get("security_status"))
        api_status = canonical_security_status(risk_rec.get("security_review_status"))
        reg_date, api_date = clean(reg_rec.get("security_review_date")), clean(risk_rec.get("last_review_date"))
        diffs = []
        if "unknown" not in (reg_status, api_status) and reg_status != api_status:
            diffs.append(f"status registry={reg_rec.get('security_status')} vs API={risk_rec.get('security_review_status')}")
        if reg_date and api_date and str(reg_date)[:10] != str(api_date)[:10]:
            diffs.append(f"review date registry={reg_date} vs API={api_date}")
        if diffs:
            r.flag("conflicting_vendor_evidence", "; ".join(diffs), "§5")
            security_reasons.append("registry and vendor-risk service disagree")
            r.find("evaluate_policy", f"Registry and vendor-risk service disagree for {vendor} ({'; '.join(diffs)}). "
                   "Neither source is silently preferred; Security must reconcile.", "Policy §5")

    # Registry has no record of a vendor that the vendor-risk service has assessed:
    # the two sources disagree about whether the vendor is known/assessed.
    if registry.get("status") == "not_found" and risk_ok:
        detail = (f"registry has no record of {vendor} but the vendor-risk service reports review status "
                  f"{risk_rec.get('security_review_status')} ({risk_rec.get('last_review_date') or 'no date'})")
        r.flag("conflicting_vendor_evidence", detail, "§5")
        security_reasons.append("registry and vendor-risk service disagree")
        r.find("evaluate_policy", f"Registry and vendor-risk service disagree: {detail}. Neither source is silently "
               "preferred; Security and Procurement must reconcile the vendor record.", "Policy §5")

    # ---------------- §5 security -------------------------------------------
    data = classify_data_level(req.get("data_access_level"))
    integ = classify_integrations(integrations)
    data_reasons = []
    labels = {"source_code": "source code access", "confidential_documents": "confidential documents",
              "customer_pii": "customer PII", "employee_pii": "employee PII", "pii": "personal data (PII)",
              "credentials_secrets": "credentials/secrets", "production_access": "production data access"}
    data_reasons += [labels[c] for c in data["categories"]]
    if integ["production_or_cloud"]:
        data_reasons.append("production/cloud-account integration (" + ", ".join(integ["production_or_cloud"]) + ")")
    if integ["source_code"] and "source_code" not in data["categories"]:
        data_reasons.append("source-code integration (" + ", ".join(integ["source_code"]) + ")")
    if integ["credentials_secrets"]:
        data_reasons.append("credentials/secrets integration (" + ", ".join(integ["credentials_secrets"]) + ")")
    all_security = data_reasons + security_reasons
    if all_security:
        r.approve("Security", "; ".join(all_security), "§5")
        r.flag("security_review_required", "; ".join(all_security), "§5")
        r.find("evaluate_policy", "Security review required: " + "; ".join(all_security) + ".", "Policy §5")
    sensitive_in_scope = data["security_sensitive"] or bool(integ["production_or_cloud"] or integ["source_code"]
                                                             or integ["credentials_secrets"])

    # §8 AI / limited-use approvals do not extend to sensitive data
    limited = [m for m in catalog.get("matches", [])
               if "limited" in str(m.get("status") or "").lower() and "same_vendor" in m.get("match_types", [])]
    if limited and sensitive_in_scope:
        item = limited[0]
        r.flag("limited_use_scope_exceeded", f"{item['product_name']} is '{item['status']}' but the request involves "
               + ", ".join(data_reasons) + ".", "§8")
        r.find("search_software_catalog", f"{item['product_name']} ({item['software_id']}) is catalogued as "
               f"'{item['status']}' ({item.get('notes')}); that approval does not extend to "
               + ", ".join(data_reasons) + ".", "Policy §8")

    # ---------------- §6 privacy --------------------------------------------
    privacy_reasons = []
    if data["pii"]:
        privacy_reasons.append([labels[c] for c in data["categories"] if "pii" in c][0])
    if integ["employee_data"]:
        privacy_reasons.append("employee-data integration (" + ", ".join(integ["employee_data"]) + ")")
    outside = risk_rec.get("stores_data_outside_region") is True if risk_ok else None
    if outside and sensitive_in_scope:
        privacy_reasons.append("vendor stores data outside the operating region while sensitive data is in scope")
    if privacy_reasons:
        r.approve("Privacy", "; ".join(privacy_reasons), "§6")
        r.flag("privacy_review_required", "; ".join(privacy_reasons), "§6")
        r.find("evaluate_policy", "Privacy review required: " + "; ".join(privacy_reasons) + ".", "Policy §6")
    if outside is None and sensitive_in_scope:
        r.unverified.append("whether sensitive data would be stored outside the operating region")

    # ---------------- §7 legal ----------------------------------------------
    legal_reasons = []
    if is_new and cost is not None and cost >= 10000:
        legal_reasons.append(f"new vendor with annual spend {money(cost)} (>= $10,000)")
    elif is_new and cost is None:
        legal_reasons.append("new vendor and annual spend unknown (cannot rule out >= $10,000)")
    terms = reg_rec.get("legal_terms_status") if reg_ok else None
    if not reg_ok:
        legal_reasons.append("no approved legal terms on file (vendor not in registry)")
    elif _slug(terms) not in {"approved", "standard"}:
        legal_reasons.append(f"legal terms are {terms or 'missing'} (not approved/standard)")
    if outside and sensitive_in_scope:
        legal_reasons.append("material cross-region data-processing issue")
    if legal_reasons:
        r.approve("Legal", "; ".join(legal_reasons), "§7")
        r.flag("legal_review_required", "; ".join(legal_reasons), "§7")
        r.find("evaluate_policy", "Legal review required: " + "; ".join(legal_reasons) + ".", "Policy §7")

    # ---------------- §3 overlap --------------------------------------------
    for m in catalog.get("matches", []):
        r.overlap.append({k: m.get(k) for k in ("software_id", "product_name", "vendor_name", "category", "relation",
                                                 "match_types", "licensed_seats", "scope",
                                                 "covers_requester_department", "status")})
    if r.overlap:
        names = ", ".join(f"{o['product_name']} ({o['software_id']})" for o in r.overlap)
        r.flag("existing_tool_overlap", f"Existing catalog items: {names}.", "§3")
        for o in r.overlap:
            rel = ("same vendor - likely expansion/add-on of an existing licence"
                   if o["relation"] == "existing_vendor_expansion_or_add_on"
                   else "same category - potential competing tool")
            seats = f", {int(o['licensed_seats'])} licensed seats" if o.get("licensed_seats") is not None else ""
            cover = "; already covers the requester's department" if o.get("covers_requester_department") else ""
            r.find("search_software_catalog", f"Existing catalog item {o['product_name']} ({o['software_id']}, "
                   f"{o['category']}, scope {o['scope']}{seats}): {rel}{cover}. Overlap is not a rejection; "
                   "confirm the gap or unused capacity.", "Policy §3")

    # ---------------- approver resolution -----------------------------------
    r.required_approvals.sort(key=CANONICAL_APPROVALS.index)
    mgr, dh = ctx.get("manager"), ctx.get("department_head")
    dh_res = ctx.get("department_head_resolution")
    dept = (requester or {}).get("department")
    for a in r.required_approvals:
        if a == "Manager":
            r.approver_details.append({"role": a, "name": mgr.get("name") if mgr else None,
                                       "employee_id": mgr.get("employee_id") if mgr else None,
                                       "status": "resolved" if mgr else "unresolved"})
        elif a == "Department Head":
            ok = dh and dh_res == "resolved"
            r.approver_details.append({"role": a, "name": dh.get("name") if ok else None,
                                       "employee_id": dh.get("employee_id") if ok else None,
                                       "status": "resolved" if ok else dh_res or "unresolved",
                                       "note": None if ok else f"No unique Director found for '{dept}'"})
        else:
            r.approver_details.append({"role": a, "name": f"{a} review queue", "employee_id": None,
                                       "status": "role queue"})
    names = [f"{d['role']}: {d['name'] or 'UNRESOLVED'}" for d in r.approver_details
             if d["role"] in {"Manager", "Department Head"}]
    if names:
        r.find("get_request_context", "Named approvers - " + "; ".join(names)
               + (". " + " ".join(ctx.get("notes", [])) if ctx.get("notes") else "."), "employees.csv")

    # ---------------- deterministic recommendation (used by fallback) --------
    r.recommendation_hint, r.next_step_hint = _hints(r)
    return r


def _hints(r: PolicyResult) -> tuple[str, str]:
    reviews = [a for a in r.required_approvals]
    if r.missing_information:
        rec = "Request more information before review"
        step = ("Ask the requester to supply: " + "; ".join(r.missing_information)
                + ". Re-run the review once complete; do not route for approval yet.")
        if "prompt_injection_detected" in r.risk_flags:
            step += " Embedded instructions in the request were ignored and do not constitute any approval."
        return rec, step
    if "vendor_risk_unavailable" in r.risk_flags:
        return ("Manual review - vendor risk could not be verified",
                "Route to Security for manual vendor assessment and retry the vendor-risk lookup; then obtain: "
                + ", ".join(reviews) + ".")
    if "budget_insufficient" in r.risk_flags:
        return ("Budget exception review required before approval",
                "Send to Finance for a budget exception decision, then route to: " + ", ".join(reviews) + ".")
    if "conflicting_vendor_evidence" in r.risk_flags or "vendor_review_expired" in r.risk_flags:
        return ("Security reassessment required before approval",
                "Ask Security to reconcile/refresh the vendor assessment, then route to: " + ", ".join(reviews) + ".")
    return ("Route for required approvals", "Route the evidence package to: " + ", ".join(reviews) + ".")
