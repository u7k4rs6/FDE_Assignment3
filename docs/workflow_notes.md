# Workflow / Architecture Notes

```text
Purchase request (data file or ad-hoc UI form)
      |
      v
Understand request ......................... LLM (single agent | stage-1 Analyst)
      |
      v
Gather evidence ---> get_request_context, check_budget, search_software_catalog,
      |              get_vendor_registry, get_vendor_risk (mock API, wrapped)
      v
Deterministic checks ---> evaluate_policy  (thresholds, budget, required fields,
      |                   security/privacy/legal triggers, 365-day expiry,
      |                   registry-vs-API conflict, injection regex, tool failures)
      v
Reason about policy & risk ................. LLM (single agent | stage-2 Reviewer)
      |                                       overlap judgement, what reviewers must check
      v
Guardrail (code) ---> floor merge, code-owned approvals, grounding, authority text
      |
      v
Structured recommendation (ProcurementDecision + transparency fields)
      |
      v
Human review / approval (UI action bar -> local audit log only)
```

## Tools

| Tool | Kind | Purpose |
|---|---|---|
| `get_request_context` | deterministic | Request + requester + manager (by `manager_id`) + department head (Director of the requester's department, or "unresolved") + budget row. Supports ad-hoc requests without touching data files. |
| `check_budget` | deterministic | §2: cost vs `available_usd`; shortfall/remaining; `not_found` when a department has no budget row. |
| `search_software_catalog` | deterministic + candidates | §3: matches on product family, vendor, category, with a relation label (expansion of an existing vendor vs competing tool in the same category) and whether the licence already covers the requester's department. Also returns keyword use-case candidates for the LLM to judge, plus the vendor's purchase history. |
| `get_vendor_registry` | deterministic | vendors.csv record with review age in days vs the policy reference date; `not_found` means new vendor. |
| `get_vendor_risk` | deterministic wrapper over external API | Mock vendor-risk service. 503/404/timeout/connection errors become `{status: "unavailable", reason, could_not_verify}`. One quick retry. |
| `evaluate_policy` | deterministic | The policy engine (`src/policy_engine.py`). Takes `request_id`, **not** an LLM-built evidence bundle, so the model can't feed invented facts into the rules. It gathers its own evidence through the same cached tools. |
| `lookup_policy` | deterministic | Returns policy section text for citation. |

## Deterministic vs model-driven

- **Code decides:** required fields (§1), approval thresholds (§4), budget (§2), security/privacy/legal triggers (§5–§7), the 365-day assessment validity, registry/API conflict detection, the AI limited-use scope (§8), the injection heuristic (§9), tool-failure handling (§10), approver name resolution, and `human_review_required = True`.
- **The model decides:** which tools to call and in what order; whether an existing tool really meets the use case (expansion vs competing tool, unused seats); ambiguities (one-time vs annual cost); what each reviewer must verify; and the wording of the recommendation and next step. It may also *add* review approvals (Security/Privacy/Legal/Procurement), flags and missing-info items.
- **The guardrail enforces it in code:** the deterministic result is a floor. The LLM can't drop floor items, can't add financial approvals (Manager/Department Head/Finance/CFO), and can't add code-decidable flags (budget, expiry, conflict, API unavailable). Evidence must cite a tool whose output the LLM actually saw, and every number in it must appear in tool output or the policy. Text claiming an approval or purchase is replaced.

## Agent responsibilities

| Architecture | Agent | Tools | Output |
|---|---|---|---|
| A, single | Procurement Copilot | all 7 tools + `submit_decision` | `LLMDecision` |
| B, staged | Stage 1: Procurement Analyst | 5 data tools + `submit_evidence_pack` | `EvidencePack`, no decision |
| B, staged | (code) | `evaluate_policy` | deterministic result |
| B, staged | Stage 2: Policy/Risk Reviewer | `lookup_policy` + `submit_decision` | `LLMDecision` |

## Handoff format (B)

`EvidencePack` (pydantic, strict JSON schema): `request_summary`, `facts[{source, fact, reference}]`,
`overlap_assessment[{existing_product, relation ∈ {expansion_of_existing_licence, competing_tool,
no_material_overlap, unclear}, rationale}]`, `uncertainties[]`, `suspicious_content[]`. The Reviewer gets
the pack inside `<untrusted_data>` (it comes from untrusted data) plus the deterministic result
in `<deterministic_policy_result>`.

## Stop / escalation conditions

- **Missing §1 field**: request clarification and don't route for approval. If cost is missing, the approval tier can't be computed, so only Procurement triage is listed.
- **Tool/API failure**: `vendor_risk_unavailable` is flagged, the unverified items are listed, and Security does a manual review. No favourable status is assumed.
- **Registry vs API conflict, or expired review**: Security reassessment.
- **Prompt injection**: flagged, the instruction is ignored, normal policy applies.
- **LLM failure** (no key, API error, timeout, refusal, two invalid submissions, more than 10 turns or 240 s): rules-only decision with "Manual review — AI analysis unavailable" and the `ai_analysis_unavailable` flag.
- **Always**: `human_review_required = True`. The UI actions only append to `data/runtime/decisions.jsonl`.

## Intentionally not built

- No autonomous actions (no purchase, approval routing, email, ticketing, or budget writes).
- No third agent, no planner/critic loop, no vector store or RAG. The policy is short, and the deterministic engine covers it.
- No persistence beyond a local JSONL audit log, and no auth or multi-user state.
- No LLM-based injection classifier. The regex heuristic plus the prompt rule plus the code-owned approvals are enough at this scale, and the LLM can still add the flag.
