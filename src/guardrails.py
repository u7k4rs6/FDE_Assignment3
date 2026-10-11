"""Post-processing guardrail shared by every architecture.

Enforced in code (not just in prompts):
1. Deterministic floor: approvals / flags / missing info computed by the
   policy engine are always present (union), whatever the LLM said.
2. Code-owned decisions: the LLM cannot add financial approvals (Manager,
   Department Head, Finance, CFO) or code-decidable flags (budget, API
   availability, expiry, conflicts) that the engine did not compute.
3. Grounding: LLM evidence must cite a tool whose output an LLM actually saw,
   and every number it states must appear in tool output or the policy.
4. Human authority: human_review_required is always True; recommendation text
   that claims an approval/purchase happened is replaced.
5. Canonical approval names, deduplication, telemetry.
"""
from __future__ import annotations

import re

from src import data_access as da
from src.grounding import numbers_in_outputs, ungrounded_numbers
from src.policy_engine import CANONICAL_APPROVALS, FINANCIAL_APPROVALS
from src.schemas import CopilotDecision, EvidenceItem, ExtendedTelemetry, LLMDecision
from src.tools import ToolRunner

CODE_OWNED_FLAGS = {"budget_insufficient", "budget_unverified", "vendor_risk_unavailable", "vendor_review_expired",
                    "conflicting_vendor_evidence", "missing_information", "limited_use_scope_exceeded"}
FLAG_TO_APPROVAL = {"security_review_required": "Security", "privacy_review_required": "Privacy",
                    "legal_review_required": "Legal"}

_APPROVAL_ALIASES = {
    "manager": "Manager", "line manager": "Manager", "department head": "Department Head", "dept head": "Department Head",
    "head of department": "Department Head", "procurement": "Procurement", "finance": "Finance", "cfo": "CFO",
    "chief financial officer": "CFO", "security": "Security", "privacy": "Privacy", "legal": "Legal",
}

_FIELD_KEYWORDS = [
    ("annual_cost", ("cost", "price", "spend", "budget amount", "amount")),
    ("user_count", ("user", "seat", "licen")),
    ("data_access_level", ("data access", "data-access", "data level", "data classification", "data")),
    ("integrations", ("integration",)),
    ("purpose", ("purpose", "justification", "business need")),
    ("requester", ("requester", "department")),
    ("product", ("product", "vendor")),
]

_AUTHORITY_VIOLATION = re.compile(
    r"\b(?:i|we|copilot)\s+(?:have\s+)?(?:approved|purchased|signed|accepted)\b|"
    r"\b(?:purchase|request|spend)\s+(?:is|has been)\s+(?:approved|completed|authori[sz]ed)\b|"
    r"\bbudget\s+(?:has been\s+)?(?:increased|modified|updated|adjusted)\b|"
    r"^\s*approved\b|\b(?:is|has been)\s+(?:cfo|finance|security|legal)[- ]approved\b", re.I)


def canonical_approval(name: str) -> str | None:
    s = re.sub(r"\b(?:approval|review|sign-?off|team)\b", "", str(name).lower()).strip(" -:")
    if s in _APPROVAL_ALIASES:
        return _APPROVAL_ALIASES[s]
    for alias, canon in _APPROVAL_ALIASES.items():
        if alias in s:
            return canon
    return None


def canonical_flag(flag: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(flag).lower()).strip("_")


def missing_field_key(item: str) -> str | None:
    s = str(item).lower()
    for key, words in _FIELD_KEYWORDS:
        if any(w in s for w in words):
            return key
    return None


def _norm_text(s: str) -> str:
    return re.sub(r"\W+", " ", s.lower()).strip()


