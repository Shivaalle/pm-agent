"""Application services shared by API routes and the LangGraph agent."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

from backend.config import settings
from backend.database import (
    approve_draft_record,
    create_action_item,
    create_notification,
    get_action_item,
    get_action_items,
    get_draft_by_id,
    get_forecast,
    get_notifications,
    get_overdue_action_items,
    get_project,
    get_project_members,
    get_project_pm_usernames,
    recompute_forecast,
    record_sync_attempt,
    record_sync_history,
    set_action_item_jira_key,
    upsert_code_activity,
)
from backend.logger import get_logger
from backend.mcp_servers import create_or_update_issue, get_jira_issue
from backend.models import ApproveResult, DraftRecord

logger = get_logger("services")

STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "into", "about",
    "please", "will", "should", "user", "users", "work", "task", "item",
    "meeting", "update", "need", "needs", "have", "has", "our", "your",
}


class ApprovalError(ValueError):
    pass


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", (text or "").lower())
        if len(token) > 2 and token not in STOPWORDS
    }


def jira_url(issue_key: Optional[str]) -> Optional[str]:
    if not issue_key:
        return None
    return f"{settings.JIRA_BASE_URL}/browse/{issue_key}" if settings.JIRA_BASE_URL else None


def build_jira_payload(draft: DraftRecord) -> Dict[str, Any]:
    """Convert a grounded draft into a Jira create/update request."""
    content = draft.proposed_change
    quote = f"\n\nSource: {draft.source_quote}"
    base = {
        "project_key": draft.project_key,
        "idempotency_label": draft.idempotency_key,
    }

    if draft.type == "status_update":
        jira_key = content.get("jira_key")
        if not jira_key or jira_key == "UNMATCHED":
            raise ApprovalError(
                "This status update is not matched to a Jira issue. Edit the draft and set a Jira key before approving."
            )
        return {
            **base,
            "issue_key": jira_key,
            "status": content.get("proposed_jira_status"),
            "description": (
                f"AI PM Agent update from meeting {draft.meeting_id}.\n"
                f"Spoken status: {content.get('spoken_status')}\n"
                f"GitHub evidence: {draft.github_evidence or 'none'}\n"
                f"Confidence: {draft.confidence:.0%}{quote}"
            ),
        }

    if draft.type == "action_item":
        return {
            **base,
            "summary": f"[Action] {str(content.get('task') or draft.title)[:200]}",
            "description": (
                f"{content.get('task') or draft.title}\n\n"
                f"Owner: {content.get('owner', 'MISSING')}\n"
                f"Due date: {content.get('due_date', 'MISSING')}\n"
                f"Priority: {content.get('priority', 'Medium')}\n"
                f"From meeting: {draft.meeting_id}{quote}"
            ),
            "assignee": (
                content.get("owner")
                if content.get("owner") and content.get("owner") != "MISSING"
                else None
            ),
        }

    if draft.type == "raid_item":
        return {
            **base,
            "summary": f"[{content.get('category', 'Risk')}] {str(content.get('title') or draft.title)[:200]}",
            "description": (
                f"{content.get('description', '')}\n\n"
                f"Severity: {content.get('severity', 'Medium')}\n"
                f"Owner: {content.get('owner', 'MISSING')}\n"
                f"Existing entry update: {content.get('is_update_to_existing', False)}\n"
                f"Reference: {content.get('existing_reference') or 'none'}{quote}"
            ),
        }

    if draft.type == "scope_change":
        return {
            **base,
            "summary": f"[Scope change] {str(content.get('title') or draft.title)[:200]}",
            "description": (
                f"{content.get('description', '')}\n\n"
                f"Requested by: {content.get('requested_by', 'MISSING')}\n"
                f"Impact: {content.get('impact_assessment', '')}\n"
                f"Status: proposed (needs approval before work starts){quote}"
            ),
        }

    return {
        **base,
        "summary": f"[Decision] {str(content.get('decision') or draft.title)[:200]}",
        "description": (
            f"{content.get('decision', draft.title)}\n\n"
            f"Rationale: {content.get('rationale', '')}{quote}"
        ),
    }


async def _notify_affected(draft: DraftRecord, jira_key: Optional[str]) -> None:
    link = f"/drafts/{draft.id}"
    content = draft.proposed_change
    if draft.type == "action_item":
        owner = content.get("owner")
        if owner and owner.upper() != "MISSING":
            from backend.database import resolve_owner_username

            username, display = await resolve_owner_username(owner)
            if username:
                await create_notification(
                    project_key=draft.project_key,
                    ntype="assignment",
                    recipient_username=username,
                    message=(
                        f"You own a new action item: {content.get('task')}. "
                        f"Due {content.get('due_date', 'MISSING')}."
                    ),
                    meeting_id=draft.meeting_id,
                    draft_id=draft.id,
                    link=link,
                    dedupe_key=f"assignment:{draft.id}:{username}",
                )
    if draft.type in {"scope_change", "raid_item"}:
        members = await get_project_members(draft.project_key)
        recipients = {
            member.username
            for member in members
            if member.role in {"Project Manager", "Sponsor"}
        }
        for recipient in recipients:
            await create_notification(
                project_key=draft.project_key,
                ntype="needs_attention",
                recipient_username=recipient,
                message=f"{draft.title} was approved. Jira: {jira_key or 'write pending'}.",
                meeting_id=draft.meeting_id,
                draft_id=draft.id,
                link=link,
                dedupe_key=f"attention:{draft.id}:{recipient}",
            )


async def approve_draft(
    draft_id: int,
    user_name: str,
    user_role: str,
) -> ApproveResult:
    draft = await get_draft_by_id(draft_id)
    if not draft:
        raise ApprovalError("Draft not found")
    if draft.status == "approved":
        return ApproveResult(
            draft_id=draft.id,
            status="already_approved",
            jira_issue_key=draft.jira_issue_key,
            jira_url=jira_url(draft.jira_issue_key),
            sync_status=draft.sync_status,
        )
    if draft.status not in {"waiting", "edited"}:
        raise ApprovalError(f"Draft is {draft.status} and cannot be approved")

    payload = build_jira_payload(draft)  # raises before recording approval

    # The human approval is persisted before any external write. This is the
    # hard gate: Jira is never touched without an audit record proving it.
    await approve_draft_record(
        draft_id=draft.id,
        user_name=user_name,
        user_role=user_role,
        jira_issue_key=None,
        sync_status="pending",
        sync_error=None,
    )

    project = await get_project(draft.project_key)
    jira_target = (project.jira_project_key if project else None) or draft.project_key
    write_result: Dict[str, Any]
    try:
        write_result = await create_or_update_issue(payload, project_key=jira_target)
    except Exception as exc:
        write_result = {"ok": False, "error": str(exc)}

    jira_key = write_result.get("issue_key")
    if not write_result.get("ok"):
        sync_status = "failed"
        sync_error = write_result.get("error") or "Jira write failed"
    else:
        skipped = [
            action
            for action in write_result.get("actions", [])
            if isinstance(action, dict) and action.get("status") == "skipped"
        ]
        sync_status = "partial" if skipped else "synced"
        sync_error = "; ".join(action.get("reason", "") for action in skipped) or None

    await record_sync_attempt(draft.id, sync_status, jira_key, sync_error)

    action_item_id = None
    if draft.type == "action_item":
        content = draft.proposed_change
        action_item_id = await create_action_item(
            project_key=draft.project_key,
            meeting_id=draft.meeting_id,
            draft_id=draft.id,
            description=content.get("task", draft.title),
            owner_display=content.get("owner"),
            due_date=content.get("due_date"),
            priority=content.get("priority", "Medium"),
            source_quote=draft.source_quote,
            jira_issue_key=jira_key,
        )
        if jira_key:
            await set_action_item_jira_key(action_item_id, jira_key)

    await _notify_affected(draft, jira_key)
    await recompute_forecast(draft.project_key)

    return ApproveResult(
        draft_id=draft.id,
        status="approved",
        jira_issue_key=jira_key,
        jira_url=jira_url(jira_key),
        sync_status=sync_status,
        error=sync_error,
        action_item_id=action_item_id,
    )


async def retry_draft_sync(
    draft_id: int, user_name: str, user_role: str
) -> ApproveResult:
    draft = await get_draft_by_id(draft_id)
    if not draft:
        raise ApprovalError("Draft not found")
    if draft.status != "approved":
        raise ApprovalError("Only approved drafts can be retried")
    if draft.sync_status not in {"failed", "partial"}:
        raise ApprovalError("This draft does not need a Jira retry")
    payload = build_jira_payload(draft)
    project = await get_project(draft.project_key)
    jira_target = (project.jira_project_key if project else None) or draft.project_key
    try:
        write_result = await create_or_update_issue(payload, project_key=jira_target)
    except Exception as exc:
        write_result = {"ok": False, "error": str(exc)}
    jira_key = write_result.get("issue_key") or draft.jira_issue_key
    if write_result.get("ok"):
        skipped = [
            action
            for action in write_result.get("actions", [])
            if isinstance(action, dict) and action.get("status") == "skipped"
        ]
        sync_status = "partial" if skipped else "synced"
        error = "; ".join(action.get("reason", "") for action in skipped) or None
    else:
        sync_status = "failed"
        error = write_result.get("error") or "Jira write failed"
    await record_sync_attempt(draft_id, sync_status, jira_key, error)
    await record_sync_history(
        draft_id,
        user_name,
        user_role,
        jira_key,
        f"Jira retry: {sync_status}" + (f" — {error}" if error else ""),
    )
    if draft.type == "action_item" and jira_key:
        items = await get_action_items([draft.project_key])
        matching = next((item for item in items if item.draft_id == draft.id), None)
        if matching:
            await set_action_item_jira_key(matching.id, jira_key)
    return ApproveResult(
        draft_id=draft_id,
        status="retried",
        jira_issue_key=jira_key,
        jira_url=jira_url(jira_key),
        sync_status=sync_status,
        error=error,
    )


async def run_deadline_check(project_keys: Iterable[str]) -> Dict[str, Any]:
    """Alert owner + PM for overdue open items and refresh the forecast."""
    checked = 0
    alerts_created = 0
    overdue_total = 0
    forecasts = []
    today = __import__("datetime").date.today().isoformat()
    for project_key in project_keys:
        overdue = await get_overdue_action_items(project_key)
        overdue_total += len(overdue)
        checked += len(overdue)
        pm_usernames = set(await get_project_pm_usernames(project_key))
        for item in overdue:
            recipients = set(pm_usernames)
            if item.owner_username:
                recipients.add(item.owner_username)
            for recipient in recipients:
                notification_id = await create_notification(
                    project_key=project_key,
                    ntype="deadline_alert",
                    recipient_username=recipient,
                    message=(
                        f"Action item '{item.description}' is overdue (due {item.due_date}). "
                        f"Owner: {item.owner_display or 'unassigned'}."
                    ),
                    meeting_id=item.meeting_id,
                    draft_id=item.draft_id,
                    action_item_id=item.id,
                    link=f"/action-items/{item.id}",
                    dedupe_key=f"deadline:{item.id}:{today}:{recipient}",
                )
                if notification_id:
                    alerts_created += 1
        forecasts.append(await recompute_forecast(project_key))
    return {
        "checked": checked,
        "alerts_created": alerts_created,
        "overdue_items": overdue_total,
        "forecasts": forecasts,
    }


async def link_code_activity(
    project_key: str,
    prs: Optional[List[Dict[str, Any]]] = None,
    commits: Optional[List[Dict[str, Any]]] = None,
    issues: Optional[List[Dict[str, Any]]] = None,
    jira_context: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Store GitHub activity, link it to Jira keys/action items, flag mismatches."""
    from backend.mcp_servers import get_commits, get_github_issues, get_recent_prs

    project = await get_project(project_key)
    repo = (project.github_repo if project else None) or settings.GITHUB_REPO or "mock/repo"
    prs = prs if prs is not None else await get_recent_prs(repo)
    commits = commits if commits is not None else await get_commits(repo)
    issues = issues if issues is not None else await get_github_issues(repo)

    status_by_key = {
        issue.get("key"): issue.get("status", "") for issue in (jira_context or [])
    }
    action_items = await get_action_items([project_key])
    synced = 0
    mismatches = 0

    async def jira_status(key: Optional[str]) -> Optional[str]:
        if not key:
            return None
        if key in status_by_key:
            return status_by_key[key]
        if settings.jira_live:
            try:
                issue = await get_jira_issue(key)
                status_by_key[key] = issue.get("status", "")
                return status_by_key[key]
            except Exception:
                return None
        return None

    def best_action_item(text: str, jira_key: Optional[str]) -> Optional[int]:
        if jira_key:
            for item in action_items:
                if item.jira_issue_key == jira_key:
                    return item.id
        text_tokens = _tokens(text)
        best_id, best_score = None, 0.0
        for item in action_items:
            item_tokens = _tokens(item.description)
            if not item_tokens:
                continue
            score = len(text_tokens & item_tokens) / len(item_tokens)
            if score > best_score:
                best_id, best_score = item.id, score
        return best_id if best_score >= 0.35 else None

    async def store(
        kind: str,
        external_id: str,
        title: str,
        author: Optional[str],
        state: str,
        url: Optional[str],
        activity_at: Optional[str],
        text: str,
        open_work: bool,
    ) -> None:
        nonlocal synced, mismatches
        keys = sorted(set(re.findall(r"\b[A-Z][A-Z0-9]{1,9}-\d+\b", text or "")))
        linked_key = keys[0] if keys else None
        linked_item = best_action_item(text, linked_key)
        mismatch = False
        details = None
        status = await jira_status(linked_key)

        if kind == "pr":
            if open_work and status and status.lower() in {"done", "closed", "resolved"}:
                mismatch = True
                details = f"Pull request is open but Jira {linked_key} is '{status}'."
            elif not open_work and state == "closed" and status and status.lower() in {"to do", "open", "in progress", "in review"}:
                mismatch = True
                details = f"Code is merged but Jira {linked_key} is still '{status}'."
        if linked_item:
            item = next((a for a in action_items if a.id == linked_item), None)
            if item and item.status == "done" and open_work:
                mismatch = True
                details = f"Action item '{item.description}' is marked done but {external_id} is still open."

        if mismatch:
            mismatches += 1
        await upsert_code_activity(
            project_key=project_key,
            repo=repo,
            kind=kind,
            external_id=external_id,
            title=title,
            author=author,
            state=state,
            url=url,
            linked_jira_key=linked_key,
            linked_action_item_id=linked_item,
            mismatch=mismatch,
            mismatch_details=details,
            activity_at=activity_at,
        )
        synced += 1

    for pr in prs:
        text = " ".join(
            str(pr.get(part, "")) for part in ("title", "body", "head")
        )
        await store(
            "pr",
            f"#{pr.get('number')}",
            pr.get("title", ""),
            pr.get("user"),
            "merged" if pr.get("merged") else pr.get("state", "unknown"),
            pr.get("html_url"),
            pr.get("merged_at") or pr.get("created_at"),
            text,
            open_work=pr.get("state") == "open",
        )
    for commit in commits:
        await store(
            "commit",
            commit.get("sha", ""),
            commit.get("message", ""),
            commit.get("author"),
            "committed",
            commit.get("html_url"),
            commit.get("date"),
            commit.get("message", ""),
            open_work=False,
        )
    for issue in issues:
        text = f"{issue.get('title', '')} {issue.get('body', '')}"
        await store(
            "issue",
            f"#{issue.get('number')}",
            issue.get("title", ""),
            issue.get("user"),
            issue.get("state", "unknown"),
            issue.get("html_url"),
            issue.get("created_at"),
            text,
            open_work=issue.get("state") == "open",
        )

    return {
        "synced": synced,
        "mismatches": mismatches,
        "prs": len(prs),
        "commits": len(commits),
        "issues": len(issues),
    }
