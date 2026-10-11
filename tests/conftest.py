from __future__ import annotations

import sys
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def fake_api(monkeypatch):
    """Serve the real mock_api app in-process (no port) for vendor-risk calls."""
    from fastapi.testclient import TestClient

    from mock_api.app import app
    from src.tools import data_tools

    client = TestClient(app)

    def _get(vendor_name: str, timeout_seconds: float = 3.0) -> dict:
        r = client.get(f"/vendor-risk/{vendor_name}")
        if r.status_code >= 400:
            resp = requests.Response()
            resp.status_code = r.status_code
            resp._content = r.content
            raise requests.HTTPError(f"{r.status_code}", response=resp)
        return r.json()

    monkeypatch.setattr(data_tools, "_raw_vendor_risk", _get)
    monkeypatch.setattr("time.sleep", lambda s: None)
    return client
