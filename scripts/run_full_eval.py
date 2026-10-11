"""Full evaluation: both architectures (+ rules-only control) on the SAME case set.

Case set = 10 gold cases (6 public + 4 other requests) + perturbation cases
that run against modified temp copies of data/ (via PROCUREMENT_DATA_DIR).
Each LLM run is repeated (default 3x) to measure consistency.

Usage:
  python scripts/run_full_eval.py                       # everything
  python scripts/run_full_eval.py --repeats 1 --archs single --suites gold
Outputs (committed): evals/results/results_<arch>.csv, evals/results/comparison.md,
                     evals/results/runs.jsonl
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
import tempfile
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=False)

from evals.perturbations import PERTURBATIONS, gold_view, materialise  # noqa: E402
from evals.scoring import score, signature  # noqa: E402
from scripts.mock_api import ensure_mock_api  # noqa: E402
from src import data_access as da  # noqa: E402
from src.config import model_name  # noqa: E402
from src.contracts import ProcurementDecision  # noqa: E402
from src.solution import run_pipeline  # noqa: E402

RESULTS = ROOT / "evals" / "results"
PRICE_IN, PRICE_OUT = 2.0, 10.0  # USD per 1M tokens, claude-sonnet-5-5 list price
TEMPLATE_COLS = ["case_id", "architecture", "correct_next_action", "grounded_evidence", "policy_followed",
                 "human_escalation_correct", "latency_ms", "llm_calls", "tool_calls", "notes"]
EXTRA_COLS = ["run", "request_id", "case_source", "approvals", "risk_flags", "missing_count", "recommendation",
              "input_tokens", "output_tokens", "fallback_used", "llm_evidence_dropped", "floor_restorations"]


def public_minimum_check(decision: dict, request_id: str) -> bool | None:
    """Re-use the grader's own checker for the public subset."""
    from evals.run_public_evals import evaluate as grader_evaluate

    cases = json.loads((ROOT / "evals" / "public_cases.json").read_text())
    case = next((c for c in cases if c["request_id"] == request_id), None)
    if case is None:
        return None
    return not grader_evaluate(ProcurementDecision.model_validate(decision), case["expectations"])


def run_one(case: dict, arch: str, rep: int, policy_text: str, data_label: str) -> dict:
    start = time.perf_counter()
    decision = run_pipeline(case["request_id"], arch).model_dump()
    wall = (time.perf_counter() - start) * 1000
    s = score(decision, case, policy_text)
    tel = decision.get("telemetry") or {}
    actions = tel.get("guardrail_actions", [])
    return {
        "case_id": case["case_id"], "architecture": arch, "run": rep, "request_id": case["request_id"],
        "case_source": case["source"], "data": data_label,
        **{k: s[k] for k in ("correct_next_action", "grounded_evidence", "policy_followed", "human_escalation_correct",
                             "action_text_ok")},
        "public_minimum_pass": public_minimum_check(decision, case["request_id"]) if case["source"].startswith("public")
        else None,
        "latency_ms": round(tel.get("latency_ms") or wall, 1),
        "llm_calls": tel.get("llm_calls", 0), "tool_calls": tel.get("tool_calls", 0),
        "input_tokens": tel.get("input_tokens", 0), "output_tokens": tel.get("output_tokens", 0),
        "fallback_used": tel.get("fallback_used", False), "fallback_reason": tel.get("fallback_reason"),
        "llm_evidence_items": tel.get("llm_evidence_items", 0), "llm_evidence_dropped": tel.get("llm_evidence_dropped", 0),
        "floor_restorations": sum(1 for a in actions if a.startswith("restored floor")),
        "guardrail_actions": actions,
        "approvals": decision["required_approvals"], "risk_flags": decision["risk_flags"],
        "missing_count": len(decision["missing_information"]), "recommendation": decision["recommendation"],
        "next_step": decision["next_step"], "signature": signature(decision),
        "failures": s["failures"], "notes": "; ".join(s["failures"]) or "ok",
        "evidence": decision["evidence"],
    }


def run_group(cases, archs, repeats, workers, policy_text, data_dir: Path | None, port: int):
    env_backup = {k: os.environ.get(k) for k in ("PROCUREMENT_DATA_DIR", "VENDOR_RISK_BASE_URL")}
    url = f"http://127.0.0.1:{port}"
    if data_dir:
        os.environ["PROCUREMENT_DATA_DIR"] = str(data_dir)
    else:
        os.environ.pop("PROCUREMENT_DATA_DIR", None)
    os.environ["VENDOR_RISK_BASE_URL"] = url
    label = data_dir.name if data_dir else "data/"
    jobs = [(c, a, r) for c in cases for a in archs for r in range(1, (1 if a == "rules" else repeats) + 1)]
    try:
        with ensure_mock_api(env={"PROCUREMENT_DATA_DIR": str(data_dir)} if data_dir else {}, url=url,
                             force_new=data_dir is not None):
            with ThreadPoolExecutor(max_workers=workers) as pool:
                return list(pool.map(lambda j: run_one(*j, policy_text, label), jobs))
    finally:
        for k, v in env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def pct(xs) -> str:
    xs = [x for x in xs if x is not None]
    return f"{100 * sum(xs) / len(xs):.0f}%" if xs else "n/a"


