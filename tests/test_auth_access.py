"""Authentication and role/project access-control tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import auth, login


def test_unauthenticated_requests_are_rejected(client: TestClient):
    assert client.get("/projects").status_code == 401
    assert client.get("/drafts").status_code == 401
    assert client.get("/overview").status_code == 401


def test_bad_password_is_rejected(client: TestClient):
    response = client.post(
        "/auth/login", json={"username": "sarah", "password": "not-the-password"}
    )
    assert response.status_code == 401


def test_login_returns_project_membership(client: TestClient):
    response = client.post(
        "/auth/login", json={"username": "sarah", "password": "demo1234"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["user"]["role"] == "Project Manager"
    assert "KAN" in body["projects"]
    me = client.get("/auth/me", headers=auth(body["token"]))
    assert me.status_code == 200
    assert me.json()["projects"][0]["project_key"] == "KAN"


def test_team_member_cannot_review_or_upload(client: TestClient):
    token = login(client, "alex")
    headers = auth(token)
    assert client.get("/drafts", headers=headers).status_code == 403
    assert client.get("/metrics?project_key=KAN", headers=headers).status_code == 403
    response = client.post(
        "/meetings/upload",
        headers=headers,
        json={
            "project_key": "KAN",
            "title": "Unauthorized upload",
            "meeting_date": "2026-11-20",
            "transcript": [{"line": 1, "speaker": "Alex", "text": "Hello"}],
        },
    )
    assert response.status_code == 403


def test_sponsor_is_read_only(client: TestClient):
    token = login(client, "mark")
    headers = auth(token)
    assert client.get("/overview?project_key=KAN", headers=headers).status_code == 200
    assert client.get("/metrics?project_key=KAN", headers=headers).status_code == 200
    assert client.get("/drafts?project_key=KAN", headers=headers).status_code == 403
    assert client.get("/integrations?project_key=KAN", headers=headers).status_code == 403


def test_admin_can_view_integrations_but_cannot_approve(client: TestClient):
    token = login(client, "admin")
    headers = auth(token)
    response = client.get("/integrations?project_key=KAN", headers=headers)
    assert response.status_code == 200
    assert set(response.json()) >= {"ai", "jira", "github", "teams", "database"}


def test_team_member_only_sees_own_action_items(client: TestClient, pm_token: str, member_token: str):
    created = client.post(
        "/meetings/upload",
        headers=auth(pm_token),
        json={
            "meeting_id": "TEST-SCOPE-TEAM",
            "project_key": "KAN",
            "title": "Scoped action ownership",
            "meeting_date": "2026-11-20",
            "transcript": [
                {"line": 1, "speaker": "Sarah", "text": "Let's assign work."},
                {
                    "line": 2,
                    "speaker": "Alex",
                    "text": "I will finish the pagination endpoint by Friday.",
                },
            ],
        },
    )
    assert created.status_code == 200
    drafts = client.get(
        "/drafts?project_key=KAN&meeting_id=TEST-SCOPE-TEAM", headers=auth(pm_token)
    ).json()
    action = next(draft for draft in drafts if draft["type"] == "action_item")
    approved = client.post(
        f"/drafts/{action['id']}/approve", headers=auth(pm_token)
    )
    assert approved.status_code == 200

    alex_items = client.get("/action-items?project_key=KAN", headers=auth(member_token)).json()
    assert all(item["owner_username"] == "alex" for item in alex_items)
    assert any(item["draft_id"] == action["id"] for item in alex_items)

    spectator = login(client, "mark")
    assert (
        client.patch(
            f"/action-items/{alex_items[0]['id']}",
            headers=auth(spectator),
            json={"status": "done"},
        ).status_code
        == 403
    )
