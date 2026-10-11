from __future__ import annotations

import html
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import data_access as da  # noqa: E402
from src.config import runtime_dir  # noqa: E402
from src.solution import handle_request_payload, run_pipeline  # noqa: E402

st.set_page_config(page_title="Procurement Request Copilot", page_icon="🧾", layout="wide")

st.markdown("""
<style>
.chip{display:inline-block;padding:2px 10px;margin:2px 4px 2px 0;border-radius:999px;font-size:0.82rem;
      font-family:ui-monospace,monospace;border:1px solid rgba(128,128,128,.35);background:rgba(128,128,128,.12)}
.chip.red{background:rgba(220,38,38,.14);border-color:rgba(220,38,38,.45)}
.chip.amber{background:rgba(217,119,6,.16);border-color:rgba(217,119,6,.5)}
.chip.blue{background:rgba(37,99,235,.13);border-color:rgba(37,99,235,.45)}
.untrusted{border-left:4px solid rgba(217,119,6,.8);background:rgba(217,119,6,.08);padding:8px 12px;border-radius:4px}
.untrusted .lbl{font-size:.75rem;text-transform:uppercase;letter-spacing:.04em;opacity:.8}
.ev{border:1px solid rgba(128,128,128,.25);border-radius:6px;padding:8px 12px;margin-bottom:6px}
.ev.fail{border-color:rgba(217,119,6,.7);background:rgba(217,119,6,.08)}
.ev .src{font-family:ui-monospace,monospace;font-size:.78rem;opacity:.85}
.ev .ref{font-size:.75rem;opacity:.7}
.kv td{padding:2px 12px 2px 0;vertical-align:top}
.kv td:first-child{opacity:.7;white-space:nowrap}
</style>
""", unsafe_allow_html=True)

RED_FLAGS = {"budget_insufficient", "prompt_injection_detected", "vendor_risk_unavailable", "conflicting_vendor_evidence",
             "vendor_review_expired", "ai_analysis_unavailable", "copilot_error", "limited_use_scope_exceeded"}
AMBER_FLAGS = {"missing_information", "security_review_required", "privacy_review_required", "legal_review_required",
               "budget_unverified"}


def esc(v: object) -> str:
    return html.escape(str(v)) if v is not None else "—"


def chips(items: list[str], colour_for=lambda x: "") -> str:
    return "".join(f'<span class="chip {colour_for(i)}">{esc(i)}</span>' for i in items) or "<i>none</i>"


def flag_colour(f: str) -> str:
    return "red" if f in RED_FLAGS else "amber" if f in AMBER_FLAGS else "blue"


@st.cache_data
def load_reference():
    reqs = da.load_requests()
    emps = da.records(da.load_employees())
    cats = sorted({c["category"] for c in da.records(da.load_software_catalog()) if c.get("category")})
    return reqs, emps, cats


requests_data, employees, categories = load_reference()
by_id = {r["request_id"]: r for r in requests_data}
emp_by_id = {e["employee_id"]: e for e in employees}

# ------------------------------------------------------------------ sidebar
st.sidebar.title("🧾 Procurement Copilot")
mode = st.sidebar.radio("Request source", ["Existing request", "New request"], horizontal=True)
architecture = st.sidebar.radio("Architecture", ["single", "staged"], horizontal=True,
                                help="single = one agent with all tools; staged = Analyst agent → deterministic engine → Reviewer agent")

request: dict | None = None
if mode == "Existing request":
    rid = st.sidebar.selectbox("Request", list(by_id), format_func=lambda r: f"{r} — {by_id[r]['product_name']}")
    request = by_id[rid]
