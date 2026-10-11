# Evaluation comparison

Generated 2026-10-11 18:41 UTC by `scripts/run_full_eval.py`. Model `claude-sonnet-5-5`, 3 repeat(s) per LLM case (rules-only control runs once), 4 parallel workers, 22 cases (10 gold incl. 6 public + 12 perturbations).

Latency is end-to-end per request measured inside `run_pipeline`, with 4 requests in flight at once (sequential latencies from the grader runner are in `evals/results_<arch>.csv`).

| Metric | Single agent | Staged / 2-agent | Rules-only control (no LLM) |
|---|---:|---:|---:|
| Public cases meeting minimum expectations (grader checker, all runs) | 100% of 18 runs | 100% of 18 runs | 100% of 6 runs |
| Runs passing all 4 quality criteria | 100% | 100% | 82% |
| Cases passing all criteria in every repeat | 22/22 | 22/22 | 18/22 |
| correct_next_action | 100% | 100% | 82% |
|   ↳ next-action text mentions the right action | 100% | 100% | 82% |
| grounded_evidence | 100% | 100% | 100% |
| policy_followed | 100% | 100% | 100% |
| human_escalation_correct | 100% | 100% | 100% |
| Avg latency (ms) | 13130 | 23537 | 29 |
| p95 latency (ms) | 14922 | 27343 | 212 |
| Avg LLM calls | 3.06 | 4.11 | 0 |
| Avg tool calls | 6.97 | 6.94 | 5.95 |
| Avg tokens / request | 17655 | 19735 | 0 |
| Avg model cost / request (USD, list price) | 0.0469 | 0.0635 | 0.0 |
| Consistency across repeats (modal approvals+flags signature) | 97% | 97% | 100% |
| Cases with identical pass/fail verdict across repeats | 22/22 | 22/22 | 22/22 |
| Fallbacks to rules-only (LLM failure) | 0 | 0 | 0 |
| LLM evidence items dropped by grounding guardrail | 0 / 455 | 0 / 478 | 0 / 0 |
| Runs where guardrail restored floor items the LLM omitted | 0 | 0 | 0 |

## Per-case correct_next_action (passing runs / runs)

| Case | Source | single | staged | rules |
|---|---|---:|---:|---:|
| G-1001 | public PUB-01 | 3/3 | 3/3 | 1/1 |
| G-1002 | public PUB-02 | 3/3 | 3/3 | 0/1 |
| G-1003 | public PUB-03 | 3/3 | 3/3 | 0/1 |
| G-1004 | gold (non-public) | 3/3 | 3/3 | 1/1 |
| G-1005 | public PUB-04 | 3/3 | 3/3 | 1/1 |
| G-1006 | public PUB-05 | 3/3 | 3/3 | 1/1 |
| G-1007 | gold (non-public) | 3/3 | 3/3 | 1/1 |
| G-1008 | gold (non-public) | 3/3 | 3/3 | 0/1 |
| G-1009 | public PUB-06 | 3/3 | 3/3 | 1/1 |
| G-1010 | gold (non-public) | 3/3 | 3/3 | 0/1 |
| P-COST-1000 | perturbation | 3/3 | 3/3 | 1/1 |
| P-COST-1000.01 | perturbation | 3/3 | 3/3 | 1/1 |
| P-COST-25000.01 | perturbation | 3/3 | 3/3 | 1/1 |
| P-LEGAL-10000 | perturbation | 3/3 | 3/3 | 1/1 |
| P-INJECT-VENDOR-NOTE | perturbation | 3/3 | 3/3 | 1/1 |
| P-INJECT-API-NOTE | perturbation | 3/3 | 3/3 | 1/1 |
| P-API-DOWN-CLEAN | perturbation | 3/3 | 3/3 | 1/1 |
| P-EXPIRED-366 | perturbation | 3/3 | 3/3 | 1/1 |
| P-CURRENT-365 | perturbation | 3/3 | 3/3 | 1/1 |
| P-VENDOR-NOT-IN-REGISTRY | perturbation | 3/3 | 3/3 | 1/1 |
| P-UNKNOWN-EMPLOYEE | perturbation | 3/3 | 3/3 | 1/1 |
| P-NO-BUDGET-ROW | perturbation | 3/3 | 3/3 | 1/1 |

## Failures found (auto-scored)

**Rules-only control (no LLM)**

- G-1002: next action lacks ['pixelcraft', 'creativesuite', 'existing', 'overlap'] (x1)
- G-1003: next action lacks ['repositor', 'repo', 'source code', 'source-code', 'control'] (x1)
- G-1008: next action lacks ['existing'] (x1)
- G-1008: next action lacks ['taskflow'] (x1)
- G-1010: next action lacks ['one-time', 'one time', 'recurring', 'annual'] (x1)


## Guardrail interventions (what the code had to correct)

**Single agent**

- dropped LLM flag 'conflicting_vendor_evidence' (x3)
- kept LLM-added approval 'Privacy' (x2)
- kept LLM-added flag 'privacy_review_required' (x2)
- dropped LLM flag 'budget_unverified' (x2)

**Staged / 2-agent**

- dropped LLM flag 'conflicting_vendor_evidence' (x4)
- kept LLM-added approval 'Privacy' (x3)
- kept LLM-added flag 'privacy_review_required' (x3)
- dropped LLM flag 'budget_unverified' (x3)
- prepended clarification request (x1)

