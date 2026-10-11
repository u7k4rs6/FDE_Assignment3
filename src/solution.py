from __future__ import annotations

import time

from src.config import llm_disabled
from src.contracts import Architecture, ProcurementDecision
from src.schemas import CopilotDecision, EvidenceItem, ExtendedTelemetry
from src.tools import new_runner


def handle_request(request_id: str, architecture: Architecture = "single") -> ProcurementDecision:
    """Assessment adapter.

    Returns a ``CopilotDecision`` (a ``ProcurementDecision`` subclass with extra
    transparency fields). Never raises: any failure degrades to the
    deterministic rules-only decision with a clear flag.
    """
    return run_pipeline(request_id, architecture)


def handle_request_payload(request: dict, architecture: str = "single") -> CopilotDecision:
    """Analyse an ad-hoc request (UI 'New request' form) without touching data files."""
    return run_pipeline(request["request_id"], architecture, request_override=request)


def run_pipeline(request_id: str, architecture: str = "single", request_override: dict | None = None) -> CopilotDecision:
    """``architecture``: 'single' | 'staged' | 'rules' (deterministic control, no LLM)."""
    start = time.perf_counter()
    runner = new_runner(request_override)
    llm = llm_out = pack = None
    fallback_reason = None
    try:
        if architecture not in {"single", "staged", "rules"}:
            raise ValueError(f"Unknown architecture '{architecture}'")
        if architecture == "rules":
            pass
        elif llm_disabled():
            fallback_reason = "LLM disabled by configuration"
        else:
            from src.llm import LLM

            llm = LLM()
            if architecture == "single":
                from src.agents import single

                llm_out = single.run(llm, runner, request_id)
            else:
                from src.agents import staged

                llm_out, pack_model = staged.run(llm, runner, request_id)
                pack = pack_model.model_dump()
    except Exception as exc:  # noqa: BLE001 - never crash the adapter
        llm_out = None
        fallback_reason = f"{type(exc).__name__}: {str(exc)[:200]}"

    try:
        from src.guardrails import finalize

        policy = runner.call("evaluate_policy", {"request_id": request_id}, caller="guardrail")
        if policy.get("status") != "ok":
            raise RuntimeError(policy.get("reason", "policy engine failed"))
        return finalize(request_id=request_id, architecture=architecture, runner=runner, policy=policy, llm=llm_out,
                        llm_stats=llm.stats if llm else None, fallback_reason=fallback_reason,
                        latency_ms=(time.perf_counter() - start) * 1000, model=llm.model if llm else None,
                        evidence_pack=pack)
    except Exception as exc:  # noqa: BLE001 - last-resort safe output
        return CopilotDecision(
            request_id=request_id,
            recommendation="Manual review — copilot error",
            evidence=[EvidenceItem(source="copilot", finding=f"Analysis failed: {type(exc).__name__}: {exc}")],
            required_approvals=["Procurement"],
            missing_information=[],
            risk_flags=["copilot_error"],
            next_step="Route to Procurement for fully manual review.",
            human_review_required=True,
            telemetry=ExtendedTelemetry(llm_calls=llm.stats.calls if llm else 0, tool_calls=len(runner.calls),
                                        tool_names=[c.name for c in runner.calls], architecture=architecture,
                                        fallback_used=True, fallback_reason=str(exc),
                                        latency_ms=round((time.perf_counter() - start) * 1000, 1)),
            architecture=architecture,
            tool_trace=runner.trace(),
        )
