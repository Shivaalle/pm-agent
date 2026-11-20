"""MCP connectors for Jira, GitHub and Microsoft Teams.

The ``@*.tool()`` functions are registered MCP tools. The backend calls the
same broker functions directly for in-process reliability; the MCP server can
also be mounted/run separately (see ``backend/mcp_app.py``). When credentials
are configured, failures are surfaced — they are never silently replaced with
fake success. Deterministic mock data is used only when a connector has no
credentials and ``CONNECTORS_ALLOW_MOCK`` is enabled.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import httpx
from mcp.server.mcpserver import MCPServer

from backend.config import settings
from backend.logger import get_logger

logger = get_logger("mcp_servers")


class ConnectorError(RuntimeError):
    """Raised when a live connector is configured but the call failed."""


class ConnectorNotConfigured(RuntimeError):
    """Raised when a connector has no credentials and mocks are disabled."""


# ---------------------------------------------------------------------------
# MCP servers
# ---------------------------------------------------------------------------

jira_mcp = MCPServer("jira-connector", instructions="Read and write Jira Cloud project work.")
github_mcp = MCPServer("github-connector", instructions="Read GitHub repositories, pull requests, commits and issues.")
teams_mcp = MCPServer("teams-connector", instructions="Read Microsoft Teams meetings and transcripts.")

PROJECT_KEY = settings.JIRA_PROJECT_KEY


# ---------------------------------------------------------------------------
# Mock fixtures (offline demo only)
# ---------------------------------------------------------------------------

_mock_jira_issues: List[Dict[str, Any]] = [
    {
        "key": f"{PROJECT_KEY}-1",
        "summary": "Implement OAuth2 and JWT authentication endpoint",
        "status": "In Progress",
        "assignee": "Alex Rivera",
        "created": "2026-11-10T10:00:00Z",
    },
    {
        "key": f"{PROJECT_KEY}-2",
        "summary": "Design database schema for projects and drafts",
        "status": "In Review",
        "assignee": "David Park",
        "created": "2026-11-12T14:30:00Z",
    },
    {
        "key": f"{PROJECT_KEY}-3",
        "summary": "Set up Docker Compose and CI/CD pipelines",
        "status": "To Do",
        "assignee": "Elena Rossi",
        "created": "2026-11-14T09:15:00Z",
    },
]

_mock_github_prs: List[Dict[str, Any]] = [
    {
        "number": 42,
        "title": "feat(auth): complete OAuth2 token generation and refresh flow",
        "body": "Closes KAN-1",
        "state": "closed",
        "merged": True,
        "draft": False,
        "user": "alex-dev",
        "head": "feat/oauth2",
        "created_at": "2026-11-15T09:00:00Z",
        "merged_at": "2026-11-18T16:45:00Z",
        "html_url": "https://github.com/mock/repo/pull/42",
    },
    {
        "number": 45,
        "title": "feat(auth): login API token expiry fixes",
        "body": "Part of KAN-1. Still failing CI.",
        "state": "open",
        "merged": False,
        "draft": False,
        "user": "alex-dev",
        "head": "fix/login-token-expiry",
        "created_at": "2026-11-19T08:20:00Z",
        "merged_at": None,
        "html_url": "https://github.com/mock/repo/pull/45",
    },
]

_mock_github_commits: List[Dict[str, Any]] = [
    {
        "sha": "a1b2c3d",
        "message": "fix: correct token expiration calculation in auth service (KAN-1)",
        "author": "Alex Rivera",
        "date": "2026-11-18T16:30:00Z",
        "html_url": "https://github.com/mock/repo/commit/a1b2c3d",
    },
    {
        "sha": "e5f6g7h",
        "message": "wip: add notification drawer layout components (KAN-2)",
        "author": "David Park",
        "date": "2026-11-19T08:15:00Z",
        "html_url": "https://github.com/mock/repo/commit/e5f6g7h",
    },
]

_mock_github_issues: List[Dict[str, Any]] = [
    {
        "number": 51,
        "title": "KAN-3: CI pipeline flaky on macOS runners",
        "body": "The build sometimes times out.",
        "state": "open",
        "user": "elena-dev",
        "created_at": "2026-11-18T12:00:00Z",
        "html_url": "https://github.com/mock/repo/issues/51",
    }
]

_mock_teams_meetings: List[Dict[str, Any]] = [
    {
        "id": "teams-meet-sprint14",
        "subject": "Sprint 14 Standup & OAuth Migration Sync",
        "start_time": "2026-11-19T09:30:00Z",
        "organizer": "sarah@company.com",
        "attendees": ["Sarah Chen", "Alex Rivera", "David Park", "Elena Rossi", "Mark Johnson"],
        "series_id": "series-standups-daily",
        "has_transcript": True,
    },
    {
        "id": "teams-meet-arch-review",
        "subject": "Architecture & Security Review",
        "start_time": "2026-11-18T14:00:00Z",
        "organizer": "alex@company.com",
        "attendees": ["Alex Rivera", "Elena Rossi", "Sarah Chen"],
        "series_id": None,
        "has_transcript": True,
    },
    {
        "id": "teams-meet-planning",
        "subject": "Sprint 15 Planning & Backlog Grooming",
        "start_time": "2026-11-20T11:00:00Z",
        "organizer": "sarah@company.com",
        "attendees": ["Sarah Chen", "Alex Rivera", "David Park", "Elena Rossi", "Mark Johnson"],
        "series_id": "series-planning-biweekly",
        "has_transcript": True,
    },
]

_mock_teams_transcripts: Dict[str, List[Dict[str, Any]]] = {
    "teams-meet-sprint14": [
        {"line": 1, "speaker": "Sarah Chen", "text": "Good morning everyone. Let's do a fast standup. Where are we on sprint deliverables?"},
        {"line": 2, "speaker": "Alex Rivera", "text": "I finished the JWT migration scripts yesterday and tested them locally."},
        {"line": 3, "speaker": "Sarah Chen", "text": "Awesome. Are the changes merged to main?"},
        {"line": 4, "speaker": "Alex Rivera", "text": "Yes, PR #42 is merged and the OAuth2 token endpoints are completely live."},
        {"line": 5, "speaker": "David Park", "text": "I'm still finishing the notification toast system on the frontend."},
        {"line": 6, "speaker": "David Park", "text": "I can finish the database schema migrations by Friday."},
        {"line": 7, "speaker": "Alex Rivera", "text": "Regarding task 1, I completed the auth endpoint refactor and it's totally done."},
        {"line": 8, "speaker": "Sarah Chen", "text": "Wait Alex, is PR #45 for task 1 actually merged or still in review?"},
        {"line": 9, "speaker": "Alex Rivera", "text": "Oh wait, PR #45 is still open waiting on David's review. My bad."},
        {"line": 10, "speaker": "Elena Rossi", "text": "Be careful with Auth0 rate limits, we might hit 429 during load spikes."},
        {"line": 11, "speaker": "Elena Rossi", "text": "We need an explicit rate-limit retry policy with exponential backoff."},
        {"line": 12, "speaker": "Sarah Chen", "text": "Agreed. Let's decide on 15-minute access tokens with refresh token rotation for security."},
        {"line": 13, "speaker": "David Park", "text": "Sounds solid. I will configure the refresh token rotation in the client session."},
        {"line": 14, "speaker": "Mark Johnson", "text": "Can we also add FaceID login on the web dashboard this sprint? A customer requested it."},
        {"line": 15, "speaker": "Sarah Chen", "text": "FaceID is out of our baseline scope. We will log it as a Scope Change and assess the impact."},
        {"line": 16, "speaker": "Sarah Chen", "text": "Someone needs to review the security audit checklist before next release."},
        {"line": 17, "speaker": "Elena Rossi", "text": "I will take that action item and finish the security audit checklist by Monday."},
        {"line": 18, "speaker": "David Park", "text": "Also heads up, our cloud hosting tier expires at the end of the month, which is a dependency on DevOps."},
        {"line": 19, "speaker": "Sarah Chen", "text": "Noted as a Dependency. Thanks team, let's execute."},
    ],
    "teams-meet-arch-review": [
        {"line": 1, "speaker": "Alex Rivera", "text": "We are reviewing our database sharding strategy."},
        {"line": 2, "speaker": "Elena Rossi", "text": "Let's decide to stick with PostgreSQL read replicas before adding CockroachDB."},
        {"line": 3, "speaker": "Sarah Chen", "text": "Approved decision. Alex will benchmark replica latency by Thursday."},
    ],
    "teams-meet-planning": [
        {"line": 1, "speaker": "Sarah Chen", "text": "Planning Sprint 15 goals."},
        {"line": 2, "speaker": "David Park", "text": "We need to address the technical debt in API error handlers."},
        {"line": 3, "speaker": "Alex Rivera", "text": "Agreed. I will create the Jira tickets for error standardization."},
    ],
}


# ---------------------------------------------------------------------------
# Jira
# ---------------------------------------------------------------------------


def _jira_headers() -> Dict[str, str]:
    auth = base64.b64encode(
        f"{settings.JIRA_EMAIL}:{settings.JIRA_API_TOKEN}".encode()
    ).decode()
    return {
        "Authorization": f"Basic {auth}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _require_jira_live() -> None:
    if settings.jira_live:
        return
    if settings.CONNECTORS_ALLOW_MOCK:
        return
    raise ConnectorNotConfigured("Jira credentials are not configured")


def _adf(text: str) -> Dict[str, Any]:
    paragraphs = []
    for paragraph in (text or "").split("\n"):
        paragraphs.append(
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": paragraph or " "}],
            }
        )
    return {"type": "doc", "version": 1, "content": paragraphs}


async def _jira_get(path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    url = f"{settings.JIRA_BASE_URL}{path}"
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(url, headers=_jira_headers(), params=params)
    if response.status_code >= 400:
        raise ConnectorError(f"Jira GET {path} failed ({response.status_code}): {response.text[:300]}")
    return response.json()


async def _jira_search(jql: str, fields: str, max_results: int) -> Dict[str, Any]:
    """Use the current JQL endpoint, falling back for older Cloud tenants."""
    params = {"jql": jql, "maxResults": max_results, "fields": fields}
    try:
        return await _jira_get("/rest/api/3/search/jql", params)
    except ConnectorError:
        return await _jira_get("/rest/api/3/search", params)


async def _jira_post(path: str, payload: Dict[str, Any]) -> httpx.Response:
    url = f"{settings.JIRA_BASE_URL}{path}"
    async with httpx.AsyncClient(timeout=20.0) as client:
        return await client.post(url, headers=_jira_headers(), json=payload)


@jira_mcp.tool()
async def get_open_issues(project_key: Optional[str] = None) -> List[Dict[str, Any]]:
    """Fetch open Jira issues for a project (live when configured)."""
    _require_jira_live()
    pkey = project_key or PROJECT_KEY
    if not settings.jira_live:
        return [issue for issue in _mock_jira_issues if issue["key"].startswith(pkey)]
    data = await _jira_search(
        f"project = {pkey} AND statusCategory != Done ORDER BY updated DESC",
        "key,summary,status,assignee,created,labels",
        settings.JIRA_MAX_RESULTS,
    )
    issues = []
    for item in data.get("issues", []):
        fields = item.get("fields", {})
        assignee = fields.get("assignee")
        issues.append(
            {
                "key": item.get("key"),
                "summary": fields.get("summary", ""),
                "status": (fields.get("status") or {}).get("name", "Unknown"),
                "assignee": assignee.get("displayName", "Unassigned") if assignee else "Unassigned",
                "created": fields.get("created"),
                "labels": fields.get("labels", []),
            }
        )
    return issues


async def get_jira_issue(issue_key: str) -> Dict[str, Any]:
    if not settings.jira_live:
        issue = next((i for i in _mock_jira_issues if i["key"] == issue_key), None)
        if not issue:
            raise ConnectorError(f"Mock Jira issue {issue_key} not found")
        return issue
    data = await _jira_get(
        f"/rest/api/3/issue/{quote(issue_key)}",
        {"fields": "summary,status,assignee,labels"},
    )
    fields = data.get("fields", {})
    return {
        "key": data.get("key"),
        "summary": fields.get("summary", ""),
        "status": (fields.get("status") or {}).get("name", "Unknown"),
        "assignee": (fields.get("assignee") or {}).get("displayName", "Unassigned"),
        "labels": fields.get("labels", []),
    }


async def get_jira_transitions(issue_key: str) -> List[Dict[str, Any]]:
    if not settings.jira_live:
        return [
            {"id": "11", "name": "To Do"},
            {"id": "21", "name": "In Progress"},
            {"id": "31", "name": "In Review"},
            {"id": "41", "name": "Done"},
        ]
    data = await _jira_get(f"/rest/api/3/issue/{quote(issue_key)}/transitions")
    return [{"id": str(t["id"]), "name": t["name"]} for t in data.get("transitions", [])]


async def find_issue_by_label(label: str, project_key: Optional[str] = None) -> Optional[str]:
    target = project_key or PROJECT_KEY
    if not settings.jira_live:
        for issue in _mock_jira_issues:
            if label in issue.get("labels", []) and issue["key"].startswith(f"{target}-"):
                return issue["key"]
        return None
    data = await _jira_search(
        f'project = {target} AND labels = "{label}"',
        "key",
        1,
    )
    issues = data.get("issues", [])
    return issues[0]["key"] if issues else None


async def _resolve_assignee(display_name: Optional[str], project_key: Optional[str] = None) -> Optional[str]:
    if not display_name or display_name.upper() == "MISSING" or not settings.jira_live:
        return None
    try:
        users = await _jira_get(
            "/rest/api/3/user/assignable/search",
            {"query": display_name, "project": project_key or PROJECT_KEY, "maxResults": 5},
        )
        if isinstance(users, list) and users:
            return users[0].get("accountId")
    except Exception as exc:
        logger.warning("Could not resolve Jira assignee '%s': %s", display_name, exc)
    return None


async def _comment_issue(issue_key: str, body: str) -> None:
    response = await _jira_post(
        f"/rest/api/3/issue/{quote(issue_key)}/comment",
        {"body": _adf(body)},
    )
    if response.status_code >= 400:
        raise ConnectorError(
            f"Jira comment on {issue_key} failed ({response.status_code}): {response.text[:300]}"
        )


async def _transition_issue(issue_key: str, target_status: str) -> Dict[str, Any]:
    if not target_status:
        return {"status": "skipped", "reason": "no target status"}
    transitions = await get_jira_transitions(issue_key)
    target = next(
        (t for t in transitions if t["name"].lower() == target_status.lower()), None
    )
    if not target and target_status.lower() == "in review":
        target = next((t for t in transitions if "review" in t["name"].lower()), None)
    if not target:
        return {
            "status": "skipped",
            "reason": f"Jira has no transition to '{target_status}'. Available: {[t['name'] for t in transitions]}",
        }
    if not settings.jira_live:
        for issue in _mock_jira_issues:
            if issue["key"] == issue_key:
                issue["status"] = target_status
        return {"status": "transitioned", "to": target_status}
    response = await _jira_post(
        f"/rest/api/3/issue/{quote(issue_key)}/transitions",
        {"transition": {"id": target["id"]}},
    )
    if response.status_code >= 400:
        raise ConnectorError(
            f"Jira transition on {issue_key} failed ({response.status_code}): {response.text[:300]}"
        )
    return {"status": "transitioned", "to": target_status}


@jira_mcp.tool()
async def create_or_update_issue(
    payload: Dict[str, Any], project_key: Optional[str] = None
) -> Dict[str, Any]:
    """Idempotently create a Jira issue, or comment/transition an existing one.

    ``idempotency_label`` is stored on newly created issues. Retrying after an
    ambiguous network failure searches for that label first, preventing a
    duplicate ticket (PRD Feature 8). ``project_key`` selects the per-project
    Jira target; it falls back to the global key when omitted.
    """
    issue_key = payload.get("issue_key")
    summary = payload.get("summary", "Update from AI PM Agent")
    description = payload.get("description", "")
    target_status = payload.get("status")
    assignee = payload.get("assignee")
    label = payload.get("idempotency_label")
    jira_target = project_key or PROJECT_KEY
    _require_jira_live()

    try:
        if issue_key and issue_key != "UNMATCHED":
            actions = []
            if description:
                await _comment_issue(issue_key, description)
                actions.append("comment_added")
            transition = await _transition_issue(issue_key, target_status)
            actions.append(transition)
            return {
                "ok": True,
                "status": "updated",
                "issue_key": issue_key,
                "actions": actions,
                "mode": "live" if settings.jira_live else "mock",
            }

        if label and settings.jira_live:
            existing = await find_issue_by_label(label, jira_target)
            if existing:
                return {
                    "ok": True,
                    "status": "already_exists",
                    "issue_key": existing,
                    "mode": "live",
                }

        if not settings.jira_live:
            if label:
                for issue in _mock_jira_issues:
                    if label in issue.get("labels", []):
                        return {
                            "ok": True,
                            "status": "already_exists",
                            "issue_key": issue["key"],
                            "mode": "mock",
                        }
            number = len(_mock_jira_issues) + 1
            new_key = f"{jira_target}-{number}"
            _mock_jira_issues.append(
                {
                    "key": new_key,
                    "summary": summary,
                    "status": "To Do",
                    "assignee": assignee or "Unassigned",
                    "created": "2026-11-20T10:00:00Z",
                    "labels": [label] if label else [],
                }
            )
            return {"ok": True, "status": "created", "issue_key": new_key, "mode": "mock"}

        fields: Dict[str, Any] = {
            "project": {"key": jira_target},
            "summary": summary[:255],
            "description": _adf(description),
            "issuetype": {"name": settings.JIRA_ISSUE_TYPE},
        }
        if label:
            fields["labels"] = [label]
        account_id = await _resolve_assignee(assignee, jira_target)
        if account_id:
            fields["assignee"] = {"id": account_id}
        response = await _jira_post("/rest/api/3/issue", {"fields": fields})
        if response.status_code not in (200, 201):
            return {
                "ok": False,
                "error": f"Jira create failed ({response.status_code}): {response.text[:400]}",
                "mode": "live",
            }
        data = response.json()
        return {
            "ok": True,
            "status": "created",
            "issue_key": data.get("key"),
            "url": f"{settings.JIRA_BASE_URL}/browse/{data.get('key')}",
            "mode": "live",
        }
    except Exception as exc:
        logger.error("Jira write failed: %s", exc)
        return {"ok": False, "error": str(exc), "mode": "live" if settings.jira_live else "mock"}


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------


def _github_headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ai-pm-agent",
    }


def _require_github_live() -> None:
    if settings.github_live:
        return
    if settings.CONNECTORS_ALLOW_MOCK:
        return
    raise ConnectorNotConfigured("GitHub credentials are not configured")


async def _github_get(path: str, params: Optional[Dict[str, Any]] = None) -> Any:
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(
            f"https://api.github.com{path}", headers=_github_headers(), params=params
        )
    if response.status_code >= 400:
        raise ConnectorError(
            f"GitHub GET {path} failed ({response.status_code}): {response.text[:300]}"
        )
    return response.json()


@github_mcp.tool()
async def get_recent_prs(repo: Optional[str] = None) -> List[Dict[str, Any]]:
    _require_github_live()
    target_repo = repo or settings.GITHUB_REPO
    if not settings.github_live:
        return _mock_github_prs
    try:
        data = await _github_get(
            f"/repos/{target_repo}/pulls",
            {"state": "all", "per_page": settings.GITHUB_PER_PAGE, "sort": "updated", "direction": "desc"},
        )
    except ConnectorError:
        raise
    return [
        {
            "number": item.get("number"),
            "title": item.get("title"),
            "body": item.get("body") or "",
            "state": item.get("state"),
            "merged": bool(item.get("merged_at")),
            "draft": bool(item.get("draft")),
            "user": (item.get("user") or {}).get("login"),
            "head": (item.get("head") or {}).get("ref"),
            "base": (item.get("base") or {}).get("ref"),
            "created_at": item.get("created_at"),
            "merged_at": item.get("merged_at"),
            "html_url": item.get("html_url"),
        }
        for item in data
    ]


@github_mcp.tool()
async def get_commits(repo: Optional[str] = None) -> List[Dict[str, Any]]:
    _require_github_live()
    target_repo = repo or settings.GITHUB_REPO
    if not settings.github_live:
        return _mock_github_commits
    data = await _github_get(
        f"/repos/{target_repo}/commits", {"per_page": settings.GITHUB_PER_PAGE}
    )
    commits = []
    for item in data:
        commit = item.get("commit", {})
        commits.append(
            {
                "sha": (item.get("sha") or "")[:7],
                "message": (commit.get("message") or "").split("\n")[0],
                "author": ((commit.get("author") or {}).get("name")) or "Unknown",
                "date": (commit.get("author") or {}).get("date"),
                "html_url": item.get("html_url"),
            }
        )
    return commits


@github_mcp.tool()
async def get_github_issues(repo: Optional[str] = None) -> List[Dict[str, Any]]:
    _require_github_live()
    target_repo = repo or settings.GITHUB_REPO
    if not settings.github_live:
        return _mock_github_issues
    data = await _github_get(
        f"/repos/{target_repo}/issues",
        {"state": "all", "per_page": settings.GITHUB_PER_PAGE, "sort": "updated"},
    )
    return [
        {
            "number": item.get("number"),
            "title": item.get("title"),
            "body": item.get("body") or "",
            "state": item.get("state"),
            "user": (item.get("user") or {}).get("login"),
            "created_at": item.get("created_at"),
            "html_url": item.get("html_url"),
        }
        for item in data
        if "pull_request" not in item
    ]


# ---------------------------------------------------------------------------
# Microsoft Teams / Microsoft Graph
# ---------------------------------------------------------------------------

_graph_token_cache: Dict[str, Any] = {"token": None, "expires_at": 0.0}


async def _graph_token() -> Optional[str]:
    if settings.TEAMS_ACCESS_TOKEN:
        return settings.TEAMS_ACCESS_TOKEN
    if not (settings.TEAMS_TENANT_ID and settings.TEAMS_CLIENT_ID and settings.TEAMS_CLIENT_SECRET):
        return None
    if _graph_token_cache["token"] and _graph_token_cache["expires_at"] > time.time() + 30:
        return _graph_token_cache["token"]
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(
            f"https://login.microsoftonline.com/{settings.TEAMS_TENANT_ID}/oauth2/v2.0/token",
            data={
                "client_id": settings.TEAMS_CLIENT_ID,
                "client_secret": settings.TEAMS_CLIENT_SECRET,
                "scope": "https://graph.microsoft.com/.default",
                "grant_type": "client_credentials",
            },
        )
        if response.status_code >= 400:
            raise ConnectorError(
                f"Microsoft Graph token request failed ({response.status_code}): {response.text[:300]}"
            )
        payload = response.json()
    _graph_token_cache["token"] = payload["access_token"]
    _graph_token_cache["expires_at"] = time.time() + int(payload.get("expires_in", 3599))
    return _graph_token_cache["token"]


async def _graph_get(path: str) -> Any:
    token = await _graph_token()
    if not token:
        raise ConnectorNotConfigured("Teams credentials are not configured")
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"https://graph.microsoft.com/v1.0{path}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
    if response.status_code >= 400:
        raise ConnectorError(
            f"Microsoft Graph GET {path} failed ({response.status_code}): {response.text[:300]}"
        )
    return response.json()


def parse_vtt(vtt: str) -> List[Dict[str, Any]]:
    """Parse Teams WebVTT transcripts, preserving speaker and time where present."""
    lines: List[Dict[str, Any]] = []
    current_time: Optional[str] = None
    current_speaker: Optional[str] = None
    for raw in vtt.splitlines():
        row = raw.strip()
        if not row or row.upper().startswith("WEBVTT") or row.isdigit():
            continue
        if "-->" in row:
            current_time = row.split("-->")[0].strip()
            continue
        match = re.match(r"<v\s+([^>]+)>(.*?)(?:</v>)?$", row)
        if match:
            current_speaker = match.group(1).strip()
            text = re.sub(r"<[^>]+>", "", match.group(2)).strip()
        else:
            text = re.sub(r"<[^>]+>", "", row).strip()
        if not text:
            continue
        if ":" in text and current_speaker is None and len(text.split(":", 1)[0]) < 40:
            possible_speaker, possible_text = text.split(":", 1)
            if possible_text.strip():
                current_speaker = possible_speaker.strip()
                text = possible_text.strip()
        lines.append(
            {
                "line": len(lines) + 1,
                "speaker": current_speaker or "Speaker",
                "text": text,
                "spoke_at": current_time,
            }
        )
    return lines


@teams_mcp.tool()
async def list_teams_meetings(days_back: int = 7) -> List[Dict[str, Any]]:
    """List Teams online meetings, live through Graph when configured."""
    if not settings.teams_live:
        if not settings.CONNECTORS_ALLOW_MOCK:
            raise ConnectorNotConfigured("Teams credentials are not configured")
        return _mock_teams_meetings
    user = settings.TEAMS_USER_ID
    path = f"/users/{quote(user)}/onlineMeetings" if user else "/me/onlineMeetings"
    try:
        data = await _graph_get(path)
    except ConnectorError as exc:
        logger.error("Teams meeting listing failed: %s", exc)
        raise
    return [
        {
            "id": meeting.get("id"),
            "subject": meeting.get("subject", "Teams meeting"),
            "start_time": meeting.get("startDateTime"),
            "organizer": (
                (meeting.get("participants") or {}).get("organizer") or {}
            ).get("upn", "Unknown"),
            "attendees": [
                attendee.get("upn")
                for attendee in ((meeting.get("participants") or {}).get("attendees") or [])
                if attendee.get("upn")
            ],
            "series_id": meeting.get("seriesId"),
            "has_transcript": True,
        }
        for meeting in data.get("value", [])
    ]


@teams_mcp.tool()
async def get_teams_meeting_transcript(meeting_id: str) -> Dict[str, Any]:
    """Fetch a Teams transcript and normalise it into numbered speaker turns."""
    if not settings.teams_live or meeting_id.startswith("teams-meet-"):
        if not settings.teams_live and not settings.CONNECTORS_ALLOW_MOCK:
            raise ConnectorNotConfigured("Teams credentials are not configured")
        if not meeting_id.startswith("teams-meet-"):
            raise ConnectorError(
                "Teams is not connected. Configure Graph credentials or upload a transcript file."
            )
        lines = _mock_teams_transcripts.get(meeting_id, _mock_teams_transcripts["teams-meet-sprint14"])
        meta = next(
            (m for m in _mock_teams_meetings if m["id"] == meeting_id),
            _mock_teams_meetings[0],
        )
        return {
            "meeting_id": meeting_id,
            "title": meta["subject"],
            "meeting_date": (meta.get("start_time") or "")[:10],
            "attendees": meta["attendees"],
            "series_id": meta.get("series_id"),
            "lines": lines,
            "mode": "mock",
        }

    user = settings.TEAMS_USER_ID
    token = await _graph_token()
    if not token:
        raise ConnectorNotConfigured("Teams credentials are not configured")
    if user:
        base = f"/users/{quote(user)}/onlineMeetings/{quote(meeting_id)}/transcripts"
    else:
        base = f"/me/onlineMeetings/{quote(meeting_id)}/transcripts"
    transcripts = await _graph_get(base)
    entries = transcripts.get("value", [])
    if not entries:
        raise ConnectorError(f"No Teams transcript is available for meeting {meeting_id}")
    transcript_id = entries[0].get("id")
    if not transcript_id:
        raise ConnectorError("Teams transcript metadata did not include an id")
    content_path = f"{base}/{quote(transcript_id)}/content"
    async with httpx.AsyncClient(timeout=40.0) as client:
        response = await client.get(
            f"https://graph.microsoft.com/v1.0{content_path}",
            headers={"Authorization": f"Bearer {token}", "Accept": "text/vtt"},
        )
        if response.status_code >= 400:
            raise ConnectorError(
                f"Teams transcript download failed ({response.status_code}): {response.text[:300]}"
            )
        content = response.text
    try:
        payload = json.loads(content)
        lines = [
            {
                "line": idx,
                "speaker": entry.get("speaker", "Speaker"),
                "text": entry.get("text", ""),
                "spoke_at": entry.get("startDateTime") or entry.get("timestamp"),
            }
            for idx, entry in enumerate(payload.get("value", payload), start=1)
            if entry.get("text")
        ]
    except json.JSONDecodeError:
        lines = parse_vtt(content)
    meta = next(
        (m for m in await list_teams_meetings() if m["id"] == meeting_id), {}
    )
    return {
        "meeting_id": meeting_id,
        "title": meta.get("subject", "Teams meeting"),
        "meeting_date": (meta.get("start_time") or "")[:10],
        "attendees": meta.get("attendees", []),
        "series_id": meta.get("series_id"),
        "lines": lines,
        "mode": "live",
    }


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


async def connector_health() -> Dict[str, Dict[str, Any]]:
    """Non-destructive connector status for the Admin view."""
    result: Dict[str, Dict[str, Any]] = {
        "jira": {
            "live": settings.jira_live,
            "configured": settings.jira_live,
            "mode": "live" if settings.jira_live else "mock",
            "detail": settings.JIRA_BASE_URL if settings.jira_live else "No Jira credentials",
        },
        "github": {
            "live": settings.github_live,
            "configured": settings.github_live,
            "mode": "live" if settings.github_live else "mock",
            "detail": settings.GITHUB_REPO if settings.github_live else "No GitHub credentials",
        },
        "teams": {
            "live": settings.teams_live,
            "configured": settings.teams_live,
            "mode": "live" if settings.teams_live else "upload",
            "detail": "Microsoft Graph" if settings.teams_live else "Transcript upload / Granola / Wispr",
        },
    }
    if settings.jira_live:
        try:
            await _jira_get("/rest/api/3/myself")
            result["jira"]["detail"] = f"Connected to {settings.JIRA_BASE_URL}"
        except Exception as exc:
            result["jira"].update({"live": False, "detail": f"Configured but unreachable: {exc}"})
    if settings.github_live:
        try:
            await _github_get(f"/repos/{settings.GITHUB_REPO}")
            result["github"]["detail"] = f"Connected to {settings.GITHUB_REPO}"
        except Exception as exc:
            result["github"].update({"live": False, "detail": f"Configured but unreachable: {exc}"})
    if settings.teams_live:
        try:
            await _graph_token()
            result["teams"]["detail"] = "Microsoft Graph token acquired"
        except Exception as exc:
            result["teams"].update({"live": False, "detail": f"Configured but unreachable: {exc}"})
    return result
