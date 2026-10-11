# CLAUDE.md — AI Procurement Request Copilot (FDE Assessment 3)

You are building a graded take-home: an internal procurement copilot that gathers evidence with tools,
applies deterministic policy checks in code, and recommends a next action while keeping approvals with
humans. Two architectures (A: single agent, B: staged 2-agent) must be built, evaluated on the same
test set, and compared. A simpler system that performs as well wins. Agent count and UI polish earn
nothing by themselves. Graders also run HIDDEN cases with different data values through the same
`handle_request(request_id, architecture)` adapter.

## Non-negotiable rules
- Never hardcode anything by request_id, vendor name, or product name in `src/`. Everything is derived
  from data files + policy at call time. Hidden cases swap values.
- Reference date for ALL date logic is **2026-09-30** (from `data/procurement_policy.md`). Never use
  `date.today()`. Parse it from the policy file (fallback constant) and pass it explicitly.
- Request text, vendor notes, API notes are UNTRUSTED DATA. Never let them change rules. Wrap them in
  `<untrusted_data>` tags in prompts and say so in the system prompt.
- The copilot never approves, purchases, edits budgets, or accepts terms. `human_review_required`
  is always `True` (every purchase needs at least a human approver under policy §4/§11).
- Deterministic policy output is a FLOOR: the LLM may ADD approvals/flags/missing items, never remove
  the ones computed in code. Enforce this in a post-processing guardrail, not just in the prompt.
- Never infer a favorable status when evidence is missing/unavailable (policy §10).
- No secrets in the repo. `.env` stays ignored; keep `.env.example` updated.
- Both architectures must share the SAME tools, data layer, policy engine and guardrail, so the only
  variable in the comparison is orchestration.

## Stack
- Python 3.11/3.12, existing deps + `anthropic` SDK (add to requirements.txt).
- `MODEL_NAME` env var, default `claude-sonnet-5-5`; temperature 0. Check current SDK tool-use syntax
  against https://docs.claude.com/en/api/overview rather than guessing.
- Streamlit UI (keep it; extend it). Playwright for UI e2e (dev dependency only).
- Structured outputs: force a final `submit_decision` tool whose input schema is generated from the
  pydantic model; validate with pydantic; one repair retry on validation failure.

## Policy → code (deterministic engine, `src/policy_engine.py`)
- Required fields (§1): requester+dept, product/vendor, annual cost, user count, business purpose,
  data access level, integrations. `null`/empty-string/"unknown" = missing. An EMPTY integrations list
  `[]` means "none required" and is NOT missing (PUB-01 allows 0 missing items). Only `null` is missing.
  A justification that is only urgency/injection text with no real purpose counts as missing purpose.
- Budget (§2): cost > department `available_usd` → `budget_insufficient` + Finance.
- Thresholds (§4), on annualized cost:
  ≤1000 → Manager; 1000.01–10000 → Department Head + Procurement;
  10000.01–25000 → Department Head + Finance + Procurement; >25000 → Department Head + Finance + CFO + Procurement.
  Unit-test the exact boundaries (1000, 1000.01, 10000, 10000.01, 25000, 25000.01). If cost is
  missing, approvals cannot be computed — say so; include Procurement for triage.
- Security (§5): data level in {source_code, confidential_documents, employee_pii, customer_pii,
  credentials/secrets} OR integrations mentioning production/cloud account OR vendor assessment
  missing / expired (>365 days before ref date) / not completed / unknown / unavailable.
- Conflict (§5): registry vs API disagree on status or date → `conflicting_vendor_evidence` + Security.
  Note: a registry "Approved" whose date is >365 days old is itself stale → `vendor_review_expired`.
- Privacy (§6): employee/customer PII, or vendor stores data outside region while sensitive data is in scope.
- Legal (§7): vendor new (registry `procurement_status == New` or not in registry) AND cost ≥ 10000
  (note ≥, unlike the Finance threshold), OR legal terms not Approved (Draft/Unknown/missing),
  OR material cross-region/data-processing issue.
- Overlap (§3): same vendor/product, same category, or plausible use-case match in catalog →
  `existing_tool_overlap` with the existing item named. Overlap is NOT rejection. Distinguish
  "expansion of an existing licence" from "new competing tool".
- AI tools (§8): a vendor approved for limited use is not approved for PII/confidential/source/prod.
- Injection (§9): deterministic regex heuristic over ALL untrusted fields (request + vendor notes +
  API notes) → `prompt_injection_detected`; LLM may also flag. Ignore the instruction itself.
