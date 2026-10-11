# Manual review of evaluation outputs

Besides the auto-scorer, I read the recommendation, next step and guardrail actions of every run, and the
full evidence of the run-1 outputs for all 10 gold cases (both architectures). Sources:
`evals/results/runs.jsonl` and, for the first run, `evals/results/history/run1_*.json|md`.

## Findings from eval run 1 (before the final engine change)

| # | Finding | Architectures | Severity | Action |
|---|---|---|---|---|
| 1 | **Engine gap.** In `P-VENDOR-NOT-IN-REGISTRY` the vendor is missing from the internal registry, but the vendor-risk API returns an approved assessment. Both architectures flagged `conflicting_vendor_evidence` in 6/6 runs. The engine did not, so the guardrail dropped a correct flag. | single 3/3, staged 3/3 (LLM was right) | Medium: a §5 "do not silently choose one" case | **Fixed in code** (`policy_engine.py`). Unit test and gold label updated. Eval re-run (run 2 = reported numbers). |
| 2 | **Inconsistent Privacy addition** for BrandBoard (G-1002 and P-LEGAL-10000). The vendor-risk API says the vendor processes personal data, but the request's data level is `internal_marketing`. The LLM sometimes adds Privacy. | single 2/6, staged 3/6 (run 1) | Low: adding a review is allowed and arguably prudent | Left as LLM judgement and reported as the consistency gap (97%). Promoting it to code would mean deciding policy (§6 keys on the request's data). |
| 3 | `budget_unverified` proposed for the incomplete request (G-1006, cost missing) and dropped by the guardrail (code-owned flag). | single 2/3, staged 3/3 | None | By design: missing cost is reported as missing information, which already blocks routing. |
| 4 | **Gendered pronoun.** One staged Reviewer output referred to a requester as "She" despite the prompt rule. | staged 1/33 gold-case runs, single 0 | Low: wording, but a real quality issue | Prompt rule kept. Listed as a known limitation; no code-level rewrite. |
| 5 | No run dropped a floor item: 0 restorations. Every LLM-proposed evidence item passed the grounding check: 0 / 455 (single) and 0 / 478 (staged) dropped. No authority-violating text. No CFO on the injection request. | both | n/a | The guardrail never had to repair the floor, so its value here is insurance, not correction. |

## Findings from eval run 2 (reported numbers, final code)

| # | Finding | Architectures | Action |
|---|---|---|---|
| 6 | **Single-agent shortcut.** In 4/66 runs (G-1001, G-1007, P-COST-1000.01, P-COST-25000.01) the agent called only `get_request_context` + `evaluate_policy`, then cited `check_budget` / catalog / registry / vendor-risk as sources. The grounding guardrail dropped the 16 items because the LLM never saw those raw outputs. The deterministic findings kept the evidence complete, and all 4 runs were scored correct. | single only | Kept strict. Staged can't shortcut because its Analyst has no `evaluate_policy`. Noted as staged's one real edge. |
| 7 | Privacy inconsistency now only on P-LEGAL-10000 (BrandBoard): 1/3 single, 1/3 staged. Consistency 98% for both. | both | As #2. |
| 8 | Finding #1 fixed: P-VENDOR-NOT-IN-REGISTRY now flags the conflict deterministically, and the guardrail no longer drops it. | both | Done. |
| 9 | No gendered pronouns in any run-2 output (regex check over recommendation, next step and evidence). | both | n/a |

## Quality notes (both architectures, gold cases)

- Next steps are specific and correct: repository controls for the code assistant, unused seats in existing
  licences (SignFlow 45 seats, CodeMate 120, TaskFlow 180, NeuralDesk 150), "use the existing TaskFlow licence
  first", one-time vs recurring cost for the training pack, an unresolved Department Head for Customer
  Success and Sales, and a manual vendor assessment plus retry for the API outage.
- Staged next steps are slightly longer and occasionally add extra (correct) asks, such as SKU questions or
  GDPR for an EU-based requester. I found no case where staged caught a policy issue that single missed,
  or the other way round.
- The rules-only control is correct on approvals, flags and missing info in every case. It fails the next-action content
  check on 4/22 cases (overlap with PixelCraft/CreativeSuite, repository controls, "use existing TaskFlow",
  one-time vs annual). That is the judgement the LLM adds.

## Policy failures found manually (for the comparison table)

- **Single:** 0 policy violations. Shared engine gap #1 (fixed). Inconsistency #2.
- **Staged:** 0 policy violations. Shared engine gap #1 (fixed). Inconsistency #2. Pronoun slip #4.
