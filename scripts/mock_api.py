"""Start the mock vendor-risk API on demand (used by evals, e2e tests, run_local).

The starter pack's eval runner assumed the API was already up; without it every
vendor lookup fails. ``ensure_mock_api()`` reuses a healthy API if one is
running, otherwise starts one on the port in VENDOR_RISK_BASE_URL and stops it
again afterwards.
"""
from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterator
from urllib.parse import urlparse

import requests

ROOT = Path(__file__).resolve().parents[1]


def base_url() -> str:
    return os.getenv("VENDOR_RISK_BASE_URL", "http://127.0.0.1:8001").rstrip("/")


def host_port(url: str | None = None) -> tuple[str, int]:
    parsed = urlparse(url or base_url())
    return parsed.hostname or "127.0.0.1", parsed.port or 8001


def is_healthy(url: str | None = None, timeout: float = 0.5) -> bool:
    try:
        return requests.get(f"{(url or base_url())}/health", timeout=timeout).ok
    except requests.RequestException:
        return False


def start_mock_api(env: dict | None = None, url: str | None = None, quiet: bool = True) -> subprocess.Popen:
    host, port = host_port(url)
    cmd = [sys.executable, "-m", "uvicorn", "mock_api.app:app", "--host", host, "--port", str(port),
           "--log-level", "warning"]
    out = subprocess.DEVNULL if quiet else None
    return subprocess.Popen(cmd, cwd=ROOT, env={**os.environ, **(env or {})}, stdout=out, stderr=out)


def wait_healthy(proc: subprocess.Popen, url: str | None = None, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"Mock API exited during startup with code {proc.returncode} (port in use?)")
        if is_healthy(url):
            return
        time.sleep(0.2)
    raise RuntimeError(f"Mock API did not become healthy within {timeout:.0f}s")


def stop(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


@contextlib.contextmanager
def ensure_mock_api(env: dict | None = None, url: str | None = None, force_new: bool = False) -> Iterator[str]:
    """Yield the API base URL, starting the API only if it is not already healthy."""
    url = url or base_url()
    if not force_new and is_healthy(url):
        yield url
        return
    proc = start_mock_api(env=env, url=url)
    try:
        wait_healthy(proc, url)
        yield url
    finally:
        stop(proc)