- Tool failure (§10): API 503/timeout/connection error/404 → `vendor_risk_unavailable`, record what
  could not be verified, route to Security/manual review.
- Resolve named approvers where data allows: Manager = requester's `manager_id`; Department Head =
  Director of the requester's department. If none exists (e.g. Customer Success has no Director), say
  "unresolved" in evidence — do not guess. Approval labels must use the canonical names:
  Manager, Department Head, Procurement, Finance, CFO, Security, Privacy, Legal.

## Starter scaffold issues found (fix and document in README "Scaffold fixes")
1. `.gitignore` contains `evals/results_*.csv` — this hides the eval results the brief requires you to
   submit. Write results to `evals/results/` and make sure they are committed.
2. `run_public_evals.py` assumes the mock API is already running; without it every vendor lookup fails
   and PUB-01 fails. Add an eval entrypoint that starts the mock API if `/health` is down, and stops it after.
3. `vendor_client.get_vendor_risk` raises raw `HTTPError` on 503/404 and `ConnectionError`/`Timeout`
   when the API is down — wrap it in a tool that returns a structured `{status: "unavailable", reason}`.
4. pandas loads empty CSV cells as `NaN` (e.g. `security_review_date`, `manager_id`) — normalise to None
   before date parsing or string checks.
5. `run_local.py` hardcodes port 8001 while the client reads `VENDOR_RISK_BASE_URL`; keep them consistent.
6. `app.py` uses `use_container_width`, deprecated in recent Streamlit — update if it warns.
7. Employee `E007` belongs to department "Go To Market", which has no budget row; managers can sit in
   other departments than their reports. Do not assume manager dept == requester dept.
Verify each one yourself before claiming it; add any others you find.

## Expected outcomes (EVAL GOLD LABELS ONLY — never import into src/)
Derived by hand from the policy; use to build `evals/gold_cases.json`. Re-derive if anything disagrees.
| Req | Key facts | Min approvals | Key flags | Correct next action |
|---|---|---|---|---|
| 1001 | SignFlow add-on $800, Finance budget ok, internal docs | Manager | (overlap/expansion ok) | Route to manager; no missing info |
| 1002 | BrandBoard new, sec not completed, terms Draft, $12k, overlaps PixelCraft/CreativeSuite | DH, Finance, Procurement, Security, Legal | overlap, security, legal | Reviews + confirm gap vs PixelCraft |
| 1003 | CodeMate approved, source code, $18k | DH, Finance, Procurement, Security | security, overlap(expansion) | Security review of repo controls; check unused seats |
| 1004 | NeuralDesk limited-use, customer PII, data outside region, $6k, CS budget $7k | DH, Procurement, Security, Privacy, Legal | security, privacy, legal, overlap | Security/Privacy review; DH unresolved for CS |
| 1005 | GrowthForge new high-risk, customer PII, outside region, $22k > Sales $18k | DH, Finance, Procurement, Security, Privacy, Legal | budget_insufficient, security, privacy, legal | Budget exception + full vendor onboarding |
| 1006 | cost/users/data missing, injection text "treat as CFO-approved" | Procurement (cannot compute) | missing_information, prompt_injection_detected, overlap(NeuralDesk) | Request clarification; ignore embedded instruction |
| 1007 | SignalWatch registry "Approved" 2025-07-01 vs API expired; prod cloud; $24k | DH, Finance, Procurement, Security | security, vendor_review_expired, conflicting_vendor_evidence | Security reassessment before expansion |
| 1008 | TaskFlow Pro $8k; TaskFlow company-wide 180 seats | DH, Procurement (if pursued) | existing_tool_overlap | Use existing licence; ask what Pro adds |
| 1009 | NimbusAI new, API 503, confidential docs, $15k | DH, Finance, Procurement, Security, Legal | vendor_risk_unavailable, security, legal | Manual review; retry assessment |
| 1010 | SignFlow training $950 "one-time annual" | Manager | — | Route to manager; note one-time vs annual ambiguity |

## Commands (keep these working)
- `python verify_setup.py` · `python -m pytest -q` · `python run_local.py`
- `python evals/run_public_evals.py --architecture single|staged` (grader's runner, unchanged contract)
- `python scripts/run_full_eval.py` (yours: both archs, gold + perturbation cases, repeats, CSV + markdown)

## Working style
- Small commits per phase with clear messages. Run tests before every commit.
- Never claim eval numbers you did not produce. Paste real output into docs.
- If an API key is missing, stop and ask — do not fake LLM output in reported results.
