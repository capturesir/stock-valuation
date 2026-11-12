from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.web.app import app
from src.web.jobs import store


@pytest.fixture
def client():
    store.reset()
    with TestClient(app) as c:
        yield c
    store.reset()

@pytest.fixture(autouse=True)
def isolate_run_history(tmp_path, monkeypatch):
    """Never let a test append to the real out/_run_history.json."""
    from src.web import jobs
    monkeypatch.setattr(jobs, "HISTORY_PATH", tmp_path / "_run_history.json")
