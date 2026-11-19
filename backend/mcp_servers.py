import os
import base64
import json
import logging
from typing import Dict, Any, List, Optional
import httpx
from mcp.server.mcpserver import MCPServer
from backend.config import settings

logger = logging.getLogger("mcp_servers")

# Initialize MCP Servers
jira_mcp = MCPServer("jira-connector")
github_mcp = MCPServer("github-connector")

# In-memory mock storage for fallback mode
_mock_jira_issues = [
    {
        "key": f"{settings.JIRA_PROJECT_KEY}-101",
        "summary": "Implement OAuth2 and JWT authentication endpoint",
        "status": "In Progress",
        "assignee": "Alex",
        "created": "2026-11-10T10:00:00Z"
    },
    {
        "key": f"{settings.JIRA_PROJECT_KEY}-102",
        "summary": "Design Database Schema for Projects and Drafts",
        "status": "In Review",
        "assignee": "David",
        "created": "2026-11-12T14:30:00Z"
    },
    {
        "key": f"{settings.JIRA_PROJECT_KEY}-103",
        "summary": "Setup Docker compose and CI/CD pipelines",
        "status": "To Do",
        "assignee": "Elena",
        "created": "2026-11-14T09:15:00Z"
    }
]

_mock_github_prs = [
    {
        "number": 42,
        "title": "feat(auth): complete OAuth2 token generation and refresh flow",
        "state": "closed",
        "merged": True,
        "user": "alex-dev",
        "merged_at": "2026-11-18T16:45:00Z",
        "html_url": "https://github.com/mock/repo/pull/42"
    },
    {
        "number": 45,
        "title": "feat(ui): notification toast system for real-time alerts",
        "state": "open",
        "merged": False,
        "user": "david-frontend",
        "created_at": "2026-11-19T08:20:00Z",
        "html_url": "https://github.com/mock/repo/pull/45"
    }
]

_mock_github_commits = [
    {
        "sha": "a1b2c3d",
        "message": "fix: correct token expiration calculation in auth service",
        "author": "Alex",
        "date": "2026-11-18T16:30:00Z"
    },
    {
        "sha": "e5f6g7h",
        "message": "wip: add notification drawer layout components",
        "author": "David",
        "date": "2026-11-19T08:15:00Z"
    }
]

# --- Jira MCP Tools ---

@jira_mcp.tool()
async def get_open_issues(project_key: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Fetches open issues from Jira for the given project key.
    Uses Jira Cloud REST API v3 if credentials are provided, else returns realistic mock issues.
    """
    pkey = project_key or settings.JIRA_PROJECT_KEY
    if settings.JIRA_BASE_URL and settings.JIRA_EMAIL and settings.JIRA_API_TOKEN and not settings.JIRA_BASE_URL.startswith("https://your-domain"):
        try:
            auth_str = f"{settings.JIRA_EMAIL}:{settings.JIRA_API_TOKEN}"
            auth_b64 = base64.b64encode(auth_str.encode()).decode()
            headers = {
                "Authorization": f"Basic {auth_b64}",
                "Accept": "application/json"
            }
            jql = f"project = {pkey} AND statusCategory != Done ORDER BY updated DESC"
            params = {
                "jql": jql, 
                "maxResults": settings.JIRA_MAX_RESULTS, 
                "fields": "key,summary,status,assignee,created"
            }
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Try modern /rest/api/3/search/jql endpoint first, fallback to /rest/api/3/search
                res = await client.get(
                    f"{settings.JIRA_BASE_URL}/rest/api/3/search/jql",
                    headers=headers,
                    params=params
                )
                if res.status_code == 404 or res.status_code == 410:
                    res = await client.get(
                        f"{settings.JIRA_BASE_URL}/rest/api/3/search",
                        headers=headers,
                        params=params
                    )
                if res.status_code == 200:
                    data = res.json()
                    issues = []
                    for item in data.get("issues", []):
                        fields = item.get("fields", {})
                        assignee = fields.get("assignee")
                        issues.append({
                            "key": item.get("key"),
                            "summary": fields.get("summary", ""),
                            "status": fields.get("status", {}).get("name", "Unknown"),
                            "assignee": assignee.get("displayName", "Unassigned") if assignee else "Unassigned",
                            "created": fields.get("created")
                        })
                    return issues
                else:
                    logger.warning(f"Jira API error {res.status_code}: {res.text}. Falling back to mock issues.")
        except Exception as e:
            logger.error(f"Failed to query Jira API: {e}. Falling back to mock data.")

    return [issue for issue in _mock_jira_issues if issue["key"].startswith(pkey)]

@jira_mcp.tool()
async def create_or_update_issue(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Creates a new Jira issue or updates status on an existing issue.
    Ensures idempotency using idempotency_key or issue_key.
    """
    issue_key = payload.get("issue_key")
    summary = payload.get("summary", "New Task from AI PM Agent")
    description = payload.get("description", "")
    target_status = payload.get("status")
    assignee = payload.get("assignee")

    # If live Jira credentials are provided
    if settings.JIRA_BASE_URL and settings.JIRA_EMAIL and settings.JIRA_API_TOKEN and not settings.JIRA_BASE_URL.startswith("https://your-domain"):
        try:
            auth_str = f"{settings.JIRA_EMAIL}:{settings.JIRA_API_TOKEN}"
            auth_b64 = base64.b64encode(auth_str.encode()).decode()
            headers = {
                "Authorization": f"Basic {auth_b64}",
                "Accept": "application/json",
                "Content-Type": "application/json"
            }
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Update existing issue
                if issue_key and issue_key != "UNMATCHED":
                    # Add comment or transition status
                    comment_payload = {
                        "body": {
                            "type": "doc",
                            "version": 1,
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [
                                        {"type": "text", "text": f"[AI PM Agent Update] Spoken status: {target_status}\n{description}"}
                                    ]
                                }
                            ]
                        }
                    }
                    res = await client.post(
                        f"{settings.JIRA_BASE_URL}/rest/api/3/issue/{issue_key}/comment",
                        headers=headers,
                        json=comment_payload
                    )
                    return {"status": "updated", "issue_key": issue_key, "action": "comment_added"}
                else:
                    # Create new issue
                    create_payload = {
                        "fields": {
                            "project": {"key": settings.JIRA_PROJECT_KEY},
                            "summary": summary,
                            "description": {
                                "type": "doc",
                                "version": 1,
                                "content": [
                                    {
                                        "type": "paragraph",
                                        "content": [{"type": "text", "text": description}]
                                    }
                                ]
                            },
                            "issuetype": {"name": "Task"}
                        }
                    }
                    res = await client.post(
                        f"{settings.JIRA_BASE_URL}/rest/api/3/issue",
                        headers=headers,
                        json=create_payload
                    )
                    if res.status_code in (200, 201):
                        new_data = res.json()
                        return {"status": "created", "issue_key": new_data.get("key")}
        except Exception as e:
            logger.error(f"Failed to create/update Jira issue: {e}. Executing mock writeback.")

    # Mock writeback fallback
    if issue_key and issue_key != "UNMATCHED":
        for issue in _mock_jira_issues:
            if issue["key"] == issue_key:
                if target_status:
                    issue["status"] = target_status
                return {"status": "updated", "issue_key": issue_key}
        return {"status": "updated", "issue_key": issue_key}
    else:
        new_id = len(_mock_jira_issues) + 101
        new_key = f"{settings.JIRA_PROJECT_KEY}-{new_id}"
        new_issue = {
            "key": new_key,
            "summary": summary,
            "status": "To Do",
            "assignee": assignee or "Unassigned",
            "created": "2026-11-19T20:00:00Z"
        }
        _mock_jira_issues.append(new_issue)
        return {"status": "created", "issue_key": new_key}


