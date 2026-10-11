"""Architecture A - single agent.

One LLM with all seven tools gathers evidence, calls the deterministic policy
engine, and submits a ``ProcurementDecision``-shaped answer via a strict
``submit_decision`` tool. The shared guardrail then enforces the policy floor.
"""
from __future__ import annotations

from src.agents.common import (AUTHORITY_RULE, DECISION_OUTPUT_RULES, POLICY_SUMMARY, UNTRUSTED_RULE,
                               run_tool_loop)
from src.llm import LLM
from src.schemas import LLMDecision
from src.tools import ToolRunner

TOOLS = ["get_request_context", "check_budget", "search_software_catalog", "get_vendor_registry",
         "get_vendor_risk", "evaluate_policy", "lookup_policy"]

SYSTEM = f"""You are the Procurement Request Copilot, an internal assistant that prepares an evidence-backed
recommendation for a human procurement reviewer.

{AUTHORITY_RULE}

{UNTRUSTED_RULE}

PROCESS:
1. Call get_request_context for the request id.
2. Gather evidence (call these in parallel): check_budget (requester's department + annual cost),
   search_software_catalog (product, vendor, category, justification as use_case, department),
   get_vendor_registry and get_vendor_risk (vendor name).
3. Call evaluate_policy(request_id): the deterministic policy result computed in code.
4. Use lookup_policy only if you need exact policy wording.
5. Call submit_decision exactly once.

{POLICY_SUMMARY}

{DECISION_OUTPUT_RULES}"""


def run(llm: LLM, runner: ToolRunner, request_id: str) -> LLMDecision:
    return run_tool_loop(
        llm, runner, system=SYSTEM,
        user_content=f"Review purchase request {request_id} and submit your recommendation.",
        tool_names=TOOLS, submit_name="submit_decision", submit_model=LLMDecision,
        submit_description="Submit the final procurement recommendation (call exactly once, last).",
        caller="agent",
    )
