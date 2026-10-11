"""Deterministic prompt-injection heuristic (policy §9).

Runs over ALL untrusted text (request fields, vendor registry notes, catalog /
purchase-history notes, vendor-risk API notes). It only *detects*; the copilot
never follows the embedded instruction.
"""
from __future__ import annotations

import re

_ROLES = r"(?:cfo|ceo|coo|vp|director|manager|finance|security|legal|privacy|procurement|department head)"

INJECTION_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("ignore_rules", re.compile(
        r"\b(?:ignore|disregard|forget)\b[^.!?\n]{0,40}\b(?:rules?|instructions?|polic(?:y|ies)|controls?|guidelines?|checks?|reviews?|prompt)\b",
        re.I)),
    ("fabricated_approval", re.compile(
        r"\b(?:treat|consider|mark|record|log|flag)\b[^.!?\n]{0,40}\bas\b[^.!?\n]{0,25}\b(?:pre-?approved|approved|authori[sz]ed|signed[- ]off|exempt)\b",
        re.I)),
    ("claimed_role_approval", re.compile(rf"\b{_ROLES}[- ](?:approved|authori[sz]ed|signed[- ]off)\b", re.I)),
    ("auto_approve", re.compile(
        r"\b(?:approve|authori[sz]e)\b[^.!?\n]{0,30}\b(?:immediately|right away|automatically|without (?:review|approval))\b",
        re.I)),
    ("bypass_controls", re.compile(
        r"\b(?:bypass|override|circumvent|disable|waive)\b[^.!?\n]{0,30}\b(?:security|review|approvals?|polic(?:y|ies)|procurement|controls?|checks?)\b",
        re.I)),
    ("role_hijack", re.compile(
        r"\b(?:you are now|pretend to be|new instructions|system prompt|developer mode)\b|(?:^|[\s\[<])(?:system|assistant)\s*:", re.I)),
    ("secret_exfiltration", re.compile(
        r"\b(?:reveal|print|show|output|send|leak)\b[^.!?\n]{0,30}\b(?:api[- ]?keys?|secrets?|passwords?|credentials?|system prompt|instructions)\b",
        re.I)),
    ("no_review_needed", re.compile(
        r"\bno (?:need for|need to|further)\b[^.!?\n]{0,20}\b(?:security|privacy|legal|review|approval)s?\b", re.I)),
]

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


def scan_text(text: str | None) -> list[dict]:
    if not text or not isinstance(text, str):
        return []
    hits = []
    for name, pattern in INJECTION_PATTERNS:
        m = pattern.search(text)
        if m:
            hits.append({"pattern": name, "snippet": m.group(0)[:120]})
    return hits


def scan_fields(fields: dict[str, object]) -> list[dict]:
    """``fields`` maps a field label (e.g. 'request.business_justification') to text."""
    hits: list[dict] = []
    for label, value in fields.items():
        values = value if isinstance(value, list) else [value]
        for v in values:
            for hit in scan_text(v if isinstance(v, str) else None):
                hits.append({"field": label, **hit})
    return hits


def strip_injection_sentences(text: str) -> str:
    sentences = [s for s in _SENTENCE_SPLIT.split(text or "") if s.strip()]
    return " ".join(s for s in sentences if not scan_text(s))


# Words that carry urgency or filler but no business purpose.
_FILLER = {
    "need", "needs", "needed", "want", "wants", "asap", "urgent", "urgently", "immediately", "now",
    "quickly", "fast", "today", "please", "right", "away", "soon", "get", "must", "have", "has",
    "the", "this", "that", "for", "and", "our", "we", "you", "it", "its", "with", "very", "really",
    "just", "can", "will", "would", "should", "tool", "tools", "software", "something", "thing",
    "buy", "purchase", "request", "approve", "approved", "help",
}


def has_real_purpose(text: str | None) -> bool:
    """False when the justification is empty or only urgency / injection text."""
    if not text or not str(text).strip():
        return False
    cleaned = strip_injection_sentences(str(text))
    words = re.findall(r"[a-zA-Z][a-zA-Z\-]+", cleaned.lower())
    content = [w for w in words if w not in _FILLER and len(w) > 2]
    return len(content) >= 2
