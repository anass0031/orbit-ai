"""Test setup: throwaway SQLite database + local-folder object storage (no Docker needed)."""
import os
import tempfile
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="orbit_s3_tests_"))
os.environ.update({
    "ORBIT_ENV": "test",
    "ORBIT_DATABASE_URL": f"sqlite:///{(_TMP / 'test.db').as_posix()}",
    "ORBIT_STORAGE_BACKEND": "local",
    "ORBIT_DATA_DIR": str(_TMP / "runtime"),
    "ORBIT_JWT_SECRET": "test-secret-that-is-long-enough-for-hs256-0123456789",
    "ORBIT_DEMO_PASSWORD": "demo-pass-123",
    "ORBIT_LOG_LEVEL": "WARNING",
})

from fastapi.testclient import TestClient  # noqa: E402

from app.core.ratelimit import login_throttle  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.seed import seed  # noqa: E402
from app.db.session import get_engine, session_scope  # noqa: E402
from app.main import app  # noqa: E402

PASSWORD = "demo-pass-123"


@pytest.fixture(autouse=True)
def fresh_db():
    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with session_scope() as s:
        seed(s)
    login_throttle.reset()
    yield


@pytest.fixture
def client():
    return TestClient(app)


def token_for(client: TestClient, email: str, password: str = PASSWORD) -> str:
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest.fixture
def as_admin(client):
    return {"Authorization": f"Bearer {token_for(client, 'admin@orbit.local')}"}


@pytest.fixture
def as_engineer(client):
    return {"Authorization": f"Bearer {token_for(client, 'engineer@orbit.local')}"}


@pytest.fixture
def as_analyst(client):
    return {"Authorization": f"Bearer {token_for(client, 'analyst@orbit.local')}"}
