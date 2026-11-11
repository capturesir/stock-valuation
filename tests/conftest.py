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
