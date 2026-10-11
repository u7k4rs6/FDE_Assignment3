# Student submission checklist

- [x] `python verify_setup.py` passes in the project environment (also checked in a fresh clone + venv).
- [x] The product processes a request end-to-end (`python run_local.py` → UI; Playwright suite `tests/e2e`, 8/8).
- [x] Architecture A is a working single-agent baseline (`src/agents/single.py`; public runner 6/6).
- [x] Architecture B is a lightweight staged / 2-agent variant (`src/agents/staged.py`; public runner 6/6).
- [x] At least 3 tools are used; at least 1 is deterministic: 7 tools, all deterministic Python (`src/tools`).
- [x] Recommendations come back in the `ProcurementDecision` structure (`CopilotDecision` subclass).
- [x] Important evidence is visible to the user (evidence panel, tool trace, deterministic floor, Evidence Pack).
- [x] Missing/conflicting/unavailable evidence is handled without fabrication (§1/§5/§10 rules + perturbation evals).
- [x] Human approval is kept for sensitive decisions (`human_review_required` always true; code-owned approvals; UI actions only log locally).
- [x] Prompt injection inside business data doesn't override behaviour (regex over all untrusted fields, `<untrusted_data>` wrapping, CFO can't be added by the LLM; request, vendor-note and API-note cases tested).
- [x] Date checks use the policy's reference date (parsed from `procurement_policy.md`; 365/366-day boundary tests).
- [x] The same eval cases ran on both architectures (22 cases × 3 repeats each).
- [x] Latency and LLM/tool-call counts are reported (`evals/results/comparison.md`, grader CSVs).
- [x] The decision memo is ≤ 500 words and backed by eval evidence (`docs/architecture_decision.md`, test-enforced).
- [x] Setup instructions work from a clean environment (fresh clone: install → preflight → 105 tests → public evals 6/6).
- [x] The LLM SDK is in `requirements.txt` (`anthropic`).
- [x] `.env`, API keys and other secrets are not committed (git history scanned; `tests/test_docs.py` checks too).