else:
    with st.sidebar.form("new_request"):
        st.caption("Ad-hoc request. Validated against the same contract; data files are never modified.")
        requester = st.selectbox("Requester", list(emp_by_id),
                                 format_func=lambda e: f"{e} — {emp_by_id[e]['name']} ({emp_by_id[e]['department']})")
        product = st.text_input("Product name")
        vendor = st.text_input("Vendor name")
        category = st.selectbox("Category", categories + ["Other"])
        cost_known = st.checkbox("Annual cost known", value=True)
        cost = st.number_input("Annual cost (USD)", min_value=0.0, step=100.0, value=1000.0)
        users_known = st.checkbox("User count known", value=True)
        users = st.number_input("Users / licences", min_value=1, step=1, value=5)
        justification = st.text_area("Business justification")
        data_level = st.selectbox("Data access level", ["none", "internal_documents", "internal_marketing",
                                                        "confidential_documents", "source_code", "customer_pii",
                                                        "employee_pii", "production_telemetry", "credentials/secrets",
                                                        "unknown"])
        integrations = st.text_input("Integrations (comma separated; blank = none)")
        urgency = st.selectbox("Urgency", ["normal", "high", "urgent"])
        submitted = st.form_submit_button("Use this request", width="stretch")
    if submitted:
        errors = [n for n, v in (("product name", product), ("vendor name", vendor)) if not v.strip()]
        if errors:
            st.sidebar.error("Please provide: " + ", ".join(errors))
        else:
            st.session_state["adhoc"] = {
                "request_id": f"ADHOC-{datetime.now(timezone.utc).strftime('%H%M%S')}",
                "requester_id": requester, "product_name": product.strip(), "vendor_name": vendor.strip(),
                "category": category, "annual_cost_usd": float(cost) if cost_known else None,
                "user_count": int(users) if users_known else None, "business_justification": justification,
                "data_access_level": data_level,
                "requested_integrations": [i.strip() for i in integrations.split(",") if i.strip()],
                "urgency": urgency,
            }
    request = st.session_state.get("adhoc")

run_clicked = st.sidebar.button("Run analysis", type="primary", width="stretch", disabled=request is None)

# ------------------------------------------------------------------ header
st.title("Procurement Request Copilot")
st.info("**Advisory only.** The copilot gathers evidence and recommends a next step. It never approves, purchases, "
        "changes budgets or accepts terms. A human makes every decision.", icon="🛡️")

if request is None:
    st.write("Create a request in the sidebar to begin.")
    st.stop()

key = f"{request['request_id']}|{architecture}|{json.dumps(request, sort_keys=True)}"
if run_clicked:
    with st.spinner(f"Running {architecture} architecture: gathering evidence, applying policy…"):
        if mode == "Existing request":
            result = run_pipeline(request["request_id"], architecture)
        else:
            result = handle_request_payload(request, architecture)
    st.session_state.setdefault("results", {})[key] = result.model_dump()
result = st.session_state.get("results", {}).get(key)

left, right = st.columns([0.9, 1.1], gap="large")

# ------------------------------------------------------------------ request details
with left:
    st.subheader("Request details")
    emp = emp_by_id.get(str(request.get("requester_id")))
    cost = request.get("annual_cost_usd")
    rows = [
        ("Request", request.get("request_id")),
        ("Requester", f"{emp['name']} ({emp['employee_id']}), {emp['department']}" if emp
         else f"{request.get('requester_id')} (not in directory)"),
        ("Product", request.get("product_name")), ("Vendor", request.get("vendor_name")),
        ("Category", request.get("category")),
        ("Annual cost", f"${cost:,.2f}" if isinstance(cost, (int, float)) else "missing"),
        ("Users", request.get("user_count") if request.get("user_count") is not None else "missing"),
        ("Data access", request.get("data_access_level")),
        ("Integrations", ", ".join(request.get("requested_integrations") or []) or
         ("none" if request.get("requested_integrations") == [] else "missing")),
        ("Urgency", request.get("urgency")),
    ]
    st.markdown('<table class="kv">' + "".join(f"<tr><td>{esc(k)}</td><td>{esc(v)}</td></tr>" for k, v in rows)
                + "</table>", unsafe_allow_html=True)
    st.markdown(f'<div class="untrusted"><div class="lbl">⚠ Untrusted requester text — treated as data, not instructions'
                f'</div>{esc(request.get("business_justification") or "(empty)")}</div>', unsafe_allow_html=True)