def p95(xs):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(0.95 * (len(xs) - 1))))] if xs else 0


def summarise(rows: list[dict], arch: str) -> dict:
    rs = [r for r in rows if r["architecture"] == arch]
    by_case = defaultdict(list)
    for r in rs:
        by_case[r["case_id"]].append(r)
    consistency = [Counter(x["signature"] for x in v).most_common(1)[0][1] / len(v) for v in by_case.values()]
    stable_verdict = [len({x["correct_next_action"] for x in v}) == 1 for v in by_case.values()]
    all4 = [all(r[k] for k in ("correct_next_action", "grounded_evidence", "policy_followed",
                               "human_escalation_correct")) for r in rs]
    lat = [r["latency_ms"] for r in rs]
    cost = [(r["input_tokens"] * PRICE_IN + r["output_tokens"] * PRICE_OUT) / 1e6 for r in rs]
    return {
        "arch": arch, "runs": len(rs), "cases": len(by_case),
        "all_criteria": pct(all4),
        "cases_all_runs_pass": sum(1 for v in by_case.values() if all(
            all(x[k] for k in ("correct_next_action", "grounded_evidence", "policy_followed", "human_escalation_correct"))
            for x in v)),
        "correct_next_action": pct([r["correct_next_action"] for r in rs]),
        "action_text_ok": pct([r["action_text_ok"] for r in rs]),
        "grounded_evidence": pct([r["grounded_evidence"] for r in rs]),
        "policy_followed": pct([r["policy_followed"] for r in rs]),
        "human_escalation_correct": pct([r["human_escalation_correct"] for r in rs]),
        "public_minimum": pct([r["public_minimum_pass"] for r in rs]),
        "public_runs": sum(1 for r in rs if r["public_minimum_pass"] is not None),
        "avg_latency_ms": round(statistics.mean(lat)) if lat else 0,
        "p95_latency_ms": round(p95(lat)) if lat else 0,
        "avg_llm_calls": round(statistics.mean(r["llm_calls"] for r in rs), 2) if rs else 0,
        "avg_tool_calls": round(statistics.mean(r["tool_calls"] for r in rs), 2) if rs else 0,
        "avg_tokens": round(statistics.mean(r["input_tokens"] + r["output_tokens"] for r in rs)) if rs else 0,
        "avg_cost_usd": round(statistics.mean(cost), 4) if cost else 0,
        "consistency": f"{100 * statistics.mean(consistency):.0f}%" if consistency else "n/a",
        "stable_verdict": f"{sum(stable_verdict)}/{len(stable_verdict)}",
        "fallbacks": sum(1 for r in rs if r["fallback_used"]),
        "evidence_dropped": sum(r["llm_evidence_dropped"] for r in rs),
        "evidence_proposed": sum(r["llm_evidence_items"] for r in rs),
        "runs_with_floor_restored": sum(1 for r in rs if r["floor_restorations"]),
    }


