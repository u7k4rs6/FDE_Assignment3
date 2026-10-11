# Evaluation comparison

Generated 2026-10-11 18:29 UTC by `scripts/run_full_eval.py`. Model `claude-sonnet-5-5`, 1 repeat(s) per LLM case (rules-only control runs once), 4 parallel workers, 22 cases (10 gold incl. 6 public + 12 perturbations).

Latency is end-to-end per request measured inside `run_pipeline`, with 4 requests in flight at once (sequential latencies from the grader runner are in `evals/results_<arch>.csv`).

| Metric | Rules-only control (no LLM) |
|---|---:|
| Public cases meeting minimum expectations (grader checker, all runs) | 100% of 6 runs |
| Runs passing all 4 quality criteria | 82% |
| Cases passing all criteria in every repeat | 18/22 |
| correct_next_action | 82% |
|   ↳ next-action text mentions the right action | 82% |
| grounded_evidence | 100% |
| policy_followed | 100% |
| human_escalation_correct | 100% |
| Avg latency (ms) | 44 |
| p95 latency (ms) | 225 |
| Avg LLM calls | 0 |
| Avg tool calls | 5.95 |
| Avg tokens / request | 0 |
| Avg model cost / request (USD, list price) | 0.0 |
| Consistency across repeats (modal approvals+flags signature) | 100% |
| Cases with identical pass/fail verdict across repeats | 22/22 |
| Fallbacks to rules-only (LLM failure) | 0 |
| LLM evidence items dropped by grounding guardrail | 0 / 0 |
| Runs where guardrail restored floor items the LLM omitted | 0 |

## Per-case correct_next_action (passing runs / runs)

| Case | Source | rules |
|---|---|---:|
| G-1001 | public PUB-01 | 1/1 |
| G-1002 | public PUB-02 | 0/1 |
| G-1003 | public PUB-03 | 0/1 |
| G-1004 | gold (non-public) | 1/1 |
| G-1005 | public PUB-04 | 1/1 |
| G-1006 | public PUB-05 | 1/1 |
| G-1007 | gold (non-public) | 1/1 |
| G-1008 | gold (non-public) | 0/1 |
| G-1009 | public PUB-06 | 1/1 |
| G-1010 | gold (non-public) | 0/1 |
| P-COST-1000 | perturbation | 1/1 |
| P-COST-1000.01 | perturbation | 1/1 |
| P-COST-25000.01 | perturbation | 1/1 |
| P-LEGAL-10000 | perturbation | 1/1 |
| P-INJECT-VENDOR-NOTE | perturbation | 1/1 |
| P-INJECT-API-NOTE | perturbation | 1/1 |
| P-API-DOWN-CLEAN | perturbation | 1/1 |
| P-EXPIRED-366 | perturbation | 1/1 |
| P-CURRENT-365 | perturbation | 1/1 |
| P-VENDOR-NOT-IN-REGISTRY | perturbation | 1/1 |
| P-UNKNOWN-EMPLOYEE | perturbation | 1/1 |
| P-NO-BUDGET-ROW | perturbation | 1/1 |

## Failures found (auto-scored)

**Rules-only control (no LLM)**

- G-1002: next action lacks ['pixelcraft', 'creativesuite', 'existing', 'overlap'] (x1)
- G-1003: next action lacks ['repositor', 'repo', 'source code', 'source-code', 'control'] (x1)
- G-1008: next action lacks ['existing'] (x1)
- G-1008: next action lacks ['taskflow'] (x1)
- G-1010: next action lacks ['one-time', 'one time', 'recurring', 'annual'] (x1)


## Guardrail interventions (what the code had to correct)

