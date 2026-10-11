"""Tool runtime shared by every architecture.

``ToolRunner`` is the single place tools are executed. It
- never raises to the caller (exceptions become ``{"status": "error", ...}``),
- caches results by (tool, args) so the deterministic policy floor can reuse
  evidence the agent already fetched instead of calling the API twice,
- records telemetry for every executed call (name, args, status, latency,
  which component asked for it, and whether an LLM saw the result).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class ToolCall:
    name: str
    args: dict
    result: dict
    status: str
    latency_ms: float
    caller: str
    visible_to_llm: bool = False

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "args": self.args,
            "status": self.status,
            "latency_ms": round(self.latency_ms, 1),
            "caller": self.caller,
            "visible_to_llm": self.visible_to_llm,
            "result": self.result,
        }


@dataclass
class ToolContext:
    """Per-run context. ``request_override`` lets the UI analyse an ad-hoc
    request without writing it into the data files."""

    request_override: dict | None = None
    runner: "ToolRunner | None" = None


def _cache_key(name: str, args: dict) -> str:
    return name + ":" + json.dumps(args, sort_keys=True, default=str)


@dataclass
class ToolRunner:
    registry: dict[str, Callable[..., dict]]
    context: ToolContext = field(default_factory=ToolContext)
    calls: list[ToolCall] = field(default_factory=list)
    _cache: dict[str, ToolCall] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.context.runner = self

    def call(self, name: str, args: dict | None = None, caller: str = "code",
             visible_to_llm: bool = False) -> dict:
        args = dict(args or {})
        key = _cache_key(name, args)
        cached = self._cache.get(key)
        if cached is not None:
            # Reuse without re-executing; an LLM seeing a cached result still
            # counts as an LLM-requested call for telemetry purposes.
            if visible_to_llm:
                cached.visible_to_llm = True
                if caller != cached.caller:
                    self.calls.append(ToolCall(name, args, cached.result, cached.status, 0.0, caller, True))
            return cached.result

        fn = self.registry.get(name)
        start = time.perf_counter()
        if fn is None:
            result = {"status": "error", "reason": f"Unknown tool '{name}'"}
        else:
            try:
                result = fn(**args, ctx=self.context)
                if not isinstance(result, dict):
                    result = {"status": "error", "reason": "Tool returned a non-dict result"}
            except TypeError as exc:  # bad/missing arguments from the model
                result = {"status": "error", "reason": f"Invalid arguments: {exc}"}
            except Exception as exc:  # noqa: BLE001 - tools must never raise to the agent
                result = {"status": "error", "reason": f"{type(exc).__name__}: {exc}"}
        latency = (time.perf_counter() - start) * 1000
        record = ToolCall(name, args, result, str(result.get("status", "ok")), latency, caller, visible_to_llm)
        self.calls.append(record)
        if fn is not None:
            self._cache[key] = record
        return result

    def mark_visible(self, name: str) -> None:
        for c in self.calls:
            if c.name == name:
                c.visible_to_llm = True

    def visible_tool_names(self) -> set[str]:
        return {c.name for c in self.calls if c.visible_to_llm}

    def outputs_for(self, names: set[str] | None = None, visible_only: bool = True) -> list[dict]:
        return [c.result for c in self.calls
                if (not visible_only or c.visible_to_llm) and (names is None or c.name in names)]

    def trace(self) -> list[dict]:
        return [c.as_dict() for c in self.calls]
