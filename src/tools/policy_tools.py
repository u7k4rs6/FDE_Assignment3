"""Policy tools: ``lookup_policy`` (text for citation) and ``evaluate_policy``
(the deterministic engine run over evidence gathered by the data tools)."""
from __future__ import annotations

import re

from src import data_access as da
from src.config import reference_date
from src.policy_engine import evaluate
from src.tools.runtime import ToolContext

_HEADING = re.compile(r"^##\s+(\d+)\.\s+(.+)$", re.M)


def _sections(text: str) -> list[dict]:
    heads = list(_HEADING.finditer(text))
    out = []
    for i, h in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        out.append({"number": h.group(1), "title": h.group(2).strip(), "text": text[h.start():end].strip()})
    return out


def lookup_policy(section: str, ctx: ToolContext | None = None) -> dict:
    """Return policy text by section number ("5", "§5") or keyword ("security")."""
    text = da.load_policy_text()
    sections = _sections(text)
    query = str(section or "").strip().lower().lstrip("§").strip()
    hits = [s for s in sections if s["number"] == query] or \
           [s for s in sections if query and query in s["title"].lower()] or \
           [s for s in sections if query and query in s["text"].lower()]
    if not hits:
        return {"status": "not_found", "query": section,
                "available_sections": [f"§{s['number']} {s['title']}" for s in sections]}
    return {"status": "ok", "query": section, "reference_date": reference_date(text).isoformat(),
            "sections": [{"section": f"§{s['number']}", "title": s["title"], "text": s["text"]} for s in hits[:2]]}


def collect_bundle(ctx: ToolContext, request_id: str, caller: str) -> dict:
    """Gather the evidence bundle with data-derived arguments (never LLM-supplied
    ones). Results already fetched by an agent are reused from the runner cache."""
    runner = ctx.runner
    assert runner is not None, "collect_bundle needs a ToolRunner-bound context"
    bundle: dict = {"context": runner.call("get_request_context", {"request_id": request_id}, caller=caller)}
    if bundle["context"].get("status") != "ok":
        return bundle
    req = bundle["context"]["request"]
    requester = bundle["context"].get("requester") or {}
    dept = requester.get("department")
    cost = da.to_number(req.get("annual_cost_usd"))
    if dept:
        bundle["budget"] = runner.call("check_budget", {"department": dept, "annual_cost": cost}, caller=caller)
    bundle["catalog"] = runner.call("search_software_catalog", {
        "product": req.get("product_name"), "vendor": req.get("vendor_name"), "category": req.get("category"),
        "use_case": req.get("business_justification"), "department": dept}, caller=caller)
    vendor = da.clean(req.get("vendor_name"))
    if vendor:
        bundle["registry"] = runner.call("get_vendor_registry", {"vendor_name": vendor}, caller=caller)
        bundle["vendor_risk"] = runner.call("get_vendor_risk", {"vendor_name": vendor}, caller=caller)
    return bundle


def evaluate_policy(request_id: str, ctx: ToolContext | None = None) -> dict:
    """Deterministic policy floor for a request.

    Takes the request id rather than an LLM-assembled evidence bundle so that a
    model cannot feed fabricated facts into the deterministic engine; the
    engine always gathers its own evidence through the same (cached) tools.
    """
    if ctx is None or ctx.runner is None:
        return {"status": "error", "reason": "evaluate_policy requires a tool runner"}
    bundle = collect_bundle(ctx, request_id, caller="evaluate_policy")
    result = evaluate(bundle, reference_date()).as_dict()
    return {"status": "ok", "request_id": request_id, **result}
