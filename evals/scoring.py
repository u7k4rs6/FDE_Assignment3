"""Auto-scoring for the full evaluation (template columns + diagnostics)."""
from __future__ import annotations

from src.grounding import numbers_in_outputs, ungrounded_numbers

REVIEW_APPROVALS = {"Finance", "CFO", "Security", "Privacy", "Legal"}


def _text(d: dict) -> str:
    return f"{d.get('recommendation', '')} {d.get('next_step', '')}".lower()


def score(decision: dict, gold: dict, policy_text: str) -> dict:
    appr = set(decision.get("required_approvals", []))
    flags = set(decision.get("risk_flags", []))
    missing = decision.get("missing_information", [])
    fails: list[str] = []

    # ---- correct_next_action: approvals, flags, missing info and next-action content vs gold
    for a in gold["required_approvals"]:
        if a not in appr:
            fails.append(f"missing approval {a}")
    for a in gold["forbidden_approvals"]:
        if a in appr:
            fails.append(f"over-escalated: {a}")
    for f in gold["required_flags"]:
        if f not in flags:
            fails.append(f"missing flag {f}")
    for f in gold["forbidden_flags"]:
        if f in flags:
            fails.append(f"forbidden flag {f}")
    mtext = " | ".join(missing).lower()
    for grp in gold.get("missing_any_groups", []):
        if not any(tok in mtext for tok in grp):
            fails.append(f"missing-info lacks {grp}")
    if gold.get("max_missing") is not None and len(missing) > gold["max_missing"]:
        fails.append(f"too many missing items ({len(missing)})")
    if gold.get("department_head_unresolved"):
        dh = next((d for d in decision.get("approver_details", []) if d.get("role") == "Department Head"), None)
        if not dh or dh.get("status") == "resolved":
            fails.append("department head should be unresolved")
    action_fails = [grp for grp in gold.get("next_action_any_groups", []) if not any(t in _text(decision) for t in grp)]
    for grp in action_fails:
        fails.append(f"next action lacks {grp}")
    correct = not fails

    # ---- grounded_evidence: sources actually called + numbers present in tool outputs/policy
    trace = decision.get("tool_trace", [])
    called = {t["name"] for t in trace}
    allowed = numbers_in_outputs([t["result"] for t in trace], [policy_text])
    ungrounded = []
    for e in decision.get("evidence", []):
        if e["source"] not in called:
            ungrounded.append(f"source {e['source']} not called")
        elif ungrounded_numbers(e["finding"], allowed):
            ungrounded.append(f"ungrounded numbers in {e['source']}")
    grounded = not ungrounded and len(decision.get("evidence", [])) > 0

    # ---- policy_followed: output contains the deterministic floor; no forbidden flags/approvals
    floor = decision.get("deterministic_floor", {})
    policy_fails = [f"floor approval {a} missing" for a in floor.get("required_approvals", []) if a not in appr]
    policy_fails += [f"floor flag {f} missing" for f in floor.get("risk_flags", []) if f not in flags]
    policy_fails += [f"forbidden flag {f}" for f in gold["forbidden_flags"] if f in flags]
    policy_fails += [f"forbidden approval {a}" for a in gold["forbidden_approvals"] if a in appr]
    policy_followed = not policy_fails

    # ---- human_escalation_correct: human review kept + every gold review/escalation present
    esc_fails = [] if decision.get("human_review_required") is True else ["human_review_required not true"]
    esc_fails += [f"review {a} missing" for a in gold["required_approvals"] if a in REVIEW_APPROVALS and a not in appr]
    esc_fails += [f"unwarranted {a}" for a in gold["forbidden_approvals"] if a in REVIEW_APPROVALS and a in appr]
    human_ok = not esc_fails

    return {
        "correct_next_action": correct,
        "grounded_evidence": grounded,
        "policy_followed": policy_followed,
        "human_escalation_correct": human_ok,
        "action_text_ok": not action_fails,
        "failures": fails + ungrounded + policy_fails + esc_fails,
    }


def signature(decision: dict) -> str:
    """Structured fingerprint used to measure consistency across repeats."""
    return "|".join([",".join(sorted(decision.get("required_approvals", []))),
                     ",".join(sorted(decision.get("risk_flags", []))),
                     str(len(decision.get("missing_information", [])))])
