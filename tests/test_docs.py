from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MEMO = ROOT / "docs" / "architecture_decision.md"


@pytest.mark.skipif(not MEMO.exists(), reason="memo not written yet")
def test_architecture_memo_is_at_most_500_words():
    text = MEMO.read_text(encoding="utf-8")
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)        # ignore comments
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'%$.,/\-]*", text)
    assert len(words) <= 500, f"memo has {len(words)} words"


def test_no_secrets_committed():
    for p in ROOT.rglob("*"):
        if any(part in {".venv", ".git", "node_modules"} for part in p.parts) or p.name == ".env" or not p.is_file():
            continue
        if p.suffix in {".png", ".pdf", ".pyc"}:
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        assert not re.search(r"sk-ant-[A-Za-z0-9_\-]{20,}", text), f"possible API key in {p}"


def test_src_has_no_request_or_vendor_hardcoding():
    names = ["REQ-10", "SignFlow", "BrandBoard", "CodeMate", "NeuralDesk", "GrowthForge", "SignalWatch", "TaskFlow",
             "NimbusAI", "PixelCraft", "CreativeSuite", "HelpDeskly", "E007", "Go To Market", "Customer Success"]
    for p in (ROOT / "src").rglob("*.py"):
        text = p.read_text(encoding="utf-8")
        for n in names:
            assert n not in text, f"{n!r} hardcoded in {p.relative_to(ROOT)}"