# ------------------------------------------------------------------ recommendation
with right:
    st.subheader("Recommendation")
    if result is None:
        st.write("Press **Run analysis** in the sidebar.")
    else:
        tel = result.get("telemetry") or {}
        if tel.get("fallback_used"):
            st.error(f"AI analysis unavailable. Showing the deterministic rules-only result. "
                     f"Reason: {tel.get('fallback_reason')}", icon="⚠️")
        st.markdown(f"### {esc(result['recommendation'])}")
        st.markdown("**Next step**")
        st.write(result["next_step"])

        st.markdown("**Required approvals**")
        details = {d["role"]: d for d in result.get("approver_details", [])}
        appr_rows = []
        for a in result["required_approvals"]:
            d = details.get(a, {})
            who = d.get("name") or ("UNRESOLVED" if d.get("status") not in (None, "role queue") else "review queue")
            status = d.get("status", "added by AI reviewer")
            appr_rows.append(f"<tr><td><b>{esc(a)}</b></td><td>{esc(who)}</td><td>{esc(status)}</td></tr>")
        st.markdown('<table class="kv approvals"><tr><td>Approval</td><td>Approver</td><td>Status</td></tr>'
                    + "".join(appr_rows) + "</table>", unsafe_allow_html=True)

        st.markdown("**Risk flags**")
        st.markdown(chips(result["risk_flags"], flag_colour), unsafe_allow_html=True)
        st.markdown("**Missing information**")
        if result["missing_information"]:
            for m in result["missing_information"]:
                st.markdown(f"- {m}")
        else:
            st.markdown("<i>none</i>", unsafe_allow_html=True)

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Latency", f"{(tel.get('latency_ms') or 0) / 1000:.1f} s")
        c2.metric("LLM calls", tel.get("llm_calls", 0))
        c3.metric("Tool calls", tel.get("tool_calls", 0))
        c4.metric("Human review", "required" if result["human_review_required"] else "—")

# ------------------------------------------------------------------ evidence
if result is not None:
    st.divider()
    st.subheader("Evidence")
    failed = [t for t in result.get("tool_trace", []) if t["status"] not in ("ok",)]
    for t in failed:
        st.warning(f"Tool **{t['name']}** returned `{t['status']}`: {t['result'].get('reason', '')}", icon="🟠")
    if result.get("unverified"):
        st.warning("**Could not be verified:** " + "; ".join(result["unverified"]), icon="🟠")
    failing_sources = {t["name"] for t in failed}
    ev_html = []
    for e in result["evidence"]:
        cls = "ev fail" if e["source"] in failing_sources else "ev"
        ev_html.append(f'<div class="{cls}"><span class="src">{esc(e["source"])}</span> · '
                       f'<span class="ref">{esc(e.get("reference"))}</span><br>{esc(e["finding"])}</div>')
    st.markdown("".join(ev_html), unsafe_allow_html=True)

    with st.expander("Guardrail actions & deterministic floor"):
        acts = (result.get("telemetry") or {}).get("guardrail_actions", [])
        st.write(acts or "No guardrail interventions were needed.")
        st.json(result.get("deterministic_floor", {}), expanded=False)
    with st.expander(f"Tool trace ({len(result.get('tool_trace', []))} calls)"):
        st.dataframe([{k: t[k] for k in ("name", "caller", "status", "latency_ms", "visible_to_llm")}
                      for t in result.get("tool_trace", [])], hide_index=True, width="stretch")
    if result.get("evidence_pack"):
        with st.expander("Stage-1 Evidence Pack (staged architecture handoff)"):
            st.json(result["evidence_pack"], expanded=False)

    # -------------------------------------------------------------- human action bar
    st.divider()
    st.subheader("Human decision")
    st.caption("These buttons only append to a local audit log (data/runtime/decisions.jsonl). "
               "Nothing is approved, purchased or sent anywhere.")
    note = st.text_area("Reviewer note", key=f"note-{key}")
    b1, b2, b3 = st.columns(3)
    action = None
    if b1.button("Route for approval", width="stretch"):
        action = "route_for_approval"
    if b2.button("Request more info", width="stretch"):
        action = "request_more_info"
    if b3.button("Reject", width="stretch"):
        action = "reject"
    if action:
        entry = {"logged_at": datetime.now(timezone.utc).isoformat(), "request_id": result["request_id"],
                 "architecture": architecture, "action": action, "note": note,
                 "copilot_recommendation": result["recommendation"],
                 "required_approvals": result["required_approvals"], "risk_flags": result["risk_flags"]}
        with (runtime_dir() / "decisions.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        st.success(f"Logged '{action}' for {result['request_id']} in the local audit log.")

    log = runtime_dir() / "decisions.jsonl"
    if log.exists():
        with st.expander("Audit log (latest 10)"):
            lines = log.read_text(encoding="utf-8").strip().splitlines()[-10:]
            st.dataframe([json.loads(x) for x in reversed(lines)], hide_index=True, width="stretch")
