# Architecture Decision Memo

## Decision
Ship **A, the single agent**. It ties staged on quality, runs about 45% faster and costs about 27% less, with one fewer moving part.

## Evidence
Same 22 cases for both: 6 public, gold labels for the other 4 requests, and 12 perturbations on temp data copies (boundaries, injected notes, API down, 365/366-day reviews, unknown vendor/employee, no budget row). 3 repeats each, `claude-sonnet-5-5`. Source: `evals/results/comparison.md`.

| Metric | Single agent | Staged / 2-agent |
|---|---:|---:|
| Cases passing all 4 criteria, every repeat | 22/22 (100% of 66 runs) | 22/22 (100% of 66 runs) |
| Public minimum checks (grader runner) | 6/6 | 6/6 |
| Avg latency, sequential grader run | 12.9 s | 23.8 s |
| Avg / p95 latency, full eval (4 in flight) | 12.9 s / 15.5 s | 23.2 s / 25.6 s |
| Avg LLM calls | 3.0 | 4.1 |
| Avg tool calls | 6.9 | 6.9 |
| Cost / request (list price) | $0.046 | $0.063 |
| Consistency across repeats | 98% | 98% |
| Notable policy/grounding failures | 0 policy misses; 16/455 evidence items cited tools it skipped (guardrail dropped them) | 0 policy misses; 0/480 dropped; 1 gendered-pronoun slip (run 1) |

The rules-only control (no LLM) scores 82%. Approvals, flags and missing info are right everywhere, but it misses the judgement calls: overlap with PixelCraft, repository controls, "use the TaskFlow licence you already have", one-time vs annual cost. That's where the LLM earns its keep.

## Trade-offs
- **Staged buys:** cleaner evidence attribution. Its Analyst has to call the raw data tools. Single sometimes shortcuts through `evaluate_policy` and then cites tools whose raw output it never saw. The guardrail catches that, and the outputs were still right.
- **Staged costs:** an extra LLM hop (+1.1 calls), about 10 s more per request, 37% more spend, a second prompt to maintain, and a typed handoff to keep in sync.
- **Quality:** identical. Reading every next step, neither caught anything the other missed.

The heavy lifting is in code either way: deterministic policy floor, code-owned approvals, grounding check, untrusted-data wrapping, rules-only fallback. Both architectures share all of it, so splitting the LLM work in two doesn't buy any extra safety.

## Risks / limitations
Validate before production:
- Gold labels came from the engine's authors. Get Procurement/Security to label a fresh batch.
- Messier real text. The injection regex is a heuristic: a miss can't grant approvals (code-owned), but recall is unmeasured.
- Use-case overlap on a big catalog (right now it's keyword candidates plus LLM judgement).
- Vendor "processes personal data" but the request doesn't: the LLM adds Privacy inconsistently (the 2% gap). Policy owners rule, then it goes into code.
- Approver directory: no named Finance, Security, Legal or CFO people yet.

## Why this is the right MVP
The client needs a fast, auditable first pass that routes to the right humans. One agent plus the deterministic engine and guardrail does that in about 13 s. A second agent adds latency and cost and only improves what the guardrail already enforces. If evidence attribution ever becomes a hard requirement, the cheap fix is to make single call the data tools before `evaluate_policy`, not to add an agent.
