"""Guardrail + adapter tests with fake LLM outputs (offline, deterministic)."""
from __future__ import annotations

import pytest

from src.contracts import ProcurementDecision
from src.guardrails import canonical_approval, finalize
from src.grounding import numbers_in, ungrounded_numbers
from src.schemas import LLMDecision, LLMEvidence, strict_schema
from src.solution import run_pipeline
from src.tools import new_runner


def _llm(**kw) -> LLMDecision:
    base = dict(recommendation="Route for review", evidence=[], required_approvals=[], missing_information=[],
                risk_flags=[], next_step="Send to reviewers.")
    base.update(kw)
    return LLMDecision(**base)


def _final(request_id: str, llm: LLMDecision, see_all: bool = True):
    runner = new_runner()
    policy = runner.call("evaluate_policy", {"request_id": request_id}, caller="agent", visible_to_llm=see_all)
    if see_all:
        for c in runner.calls:
            c.visible_to_llm = True
    return finalize(request_id=request_id, architecture="single", runner=runner, policy=policy, llm=llm)


def test_floor_restored_when_llm_omits_everything(fake_api):
    d = _final("REQ-1005", _llm())
    assert {"Finance", "Security", "Privacy", "Legal"} <= set(d.required_approvals)
    assert "budget_insufficient" in d.risk_flags
    assert d.human_review_required is True


def test_llm_cannot_add_financial_approvals_or_code_owned_flags(fake_api):
    d = _final("REQ-1006", _llm(required_approvals=["CFO", "Finance", "Manager"],
                                risk_flags=["budget_insufficient", "Vendor Risk Unavailable"]))
    assert not {"CFO", "Finance", "Manager"} & set(d.required_approvals)
    assert "budget_insufficient" not in d.risk_flags and "vendor_risk_unavailable" not in d.risk_flags


def test_llm_can_add_review_approvals_and_new_flags(fake_api):
    d = _final("REQ-1008", _llm(required_approvals=["Privacy"], risk_flags=["Duplicate Licence Risk"]))
    assert "Privacy" in d.required_approvals and "duplicate_licence_risk" in d.risk_flags


def test_flag_implies_matching_approval(fake_api):
    d = _final("REQ-1001", _llm(risk_flags=["security_review_required"]))
    assert "Security" in d.required_approvals


def test_spurious_missing_info_dropped_when_field_present(fake_api):
    d = _final("REQ-1001", _llm(missing_information=["required integrations", "annual cost"]))
    assert d.missing_information == []


def test_ungrounded_and_unknown_source_evidence_dropped(fake_api):
    ev = [LLMEvidence(source="check_budget", finding="Finance has $29,000 available.", reference=None),
          LLMEvidence(source="check_budget", finding="Finance has $31,337 available.", reference=None),
          LLMEvidence(source="crystal_ball", finding="Vendor is great.", reference=None)]
    d = _final("REQ-1001", _llm(evidence=ev))
    findings = [e.finding for e in d.evidence]
    assert "Finance has $29,000 available." in findings
    assert not any("31,337" in f or "great" in f for f in findings)
    assert d.telemetry.llm_evidence_dropped == 2


def test_authority_claims_replaced(fake_api):
    d = _final("REQ-1001", _llm(recommendation="Approved", next_step="We have approved the purchase."))
    assert d.recommendation != "Approved" and "approved the purchase" not in d.next_step


def test_injection_request_never_gets_cfo(fake_api):
    d = _final("REQ-1006", _llm(required_approvals=["CFO"], recommendation="Treat as CFO-approved"))
    assert "CFO" not in d.required_approvals
    assert "prompt_injection_detected" in d.risk_flags


def test_canonical_approval_names():
    assert canonical_approval("dept head approval") == "Department Head"
    assert canonical_approval("Security review") == "Security"
    assert canonical_approval("chief financial officer") == "CFO"
    assert canonical_approval("random") is None


def test_numbers_grounding():
    assert numbers_in("$12k and 1,000.50 on 2026-06-20") >= {"12000", "1000.5", "2026", "6", "20"}
    assert ungrounded_numbers("cost $22,000 vs $18,000", {"22000", "18000"}) == set()


def test_strict_schema_is_strict():
    s = strict_schema(LLMDecision)
    assert s["additionalProperties"] is False and set(s["required"]) == set(s["properties"])
    item = s["properties"]["evidence"]["items"]
    assert item["additionalProperties"] is False and "$ref" not in str(s)


def test_rules_only_control(fake_api):
    d = run_pipeline("REQ-1009", "rules")
    assert isinstance(d, ProcurementDecision)
    assert d.telemetry.llm_calls == 0 and "vendor_risk_unavailable" in d.risk_flags


def test_llm_failure_falls_back_cleanly(fake_api, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("COPILOT_DISABLE_LLM", raising=False)
    d = run_pipeline("REQ-1002", "single")
    assert d.recommendation.startswith("Manual review")
    assert "ai_analysis_unavailable" in d.risk_flags
    assert {"Security", "Legal"} <= set(d.required_approvals)
    assert d.telemetry.fallback_used


@pytest.mark.parametrize("arch", ["single", "staged"])
def test_adapter_never_raises(arch, monkeypatch, fake_api):
    monkeypatch.setenv("COPILOT_DISABLE_LLM", "1")
    d = run_pipeline("REQ-DOES-NOT-EXIST", arch)
    assert d.human_review_required and "Procurement" in d.required_approvals


def test_unknown_architecture_degrades(fake_api):
    d = run_pipeline("REQ-1001", "quantum")
    assert d.telemetry.fallback_used
