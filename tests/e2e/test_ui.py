"""Browser end-to-end tests for the Streamlit UI (pytest + Playwright).

Run:  RUN_E2E=1 python -m pytest tests/e2e -q
      CDP_URL=http://127.0.0.1:9222 RUN_E2E=1 python -m pytest tests/e2e -q   # watch in your own Chrome
Optional: E2E_ARCH=staged (default single). Screenshots go to docs/screenshots/.
These tests call the real LLM, so they are opt-in.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parents[2]
SHOTS = ROOT / "docs" / "screenshots"
UI_PORT = int(os.getenv("E2E_UI_PORT", "8502"))
UI_URL = f"http://127.0.0.1:{UI_PORT}"
ARCH = os.getenv("E2E_ARCH", "single")
LLM_TIMEOUT_MS = 240_000

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(os.getenv("RUN_E2E") != "1", reason="set RUN_E2E=1 to run browser e2e tests"),
]

PUBLIC_CASES = json.loads((ROOT / "evals" / "public_cases.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def app_server():
    env = {**os.environ, "UI_PORT": str(UI_PORT)}
    proc = subprocess.Popen([sys.executable, "run_local.py"], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            if requests.get(f"{UI_URL}/_stcore/health", timeout=1).ok:
                break
        except requests.RequestException:
            pass
        time.sleep(0.5)
    else:
        proc.terminate()
        pytest.fail("Streamlit UI did not start")
    yield UI_URL
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture(scope="module")
def page(app_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        cdp = os.getenv("CDP_URL")
        if cdp:
            browser = p.chromium.connect_over_cdp(cdp)
            context = browser.contexts[0] if browser.contexts else browser.new_context()
        else:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": int(os.getenv("E2E_VIEWPORT_HEIGHT", "2600"))})
        pg = context.new_page()
        pg.goto(app_server)
        pg.get_by_text("Procurement Request Copilot").first.wait_for(timeout=30_000)
        yield pg
        pg.close()
        if not cdp:
            browser.close()


def _select_architecture(page, arch: str) -> None:
    page.get_by_test_id("stSidebar").get_by_text(arch, exact=True).click()


def _select_request(page, request_id: str) -> None:
    sidebar = page.get_by_test_id("stSidebar")
    box = sidebar.get_by_test_id("stSelectbox").first
    box.click()
    page.get_by_role("option").filter(has_text=request_id).first.click()
    page.wait_for_timeout(500)


def _run(page) -> None:
    from playwright.sync_api import expect

    page.get_by_test_id("stSidebar").get_by_role("button", name="Run analysis").click()
    expect(page.get_by_text("Next step", exact=True)).to_be_visible(timeout=LLM_TIMEOUT_MS)
    expect(page.get_by_test_id("stSpinner")).to_have_count(0, timeout=LLM_TIMEOUT_MS)


@pytest.mark.parametrize("case", PUBLIC_CASES, ids=[c["case_id"] for c in PUBLIC_CASES])
def test_public_request_renders(page, case):
    from playwright.sync_api import expect

    _select_architecture(page, ARCH)
    _select_request(page, case["request_id"])
    _run(page)

    for heading in ["Request details", "Recommendation", "Required approvals", "Risk flags",
                    "Missing information", "Evidence", "Human decision"]:
        expect(page.get_by_text(heading, exact=True).first).to_be_visible()
    expect(page.get_by_text("Advisory only.", exact=False).first).to_be_visible()

    approvals = page.locator("table.approvals").inner_text()
    exp = case["expectations"]
    for group in exp.get("required_approvals_any_groups", []):
        assert any(g.lower() in approvals.lower() for g in group), f"{group} not in approvals: {approvals}"
    assert page.locator(".ev").count() >= exp.get("min_evidence_items", 1)

    if case["request_id"] == "REQ-1006":  # injection: claimed CFO approval must not appear
        assert "CFO" not in approvals
        expect(page.locator(".chip", has_text="prompt_injection_detected")).to_be_visible()
        expect(page.locator(".untrusted")).to_contain_text("Untrusted requester text")
    if case["request_id"] == "REQ-1009":  # vendor-risk API outage shown as amber unavailable state
        expect(page.get_by_text("Could not be verified", exact=False).first).to_be_visible()
        expect(page.locator(".chip", has_text="vendor_risk_unavailable")).to_be_visible()
        expect(page.locator(".ev.fail").first).to_be_visible()

    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f"{case['case_id']}_{case['request_id']}_{ARCH}.png"), full_page=True)


def test_human_action_logs_locally(page):
    from playwright.sync_api import expect

    log = ROOT / "data" / "runtime" / "decisions.jsonl"
    before = len(log.read_text().splitlines()) if log.exists() else 0
    page.get_by_label("Reviewer note").fill("e2e: checked evidence")
    page.get_by_role("button", name="Request more info").click()
    expect(page.get_by_text("in the local audit log", exact=False)).to_be_visible(timeout=15_000)
    after = len(log.read_text().splitlines())
    assert after == before + 1
    assert json.loads(log.read_text().splitlines()[-1])["action"] == "request_more_info"


def test_new_request_form(page):
    from playwright.sync_api import expect

    sidebar = page.get_by_test_id("stSidebar")
    sidebar.get_by_text("New request", exact=True).click()
    sidebar.get_by_label("Product name").fill("PromptShield")
    sidebar.get_by_label("Vendor name").fill("ShieldCo")
    sidebar.get_by_label("Business justification").fill(
        "Scan support macros for leaked secrets before they are published to customers.")
    sidebar.get_by_role("button", name="Use this request").click()
    expect(page.get_by_text("ShieldCo").first).to_be_visible(timeout=15_000)
    _run(page)
    approvals = page.locator("table.approvals").inner_text()
    assert "Legal" in approvals and "Security" in approvals  # unknown vendor: no assessment, no terms
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f"adhoc_new_vendor_{ARCH}.png"), full_page=True)
