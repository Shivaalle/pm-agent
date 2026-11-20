"""Per-project Jira/GitHub target mapping (credentials stay global)."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from backend.mcp_servers import create_or_update_issue
from tests.conftest import auth

JIRA_TARGET = "ZZZ"
REPO_TARGET = "acme/other-repo"


@pytest.fixture
def targets_set(client: TestClient, admin_token: str):
    headers = auth(admin_token)
    original = client.get("/projects/KAN", headers=headers).json()
    response = client.patch(
        "/projects/KAN",
        headers=headers,
        json={"jira_project_key": JIRA_TARGET, "github_repo": REPO_TARGET},
    )
    assert response.status_code == 200
    yield
    client.patch(
        "/projects/KAN",
        headers=headers,
        json={
            "jira_project_key": original.get("jira_project_key"),
            "github_repo": original.get("github_repo"),
        },
    )


def test_admin_can_set_and_read_project_targets(client: TestClient, admin_token: str, targets_set):
    response = client.get("/projects/KAN", headers=auth(admin_token))
    assert response.status_code == 200
    body = response.json()
    assert body["jira_project_key"] == JIRA_TARGET
    assert body["github_repo"] == REPO_TARGET


def test_pm_can_set_targets_but_team_member_cannot(client: TestClient, pm_token: str, member_token: str, admin_token: str, targets_set):
    response = client.patch(
        "/projects/KAN",
        headers=auth(pm_token),
        json={"jira_project_key": "KAN", "github_repo": "some/repo"},
    )
    assert response.status_code == 200
    blocked = client.patch(
        "/projects/KAN",
        headers=auth(member_token),
        json={"jira_project_key": "KAN", "github_repo": "some/repo"},
    )
    assert blocked.status_code == 403
    # restore for the other tests in this module
    client.patch(
        "/projects/KAN",
        headers=auth(admin_token),
        json={"jira_project_key": JIRA_TARGET, "github_repo": REPO_TARGET},
    )


def test_jira_write_uses_project_target_key():
    payload = {
        "summary": "Per-project target test",
        "description": "Created in the mapped Jira project.",
        "idempotency_label": "ai-pm-test-project-target",
    }
    result = asyncio.run(create_or_update_issue(payload, project_key=JIRA_TARGET))
    assert result["ok"] is True
    assert result["issue_key"].startswith(f"{JIRA_TARGET}-")


def test_project_creation_and_membership_isolation(client: TestClient, admin_token: str):
    created = client.post(
        "/projects",
        headers=auth(admin_token),
        json={
            "project_key": "OP",
            "name": "Ops Portfolio",
            "jira_project_key": "OP",
            "github_repo": "acme/ops-repo",
        },
    )
    assert created.status_code == 201
    assert created.json()["jira_project_key"] == "OP"
    assert created.json()["github_repo"] == "acme/ops-repo"

    response = client.post(
        "/users",
        headers=auth(admin_token),
        json={
            "username": "second_pm_test",
            "display_name": "Second PM",
            "role": "Project Manager",
            "project_key": "KAN",
            "password": "demo1234",
        },
    )
    assert response.status_code == 201
    user_id = response.json()["id"]

    login = client.post(
        "/auth/login", json={"username": "second_pm_test", "password": "demo1234"}
    )
    token = login.json()["token"]
    me = client.get("/auth/me", headers=auth(token))
    assert [p["project_key"] for p in me.json()["projects"]] == ["KAN"]

    assert client.post(
        "/projects/OP/members", headers=auth(admin_token), json={"user_id": user_id}
    ).status_code == 201
    assert (
        client.delete(
            f"/projects/KAN/members/{user_id}", headers=auth(admin_token)
        ).status_code
        == 200
    )

    login = client.post(
        "/auth/login", json={"username": "second_pm_test", "password": "demo1234"}
    )
    me = client.get("/auth/me", headers=auth(login.json()["token"]))
    assert [p["project_key"] for p in me.json()["projects"]] == ["OP"]


def test_agent_reads_context_from_project_targets(monkeypatch, targets_set):
    from backend import agent

    captured: dict[str, object] = {}

    async def fake_prs(repo=None):
        captured["pr_repo"] = repo
        return []

    async def fake_commits(repo=None):
        captured["commit_repo"] = repo
        return []

    async def fake_issues(repo=None):
        captured["issue_repo"] = repo
        return []

    async def fake_jira(project_key=None):
        captured["jira_project"] = project_key
        return []

    monkeypatch.setattr(agent, "get_recent_prs", fake_prs)
    monkeypatch.setattr(agent, "get_commits", fake_commits)
    monkeypatch.setattr(agent, "get_github_issues", fake_issues)
    monkeypatch.setattr(agent, "get_open_issues", fake_jira)

    asyncio.run(agent.fetch_context_node({"project_key": "KAN"}))

    assert captured["pr_repo"] == REPO_TARGET
    assert captured["commit_repo"] == REPO_TARGET
    assert captured["issue_repo"] == REPO_TARGET
    assert captured["jira_project"] == JIRA_TARGET