def finalize(*, request_id: str, architecture: str, runner: ToolRunner, policy: dict, llm: LLMDecision | None,
             llm_stats=None, fallback_reason: str | None = None, latency_ms: float | None = None,
             model: str | None = None, evidence_pack: dict | None = None) -> CopilotDecision:
    actions: list[str] = []
    floor_approvals = list(policy.get("required_approvals", []))
    floor_flags = list(policy.get("risk_flags", []))
    floor_missing = list(policy.get("missing_information", []))
    present_fields = set(policy.get("present_fields", []))
    floor_missing_keys = {missing_field_key(m) for m in floor_missing}

    approvals = list(floor_approvals)
    flags = list(floor_flags)
    missing = list(floor_missing)
    evidence: list[EvidenceItem] = []
    llm_items = dropped = 0

    if llm is not None:
        # -- approvals: LLM may add review approvals only -------------------
        for a in llm.required_approvals:
            canon = canonical_approval(a)
            if canon is None or canon in approvals:
                continue
            if canon in FINANCIAL_APPROVALS:
                actions.append(f"dropped LLM-added financial approval '{canon}' (thresholds are code-owned)")
                continue
            approvals.append(canon)
            actions.append(f"kept LLM-added approval '{canon}'")
        removed = [a for a in floor_approvals if a not in {canonical_approval(x) for x in llm.required_approvals}]
        if removed:
            actions.append("restored floor approvals omitted by LLM: " + ", ".join(removed))

        # -- flags ------------------------------------------------------------
        for f in llm.risk_flags:
            cf = canonical_flag(f)
            if not cf or cf in flags:
                continue
            if cf in CODE_OWNED_FLAGS:
                actions.append(f"dropped LLM flag '{cf}' (code-decidable and not supported by deterministic checks)")
                continue
            flags.append(cf)
            actions.append(f"kept LLM-added flag '{cf}'")
        omitted = [f for f in floor_flags if f not in {canonical_flag(x) for x in llm.risk_flags}]
        if omitted:
            actions.append("restored floor flags omitted by LLM: " + ", ".join(omitted))

        # -- missing information -------------------------------------------
        for m in llm.missing_information:
            key = missing_field_key(m)
            if key in present_fields:
                actions.append(f"dropped LLM missing-info '{m}' (field is present per §1 check)")
                continue
            if key is not None and key in floor_missing_keys:
                continue  # duplicate of a floor item
            if _norm_text(m) in {_norm_text(x) for x in missing}:
                continue
            missing.append(m)
            actions.append(f"kept LLM-added missing-info '{m}'")

        # -- evidence grounding ----------------------------------------------
        visible = runner.visible_tool_names()
        allowed_numbers = numbers_in_outputs(runner.outputs_for(visible), [da.load_policy_text()])
        for e in llm.evidence:
            llm_items += 1
            if e.source not in visible:
                dropped += 1
                actions.append(f"dropped evidence citing '{e.source}' (tool output not seen by the LLM)")
                continue
            bad = ungrounded_numbers(e.finding, allowed_numbers)
            if bad:
                dropped += 1
                actions.append(f"dropped evidence with ungrounded numbers {sorted(bad)} (source {e.source})")
                continue
            evidence.append(EvidenceItem(source=e.source, finding=e.finding, reference=e.reference))

    # Flag <-> approval consistency
    for f, a in FLAG_TO_APPROVAL.items():
        if f in flags and a not in approvals:
            approvals.append(a)
            actions.append(f"added '{a}' to match flag '{f}'")
    if missing and "missing_information" not in flags:
        flags.append("missing_information")

    # Deterministic findings always included (deduplicated)
    seen = {_norm_text(e.finding) for e in evidence}
    for f in policy.get("findings", []):
        if _norm_text(f["finding"]) not in seen:
            evidence.append(EvidenceItem(**f))
            seen.add(_norm_text(f["finding"]))

    # Recommendation / next step
    rec_hint, step_hint = policy.get("recommendation_hint", ""), policy.get("next_step_hint", "")
    if fallback_reason is not None:
        recommendation = "Manual review — AI analysis unavailable"
        next_step = f"{step_hint} (AI analysis unavailable: {fallback_reason}. Deterministic checks only.)"
        if "ai_analysis_unavailable" not in flags:
            flags.append("ai_analysis_unavailable")
    elif llm is None:
        recommendation, next_step = rec_hint, step_hint
    else:
        recommendation, next_step = llm.recommendation.strip(), llm.next_step.strip()
        if _AUTHORITY_VIOLATION.search(recommendation) or _AUTHORITY_VIOLATION.search(next_step):
            actions.append("replaced LLM recommendation that asserted an approval/purchase (human authority, §11): "
                           f"{recommendation[:80]!r}")
            recommendation, next_step = rec_hint, step_hint
        if floor_missing and not re.search(r"clarif|more info|missing|provide|supply|ask the requester|return the request|obtain|request (?:the )?(?:annual|cost|user)", next_step, re.I):
            next_step = f"Ask the requester for: {'; '.join(floor_missing)}. " + next_step
            actions.append("prepended clarification request (floor has missing information)")

    approvals = sorted({a for a in approvals if a in CANONICAL_APPROVALS}, key=CANONICAL_APPROVALS.index)
    flags = list(dict.fromkeys(flags))

    llm_tool_calls = sum(1 for c in runner.calls if c.caller in {"agent", "analyst", "reviewer"})
    telemetry = ExtendedTelemetry(
        llm_calls=llm_stats.calls if llm_stats else 0,
        tool_calls=len(runner.calls),
        tool_names=[c.name for c in runner.calls],
        architecture=architecture,
        model=model,
        latency_ms=round(latency_ms, 1) if latency_ms is not None else None,
        llm_tool_calls=llm_tool_calls,
        code_tool_calls=len(runner.calls) - llm_tool_calls,
        input_tokens=llm_stats.input_tokens if llm_stats else 0,
        output_tokens=llm_stats.output_tokens if llm_stats else 0,
        fallback_used=fallback_reason is not None,
        fallback_reason=fallback_reason,
        llm_evidence_items=llm_items,
        llm_evidence_dropped=dropped,
        guardrail_actions=actions,
    )
    return CopilotDecision(
        request_id=request_id,
        recommendation=recommendation or "Manual review",
        evidence=evidence,
        required_approvals=approvals,
        missing_information=missing,
        risk_flags=flags,
        next_step=next_step or "Route to Procurement for manual review.",
        human_review_required=True,  # always: every purchase needs a human approver (§4, §11)
        telemetry=telemetry,
        architecture=architecture,
        approver_details=policy.get("approver_details", []),
        unverified=list(dict.fromkeys(policy.get("unverified", []))),
        deterministic_floor={"required_approvals": floor_approvals, "risk_flags": floor_flags,
                             "missing_information": floor_missing, "approvals_computable":
                             policy.get("approvals_computable", True), "flag_reasons": policy.get("flag_reasons", []),
                             "approval_reasons": policy.get("approval_reasons", [])},
        tool_trace=runner.trace(),
        evidence_pack=evidence_pack,
    )
