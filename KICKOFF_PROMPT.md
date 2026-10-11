Read CLAUDE.md fully, then read docs/Assignment_3_Brief.pdf, README.md, data/procurement_policy.md,
data/README.md, src/contracts.py, evals/README.md, evals/public_cases.json and templates/*.
Then build the full submission in the phases below. Commit after each phase. Run tests before each
commit. Only stop to ask me if you are blocked (e.g. no ANTHROPIC_API_KEY in .env).

## Phase 0 — Setup and scaffold diagnosis
- Create .venv, install requirements, run `python verify_setup.py` and the existing tests.
- Verify every scaffold issue listed in CLAUDE.md, fix the real ones, and look for others
  (run the eval runner without the API up, run from another cwd, NaN handling, etc.).
- `git init` if needed. Add `anthropic` to requirements.txt; put pytest + playwright in requirements-dev.txt.

## Phase 1 — Data layer + tools (`src/tools/`)
Each tool is a plain Python function with a JSON schema, returns structured dicts, never raises
to the agent (errors become `{status:"error"|"unavailable", reason}`), and records telemetry.
Minimum set (≥3 required, ≥1 deterministic — we will have several deterministic ones):
1. `get_request_context(request_id)` — request + requester + manager + department head + budget row.
2. `check_budget(department, annual_cost)` — deterministic.
3. `search_software_catalog(product, vendor, category, use_case)` — deterministic matching on
   vendor/product/category plus returns candidates for the LLM to judge use-case fit; includes
   purchase history for the vendor.
4. `get_vendor_registry(vendor_name)` — internal vendors.csv record, with review age in days vs ref date.
5. `get_vendor_risk(vendor_name)` — wraps the mock API; timeouts, 503, 404 and connection errors →
   structured unavailable result.
6. `evaluate_policy(evidence_bundle)` — the deterministic engine from CLAUDE.md: required approvals,
   risk flags, missing info, and the policy section reference for each rule fired.
7. `lookup_policy(section)` — returns policy text for a section so the LLM can cite it.
Unit-test the policy engine thoroughly: every threshold boundary, ≥ vs > on the legal rule, 365-day
expiry boundary, empty-list vs null integrations, NaN handling, vendor absent from registry, API down,
registry/API conflict, injection in request text AND in vendor notes.

## Phase 2 — Architecture A: single-agent baseline (`src/agents/single.py`)
- One LLM agent with tool use over the tools above, then a forced `submit_decision` call producing
  `ProcurementDecision`. System prompt: role, policy summary, untrusted-data rule, "recommend only".
- Guardrail (`src/guardrails.py`, shared by A and B): merge deterministic floor (union approvals,
  flags, missing info), force `human_review_required=True`, drop evidence items that cite a source
  that was never called, dedupe, canonicalise approval names, attach telemetry.
- Fallback: if the LLM fails or times out, return a rules-only decision with recommendation
  "Manual review — AI analysis unavailable" and a clear flag. Never crash the adapter.
- Wire `src/solution.py::handle_request(request_id, architecture)` to dispatch "single" / "staged".
- Run `python evals/run_public_evals.py --architecture single` until 6/6 pass WITHOUT any
  request-specific logic. If a check fails, fix the general rule, not the case.

## Phase 3 — Architecture B: staged / 2-agent (`src/agents/staged.py`)
- Stage 1 "Procurement Analyst": has the data tools, produces a typed `EvidencePack`
  (facts + tool results + uncertainties), makes NO decision.
- Stage 2 "Policy/Risk Reviewer": no data tools (only `lookup_policy`), receives EvidencePack +
  deterministic policy result, produces the decision.
- Same guardrail, same fallback, same telemetry. Keep it lightweight; no extra agents.

## Phase 4 — Product UI (Streamlit, `app.py`)
Sidebar: request picker, architecture toggle, plus a "New request" form so a reviewer can type an
ad-hoc request (validate with the same contract; never mutate data files).
Main view, three panels:
- Request details (clean fields, not raw JSON; untrusted text visibly marked).
- Evidence panel: each item with source tool, finding, reference; tool failures shown in amber.
- Recommendation: recommendation, required approvals (with resolved names), missing information,
  risk flags as chips, next step, telemetry (latency, LLM calls, tool calls).
- Human action bar: "Route for approval", "Request more info", "Reject" — these ONLY append to a local
  audit log (`data/runtime/decisions.jsonl`, gitignored) with reviewer note. Banner: advisory only.
Run the pipeline in a spinner; show the fallback state clearly if the LLM is unavailable.

## Phase 5 — UI verification with Playwright (my standing preference)
- `tests/e2e/test_ui.py` (pytest-playwright): start `run_local.py`, open the UI, pick each public
  request, click Run, assert the evidence/approvals/flags/next-step sections render, assert the
  injection request does NOT show CFO approval, assert the NimbusAI request shows the
  unavailable state. Save screenshots to `docs/screenshots/` for the README.
- If env `CDP_URL` is set, connect with `chromium.connect_over_cdp(CDP_URL)` instead of launching,
  so I can watch it in my own Chrome; otherwise launch headless.
- If Claude in Chrome is available in this session, also eyeball the running UI with it and fix
  anything that looks broken.

## Phase 6 — Evaluation (`scripts/run_full_eval.py`)
- Starts the mock API if needed. Runs BOTH architectures on the SAME case set:
  6 public cases + gold cases for the other 4 requests (from CLAUDE.md table) + perturbation cases
  built by a test fixture that writes temp data copies (no edits to real data): cost at threshold
  boundaries, vendor notes containing an injection, API forced down for an otherwise clean vendor,
  expired review date, vendor missing from registry, unknown employee. Point the data layer at the
  temp dir via an env var (e.g. `PROCUREMENT_DATA_DIR`) — this also proves no hardcoding.
- Optionally include a third "rules-only control" row (deterministic engine, no LLM) to show what the
  LLM actually adds. It is a control, not a shipping candidate.
- Repeat each LLM run 3× (configurable) to measure consistency.
- Per case, auto-score the template columns: correct_next_action (approvals/flags/missing match gold),
  grounded_evidence (every evidence item cites a tool actually called and its numbers appear in that
  tool's output), policy_followed (output ⊇ deterministic floor, no forbidden flags),
  human_escalation_correct, latency_ms, llm_calls, tool_calls.
- Write `evals/results/results_<arch>.csv`, `evals/results/comparison.md` (table from evals/README.md
  incl. pass rate, avg/p95 latency, avg LLM and tool calls, consistency across repeats, failures found).
- Also run the grader's `evals/run_public_evals.py` for both archs and commit its CSVs.

## Phase 7 — Docs and submission package
- `README.md`: one-command setup/run, product workflow, architecture (Mermaid diagrams for A and B
  plus the end-to-end workflow), tools table (deterministic vs LLM), agents and handoff format,
  assumptions, evaluation results (real numbers), architecture comparison, ship decision, known
  limitations, scaffold fixes, screenshots.
- `docs/architecture_decision.md` from the template, ≤500 words (add a test that counts words).
  Decide the ship choice FROM THE DATA. If staged is not clearly better on quality, ship single and
  say why (latency/cost/complexity). Do not pre-decide.
- `docs/workflow_notes.md` filled from the template.
- Optional one-command convenience: `make run`, `make eval`, `make test`.
- Final checks: walk through STUDENT_CHECKLIST.md item by item and report status; clean-clone test
  (fresh venv in /tmp, install, verify_setup, tests, evals); `git grep` for keys; confirm `.env` is
  ignored and results are committed.
- Tell me the exact `gh repo create ... --public --source . --push` command to run (or run it if gh
  is authenticated and I confirm).

At the end, give me: a summary of what was built, the comparison table, the ship decision, the
scaffold bugs fixed, and anything you were unsure about.
