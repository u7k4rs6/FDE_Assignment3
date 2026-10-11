"""Numeric grounding check shared by the guardrail and the eval scorer.

An evidence finding is grounded when (a) its source is a tool that actually
ran and (b) every number it states appears in tool output (or the policy).
"""
from __future__ import annotations

import json
import re

_NUM = re.compile(r"(?<![\w.])\$?(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s?([kK]\b)?")
ALWAYS_OK = {str(i) for i in range(0, 13)} | {"365"}


def _canon(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def numbers_in(text: str) -> set[str]:
    out = set()
    for whole, frac, k in _NUM.findall(text or ""):
        v = float(whole.replace(",", "") + (frac or ""))
        if k:
            v *= 1000
        out.add(_canon(v))
    return out


def numbers_in_outputs(outputs: list[dict], extra_texts: list[str] | None = None) -> set[str]:
    nums = set()
    for o in outputs:
        nums |= numbers_in(json.dumps(o, default=str))
    for t in extra_texts or []:
        nums |= numbers_in(t)
    return nums


def ungrounded_numbers(finding: str, allowed: set[str]) -> set[str]:
    return {n for n in numbers_in(finding) if n not in allowed and n not in ALWAYS_OK}
