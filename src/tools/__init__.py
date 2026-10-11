"""Tool registry + JSON schemas (Anthropic tool format).

All seven tools are deterministic Python; the LLM only decides *when* to call
them and how to interpret the results. Both architectures use this registry.
"""
from __future__ import annotations

from src.tools.data_tools import (check_budget, get_request_context, get_vendor_registry, get_vendor_risk,
                                  search_software_catalog)
from src.tools.policy_tools import collect_bundle, evaluate_policy, lookup_policy
from src.tools.runtime import ToolCall, ToolContext, ToolRunner

REGISTRY = {
    "get_request_context": get_request_context,
    "check_budget": check_budget,
    "search_software_catalog": search_software_catalog,
    "get_vendor_registry": get_vendor_registry,
    "get_vendor_risk": get_vendor_risk,
    "evaluate_policy": evaluate_policy,
    "lookup_policy": lookup_policy,
}


def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


_S = {"type": "string"}
_NULLABLE_S = {"anyOf": [{"type": "string"}, {"type": "null"}]}

TOOL_SCHEMAS = {
    "get_request_context": {
        "description": "Load a purchase request with its requester, the requester's manager, the department head "
                       "(Director of the requester's department, if any) and the department budget row. Call first.",
        "input_schema": _obj({"request_id": _S}, ["request_id"]),
    },
    "check_budget": {
        "description": "Deterministic budget check (policy §2): compares an annual cost with a department's available "
                       "software budget. Returns within_budget, shortfall and remaining amounts.",
        "input_schema": _obj({"department": _S,
                              "annual_cost": {"anyOf": [{"type": "number"}, {"type": "null"}]}},
                             ["department", "annual_cost"]),
    },
    "search_software_catalog": {
        "description": "Search the approved software catalog for the same product family, vendor or category "
                       "(deterministic), plus keyword candidates that may satisfy the use case (needs your "
                       "judgement), and the vendor's purchase history (policy §3).",
        "input_schema": _obj({"product": _NULLABLE_S, "vendor": _NULLABLE_S, "category": _NULLABLE_S,
                              "use_case": _NULLABLE_S, "department": _NULLABLE_S},
                             ["product", "vendor", "category", "use_case", "department"]),
    },
    "get_vendor_registry": {
        "description": "Internal vendor registry record: procurement status, security status, review date with its "
                       "age in days versus the policy reference date (current for 365 days), legal terms status.",
        "input_schema": _obj({"vendor_name": _S}, ["vendor_name"]),
    },
    "get_vendor_risk": {
        "description": "External vendor-risk service: risk level, security review status/date, personal-data "
                       "processing and outside-region storage. Returns status 'unavailable' on outages/404.",
        "input_schema": _obj({"vendor_name": _S}, ["vendor_name"]),
    },
    "evaluate_policy": {
        "description": "Run the deterministic policy engine for a request: required approvals (§4 thresholds, "
                       "§2 budget, §5-§7 reviews), risk flags, missing information (§1), injection detection (§9), "
                       "tool failures (§10), resolved approvers. Its output is a floor you cannot lower.",
        "input_schema": _obj({"request_id": _S}, ["request_id"]),
    },
    "lookup_policy": {
        "description": "Return the procurement policy text for a section number (e.g. '5') or keyword "
                       "(e.g. 'legal') so you can cite it.",
        "input_schema": _obj({"section": _S}, ["section"]),
    },
}


def anthropic_tools(names: list[str]) -> list[dict]:
    return [{"name": n, "strict": True, **TOOL_SCHEMAS[n]} for n in names]


def new_runner(request_override: dict | None = None) -> ToolRunner:
    return ToolRunner(registry=REGISTRY, context=ToolContext(request_override=request_override))


__all__ = ["REGISTRY", "TOOL_SCHEMAS", "anthropic_tools", "new_runner", "collect_bundle", "ToolRunner",
           "ToolContext", "ToolCall"]