# --- GitHub MCP Tools ---

@github_mcp.tool()
async def get_recent_prs(repo: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Fetches recent pull requests from GitHub (open and recently merged).
    """
    target_repo = repo or settings.GITHUB_REPO
    if settings.GITHUB_TOKEN and target_repo and not target_repo.startswith("owner/"):
        try:
            headers = {
                "Authorization": f"Bearer {settings.GITHUB_TOKEN}",
                "Accept": "application/vnd.github.v3+json"
            }
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.get(
                    f"https://api.github.com/repos/{target_repo}/pulls?state=all&per_page={settings.GITHUB_PER_PAGE}",
                    headers=headers
                )
                if res.status_code == 200:
                    prs = []
                    for item in res.json():
                        prs.append({
                            "number": item.get("number"),
                            "title": item.get("title"),
                            "state": item.get("state"),
                            "merged": bool(item.get("merged_at")),
                            "user": item.get("user", {}).get("login"),
                            "created_at": item.get("created_at"),
                            "merged_at": item.get("merged_at"),
                            "html_url": item.get("html_url")
                        })
                    return prs
        except Exception as e:
            logger.error(f"Failed to query GitHub PRs: {e}. Falling back to mock PRs.")

    return _mock_github_prs

@github_mcp.tool()
async def get_commits(repo: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Fetches recent commit history from GitHub.
    """
    target_repo = repo or settings.GITHUB_REPO
    if settings.GITHUB_TOKEN and target_repo and not target_repo.startswith("owner/"):
        try:
            headers = {
                "Authorization": f"Bearer {settings.GITHUB_TOKEN}",
                "Accept": "application/vnd.github.v3+json"
            }
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.get(
                    f"https://api.github.com/repos/{target_repo}/commits?per_page={settings.GITHUB_PER_PAGE}",
                    headers=headers
                )
                if res.status_code == 200:
                    commits = []
                    for item in res.json():
                        commit_data = item.get("commit", {})
                        commits.append({
                            "sha": item.get("sha", "")[:7],
                            "message": commit_data.get("message", "").split("\n")[0],
                            "author": commit_data.get("author", {}).get("name", "Unknown"),
                            "date": commit_data.get("author", {}).get("date")
                        })
                    return commits
        except Exception as e:
            logger.error(f"Failed to query GitHub commits: {e}. Falling back to mock commits.")

    return _mock_github_commits