def write_outputs(rows: list[dict], archs: list[str], meta: dict) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    for arch in archs:
        with (RESULTS / f"results_{arch}.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=TEMPLATE_COLS + EXTRA_COLS, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                if r["architecture"] == arch:
                    w.writerow({**r, "approvals": ";".join(r["approvals"]), "risk_flags": ";".join(r["risk_flags"])})
    with (RESULTS / "runs.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, default=str) + "\n")

    sums = {a: summarise(rows, a) for a in archs}
    cols = [a for a in ("single", "staged", "rules") if a in sums]
    names = {"single": "Single agent", "staged": "Staged / 2-agent", "rules": "Rules-only control (no LLM)"}
    lines = [f"# Evaluation comparison\n",
             f"Generated {meta['generated']} by `scripts/run_full_eval.py`. Model `{meta['model']}`, "
             f"{meta['repeats']} repeat(s) per LLM case (rules-only control runs once), {meta['workers']} parallel workers, "
             f"{meta['n_cases']} cases ({meta['n_gold']} gold incl. 6 public + {meta['n_pert']} perturbations).\n",
             "Latency is end-to-end per request measured inside `run_pipeline`, with "
             f"{meta['workers']} requests in flight at once (sequential latencies from the grader runner are in "
             "`evals/results_<arch>.csv`).\n",
             "| Metric | " + " | ".join(names[c] for c in cols) + " |",
             "|---|" + "---:|" * len(cols)]

    def row(label, key, fmt=str):
        lines.append(f"| {label} | " + " | ".join(fmt(sums[c][key]) for c in cols) + " |")

    lines.append("| Public cases meeting minimum expectations (grader checker, all runs) | " + " | ".join(
        f"{sums[c]['public_minimum']} of {sums[c]['public_runs']} runs" for c in cols) + " |")
    row("Runs passing all 4 quality criteria", "all_criteria")
    lines.append("| Cases passing all criteria in every repeat | " + " | ".join(
        f"{sums[c]['cases_all_runs_pass']}/{sums[c]['cases']}" for c in cols) + " |")
    row("correct_next_action", "correct_next_action")
    row("  ↳ next-action text mentions the right action", "action_text_ok")
    row("grounded_evidence", "grounded_evidence")
    row("policy_followed", "policy_followed")
    row("human_escalation_correct", "human_escalation_correct")
    row("Avg latency (ms)", "avg_latency_ms")
    row("p95 latency (ms)", "p95_latency_ms")
    row("Avg LLM calls", "avg_llm_calls")
    row("Avg tool calls", "avg_tool_calls")
    row("Avg tokens / request", "avg_tokens")
    row("Avg model cost / request (USD, list price)", "avg_cost_usd")
    row("Consistency across repeats (modal approvals+flags signature)", "consistency")
    row("Cases with identical pass/fail verdict across repeats", "stable_verdict")
    row("Fallbacks to rules-only (LLM failure)", "fallbacks")
    lines.append("| LLM evidence items dropped by grounding guardrail | " + " | ".join(
        f"{sums[c]['evidence_dropped']} / {sums[c]['evidence_proposed']}" for c in cols) + " |")
    row("Runs where guardrail restored floor items the LLM omitted", "runs_with_floor_restored")

    # per-case table
    lines += ["\n## Per-case correct_next_action (passing runs / runs)\n",
              "| Case | Source | " + " | ".join(cols) + " |", "|---|---|" + "---:|" * len(cols)]
    case_ids = list(dict.fromkeys(r["case_id"] for r in rows))
    for cid in case_ids:
        src = next(r["case_source"] for r in rows if r["case_id"] == cid)
        cells = []
        for c in cols:
            rr = [r for r in rows if r["case_id"] == cid and r["architecture"] == c]
            cells.append(f"{sum(r['correct_next_action'] for r in rr)}/{len(rr)}" if rr else "-")
        lines.append(f"| {cid} | {src} | " + " | ".join(cells) + " |")

    # failures
    lines.append("\n## Failures found (auto-scored)\n")
    any_fail = False
    for c in cols:
        fails = Counter()
        for r in rows:
            if r["architecture"] == c:
                for f in r["failures"]:
                    fails[f"{r['case_id']}: {f}"] += 1
        if fails:
            any_fail = True
            lines.append(f"**{names[c]}**\n")
            lines += [f"- {k} (x{v})" for k, v in fails.most_common()]
            lines.append("")
    if not any_fail:
        lines.append("None.")

    # guardrail interventions
    lines.append("\n## Guardrail interventions (what the code had to correct)\n")
    for c in cols:
        acts = Counter()
        for r in rows:
            if r["architecture"] == c:
                for a in r["guardrail_actions"]:
                    acts[a.split(":")[0].split("(")[0].strip()] += 1
        if acts:
            lines.append(f"**{names[c]}**\n")
            lines += [f"- {k} (x{v})" for k, v in acts.most_common(12)]
            lines.append("")
    (RESULTS / "comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (RESULTS / "summary.json").write_text(json.dumps({"meta": meta, "summary": sums}, indent=2), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=int(os.getenv("EVAL_REPEATS", "3")))
    ap.add_argument("--archs", default="single,staged,rules")
    ap.add_argument("--suites", default="gold,perturb")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--only", default="", help="comma-separated case ids")
    args = ap.parse_args()
    archs = [a.strip() for a in args.archs.split(",") if a.strip()]
    only = {c.strip() for c in args.only.split(",") if c.strip()}
    policy_text = da.load_policy_text()

    gold = json.loads((ROOT / "evals" / "gold_cases.json").read_text()) if "gold" in args.suites else []
    perts = PERTURBATIONS if "perturb" in args.suites else []
    if only:
        gold = [g for g in gold if g["case_id"] in only]
        perts = [p for p in perts if p["case_id"] in only]

    rows: list[dict] = []
    t0 = time.perf_counter()
    if gold:
        print(f"Running {len(gold)} gold cases x {archs} ...", flush=True)
        rows += run_group(gold, archs, args.repeats, args.workers, policy_text, None, 8001)
    with tempfile.TemporaryDirectory(prefix="procurement-perturb-") as tmp:
        for i, p in enumerate(perts):
            d = materialise(p, Path(tmp))
            print(f"Running perturbation {p['case_id']} ...", flush=True)
            rows += run_group([gold_view(p)], archs, args.repeats, args.workers, policy_text, d, 8011 + (i % 5))
    meta = {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "model": model_name(),
            "repeats": args.repeats, "workers": args.workers, "n_cases": len(gold) + len(perts),
            "n_gold": len(gold), "n_pert": len(perts), "wall_seconds": round(time.perf_counter() - t0)}
    write_outputs(rows, archs, meta)
    print((RESULTS / "comparison.md").read_text())


if __name__ == "__main__":
    main()
