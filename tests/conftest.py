"""Shared pytest fixtures.

Environment is configured *before* importing the backend so the cached
settings singleton points at an isolated test database and deterministic
offline providers. Live credentials in .env are deliberately overridden:
tests must never write to a real Jira or GitHub account.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TEST_DB = ROOT / "tests" / ".test_agent.db"
for suffix in ("", "-wal", "-shm"):
    candidate = Path(str(TEST_DB) + suffix)
    if candidate.exists():
        candidate.unlink()

os.environ.update(
    {
        "PM_DB_PATH": str(TEST_DB),
        "APP_ENV": "test",
        "AI_PROVIDER": "mock",
        "AI_ALLOW_MOCK_FALLBACK": "false",
        "SCHEDULER_ENABLED": "false",
        "CONNECTORS_ALLOW_MOCK": "true",
        "DEMO_PASSWORD": "demo1234",
        # Force every connector offline even if .env has live credentials.
        "JIRA_BASE_URL": "",
        "JIRA_EMAIL": "",
        "JIRA_API_TOKEN": "",
        "GITHUB_TOKEN": "",
        "GITHUB_REPO": "",
        "TEAMS_ACCESS_TOKEN": "",
        "TEAMS_TENANT_ID": "",
        "TEAMS_CLIENT_ID": "",
        "TEAMS_CLIENT_SECRET": "",
    }
)

from fastapi.testclient import TestClient  # noqa: E402

from backend.config import settings  # noqa: E402
from backend.database import init_db  # noqa: E402
from backend.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def seeded_database():
    asyncio.run(init_db())
    yield
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(TEST_DB) + suffix)
        if candidate.exists():
            candidate.unlink()


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def project_key() -> str:
    return settings.JIRA_PROJECT_KEY


def login(client: TestClient, username: str, password: str = "demo1234") -> str:
    response = client.post(
        "/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    return response.json()["token"]


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="session")
def pm_token(client: TestClient) -> str:
    return login(client, "sarah")


@pytest.fixture(scope="session")
def member_token(client: TestClient) -> str:
    return login(client, "alex")


@pytest.fixture(scope="session")
def admin_token(client: TestClient) -> str:
    return login(client, "admin")
