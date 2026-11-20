"""FastAPI application for the AI Project Manager Agent."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.agent import pm_agent_app
from backend.auth import UserContext, get_current_user
from backend.config import settings
from backend.database import (
    create_project,
    add_private_series,
    add_project_member,
    approve_draft_record,
    authenticate,
    create_notification,
    create_session,
    create_user,
    delete_session,
    edit_draft,
    get_action_item,
    get_action_items,
    get_code_activity,
    get_draft_by_id,
    get_drafts,
    get_forecast,
    get_forecast_history,
    get_history,
    get_llm_cost_summary,
    get_meeting,
    get_meeting_series,
    get_meetings,
    get_notifications,
    get_private_series,
    get_project,
    get_project_members,
    get_project_pm_usernames,
    get_projects_for_user,
    get_quality_metrics,
    get_sla_summary,
    get_transcript_lines,
    get_user_projects,
    get_user_by_username,
    get_users,
    init_db,
    is_series_private,
    mark_all_notifications_read,
    mark_notification_read,
    purge_meeting,
    recompute_forecast,
    reject_draft,
    remove_private_series,
    remove_project_member,
    save_meeting,
    set_meeting_status,
    update_action_item_status,
    update_project_integrations,
)
from backend.logger import get_logger, setup_logging
from backend.mcp_servers import (
    ConnectorError,
    connector_health,
    get_teams_meeting_transcript,
    list_teams_meetings,
    parse_vtt,
)
from backend.models import (
    ActionItemRecord,
    AlertCheckResult,
    ApproveResult,
    BatchApproveRequest,
    BatchApproveResult,
    CodeActivityRecord,
    CodeActivitySyncResult,
    CostSummary,
    CreateUserRequest,
    DraftRecord,
    EditDraftRequest,
    ForecastRecord,
    HealthResponse,
    HistoryRecord,
    IntegrationStatus,
    IntegrationsResponse,
    LoginRequest,
    LoginResponse,
    MeetingRecord,
    MeetingSummaryRecord,
    NotificationRecord,
    PrivacyMarkRequest,
    CreateProjectRequest,
    ProjectContext,
    ProjectMember,
    QualityMetrics,
    RejectDraftRequest,
    SlaSummary,
    TeamsImportRequest,
    TeamsMeeting,
    TranscriptLine,
    TranscriptUploadPayload,
    UpdateActionItemRequest,
    UpdateProjectRequest,
    UploadResult,
    UserRecord,
)
from backend.services import (
    ApprovalError,
    approve_draft,
    link_code_activity,
    retry_draft_sync,
    run_deadline_check,
)

setup_logging()
logger = get_logger("main_api")

_scheduler_task: Optional[asyncio.Task] = None


async def _deadline_worker() -> None:
    await asyncio.sleep(5)
    while True:
        try:
            project_keys = await get_user_projects(0, is_admin=True)
            result = await run_deadline_check(project_keys)
            if result["alerts_created"]:
                logger.info(
                    "Deadline scan created %d alerts across %d overdue items",
                    result["alerts_created"],
                    result["overdue_items"],
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Deadline worker failed: %s", exc)
        await asyncio.sleep(max(15, settings.ALERT_SCAN_INTERVAL_SECONDS))


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _scheduler_task
    await init_db()
    if settings.SCHEDULER_ENABLED:
        _scheduler_task = asyncio.create_task(_deadline_worker())
    logger.info(
        "API ready | AI=%s (%s) | Jira=%s | GitHub=%s | Teams=%s",
        settings.AI_PROVIDER,
        settings.PRIMARY_MODEL,
        "live" if settings.jira_live else "mock",
        "live" if settings.github_live else "mock",
        "live" if settings.teams_live else "upload",
    )
    yield
    if _scheduler_task:
        _scheduler_task.cancel()
        try:
            await _scheduler_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="AI Project Manager Agent API",
    description=(
        "Grounded meeting-to-Jira workflow with MCP connectors, human approval, "
        "deadline alerts, audit history, cost/SLA tracking and role-scoped views."
    ),
    version=settings.APP_VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS or ["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bearer_token(authorization: Optional[str]) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Sign in required")
    return authorization.split(" ", 1)[1].strip()


async def _accessible_projects(user: UserContext) -> List[str]:
    return await get_user_projects(user.user_id, user.is_admin)


async def _scoped_project(user: UserContext, project_key: Optional[str], action: str = "read") -> str:
    key = project_key or settings.JIRA_PROJECT_KEY
    await user.require_project(key, action)
    return key


async def _scoped_keys(user: UserContext, project_key: Optional[str], action: str = "read") -> List[str]:
    if project_key:
        return [await _scoped_project(user, project_key, action)]
    keys = await _accessible_projects(user)
    if not keys:
        raise HTTPException(status_code=403, detail="You are not a member of any project")
    return keys


def _require_draft_reader(user: UserContext) -> None:
    if user.role not in {"Project Manager", "Admin"}:
        raise HTTPException(
            status_code=403,
            detail="The review queue is available to the Project Manager and Admin.",
        )


def _generate_meeting_id(project_key: str, title: str, meeting_date: str, transcript: str) -> str:
    digest = hashlib.sha1(
        f"{project_key}|{title}|{meeting_date}|{transcript}".encode("utf-8")
    ).hexdigest()[:8].upper()
    return f"MEET-{meeting_date.replace('-', '')}-{digest}"


def _normalise_transcript_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    """Accept Granola/Wispr-style JSON as well as the documented transcript shape."""
    transcript = data.get("transcript") or data.get("lines") or data.get("segments") or []
    normalised = []
    for index, item in enumerate(transcript, start=1):
        if isinstance(item, str):
            normalised.append({"line": index, "speaker": "Speaker", "text": item})
            continue
        text = item.get("text") or item.get("content") or item.get("sentence") or item.get("transcript")
        speaker = (
            item.get("speaker")
            or item.get("speaker_name")
            or item.get("name")
            or item.get("source")
            or "Speaker"
        )
        normalised.append(
            {
                "line": item.get("line") or item.get("index") or index,
                "speaker": str(speaker),
                "text": str(text or ""),
                "spoke_at": item.get("spoke_at") or item.get("start_time") or item.get("timestamp"),
            }
        )
    attendees = data.get("attendees") or data.get("participants") or []
    if attendees and isinstance(attendees[0], dict):
        attendees = [a.get("name") or a.get("displayName") or a.get("email") or "Attendee" for a in attendees]
    return {
        "meeting_id": data.get("meeting_id") or data.get("id"),
        "project_key": data.get("project_key"),
        "title": data.get("title") or data.get("subject") or data.get("name") or "Project meeting",
        "meeting_date": data.get("meeting_date") or data.get("date") or (data.get("start_time") or "")[:10],
        "attendees": [str(a) for a in attendees],
        "transcript": normalised,
        "is_private": bool(data.get("is_private", False)),
        "series_id": data.get("series_id"),
        "source": data.get("source", "upload"),
        "source_url": data.get("source_url"),
    }


async def _run_agent(
    meeting_id: str,
    project_key: str,
    title: str,
    meeting_date: str,
    transcript_lines: List[TranscriptLine],
    started_at: float,
) -> Dict[str, Any]:
    numbered = "\n".join(
        f"Line {line.line}: {line.speaker}: {line.text}"
        for line in sorted(transcript_lines, key=lambda item: item.line)
    )
    state = {
        "meeting_id": meeting_id,
        "project_key": project_key,
        "title": title,
        "meeting_date": meeting_date,
        "numbered_transcript": numbered,
        "sla_started_at": started_at,
        "inserted_draft_ids": [],
    }
    try:
        result = await pm_agent_app.ainvoke(state)
        return result
    except Exception as exc:
        logger.exception("Agent run failed for %s", meeting_id)
        await set_meeting_status(meeting_id, "failed", str(exc))
        raise


async def _ingest(
    payload: TranscriptUploadPayload,
    user: UserContext,
    started_at: float,
) -> UploadResult:
    project_key = await _scoped_project(user, payload.project_key, "upload_meeting")
    if payload.is_private:
        logger.info("Private meeting skipped before persistence by %s", user.username)
        return UploadResult(
            status="skipped",
            message="Meeting marked private. It was not read, processed or stored.",
        )
    if await is_series_private(payload.series_id, project_key):
        return UploadResult(
            status="skipped",
            message="This recurring meeting series is marked private. It was not read, processed or stored.",
        )

    lines = [line for line in payload.transcript if line.text.strip()]
    if not lines:
        raise HTTPException(status_code=400, detail="Transcript has no non-empty lines")
    numbered = "\n".join(f"Line {line.line}: {line.speaker}: {line.text}" for line in lines)
    meeting_id = payload.meeting_id or _generate_meeting_id(
        project_key, payload.title, payload.meeting_date, numbered
    )
    await save_meeting(
        meeting_id=meeting_id,
        project_key=project_key,
        title=payload.title,
        meeting_date=payload.meeting_date,
        attendees=payload.attendees,
        transcript_lines=lines,
        is_private=False,
        series_id=payload.series_id,
        source=payload.source,
        source_url=payload.source_url,
        transcript_available_at=__import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat(),
        status="processing",
    )
    try:
        result = await _run_agent(
            meeting_id, project_key, payload.title, payload.meeting_date, lines, started_at
        )
    except Exception as exc:
        return UploadResult(
            status="failed",
            meeting_id=meeting_id,
            project_key=project_key,
            message=f"Meeting saved but drafting failed: {exc}",
        )

    drafts = await get_drafts(project_keys=[project_key], meeting_id=meeting_id)
    duration = max(0.0, __import__("time").time() - started_at)
    alerts = await run_deadline_check([project_key])
    return UploadResult(
        status="success" if drafts else "failed",
        meeting_id=meeting_id,
        project_key=project_key,
        total_drafts_generated=len(drafts),
        draft_ids=[draft.id for draft in drafts],
        duration_seconds=round(duration, 2),
        within_sla=duration <= settings.SLA_SECONDS,
        llm_provider=result.get("llm_provider") if result else None,
        llm_model=result.get("llm_model") if result else None,
        message=(
            None
            if drafts
            else "The AI returned no drafts with a verified transcript source. Nothing was saved."
        ),
        alerts_created=alerts["alerts_created"],
    )


# ---------------------------------------------------------------------------
# Public / auth
# ---------------------------------------------------------------------------


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        version=settings.APP_VERSION,
        database="sqlite",
        scheduler="running" if settings.SCHEDULER_ENABLED else "disabled",
    )


@app.get("/capabilities")
async def capabilities(user: UserContext = Depends(get_current_user)) -> Dict[str, Any]:
    """Safe, secret-free mode information for every signed-in role."""
    from backend.llm.client import get_llm_client

    client = get_llm_client()
    ai_live = client.provider.available and client.provider.name != "mock"
    return {
        "ai": {
            "provider": client.provider.name,
            "model": client.provider.model,
            "mode": "live" if ai_live else ("mock" if client.provider.name == "mock" else "unconfigured"),
        },
        "jira": {
            "mode": "live" if settings.jira_live else "mock",
            "project": settings.JIRA_PROJECT_KEY,
        },
        "github": {
            "mode": "live" if settings.github_live else "mock",
            "repo": settings.GITHUB_REPO or None,
        },
        "teams": {
            "mode": "live" if settings.teams_live else "upload",
        },
        "sla_seconds": settings.SLA_SECONDS,
    }


@app.post("/auth/login", response_model=LoginResponse)
async def login(request: LoginRequest) -> LoginResponse:
    user = await authenticate(request.username, request.password)
    if not user:
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    token, expires_at = await create_session(user.id)
    projects = await get_user_projects(user.id, user.role == "Admin")
    return LoginResponse(
        token=token,
        expires_at=expires_at,
        user=user,
        projects=projects,
    )


@app.post("/auth/logout")
async def logout(authorization: Optional[str] = Header(None)) -> Dict[str, str]:
    token = _bearer_token(authorization)
    await delete_session(token)
    return {"status": "signed_out"}


@app.get("/auth/me")
async def me(user: UserContext = Depends(get_current_user)) -> Dict[str, Any]:
    projects = await get_projects_for_user(user.user_id, user.is_admin)
    return {
        "user": {
            "id": user.user_id,
            "username": user.username,
            "display_name": user.display_name,
            "role": user.role,
        },
        "projects": projects,
    }


@app.get("/auth/demo-accounts")
async def demo_accounts() -> Dict[str, Any]:
    """Convenience for local demos. Disabled outside development."""
    if settings.APP_ENV not in {"development", "test", "local"}:
        raise HTTPException(status_code=404, detail="Not found")
    users = await get_users(settings.JIRA_PROJECT_KEY)
    return {
        "password": settings.DEMO_PASSWORD,
        "accounts": [
            {"username": user.username, "display_name": user.display_name, "role": user.role}
            for user in users
        ],
    }


# ---------------------------------------------------------------------------
# Users and projects
# ---------------------------------------------------------------------------


@app.get("/users", response_model=List[UserRecord])
async def list_users(
    project_key: Optional[str] = Query(None),
    user: UserContext = Depends(get_current_user),
) -> List[UserRecord]:
    if user.role not in {"Project Manager", "Admin"}:
        raise HTTPException(status_code=403, detail="Only PMs and Admins can list users")
    if project_key:
        await user.require_project(project_key)
    return await get_users(project_key)


@app.post("/users", response_model=UserRecord, status_code=201)
async def create_user_endpoint(
    request: CreateUserRequest,
    user: UserContext = Depends(get_current_user),
) -> UserRecord:
    user.require("admin")
    if await get_user_by_username(request.username):
        raise HTTPException(status_code=409, detail="Username already exists")
    project_key = request.project_key or settings.JIRA_PROJECT_KEY
    user_id = await create_user(
        username=request.username,
        display_name=request.display_name,
        email=request.email,
        role=request.role,
        password=request.password,
        project_key=project_key,
    )
    created = await get_user_by_username(request.username)
    assert created is not None
    return UserRecord(
        id=user_id,
        username=created["username"],
        display_name=created["display_name"],
        email=created.get("email"),
        role=created["role"],
        created_at=created["created_at"],
    )


@app.get("/projects", response_model=List[ProjectContext])
async def list_projects(user: UserContext = Depends(get_current_user)) -> List[ProjectContext]:
    return await get_projects_for_user(user.user_id, user.is_admin)


@app.post("/projects", response_model=ProjectContext, status_code=201)
async def create_project_endpoint(
    request: CreateProjectRequest,
    user: UserContext = Depends(get_current_user),
) -> ProjectContext:
    user.require("admin")
    if await get_project(request.project_key):
        raise HTTPException(status_code=409, detail="Project already exists")
    try:
        return await create_project(
            project_key=request.project_key,
            name=request.name,
            agreed_scope=request.agreed_scope,
            team_members=request.team_members,
            jira_project_key=request.jira_project_key,
            github_repo=request.github_repo,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.get("/projects/{project_key}", response_model=ProjectContext)
async def project_details(
    project_key: str, user: UserContext = Depends(get_current_user)
) -> ProjectContext:
    await user.require_project(project_key)
    project = await get_project(project_key)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@app.patch("/projects/{project_key}", response_model=ProjectContext)
async def update_project(
    project_key: str,
    request: UpdateProjectRequest,
    user: UserContext = Depends(get_current_user),
) -> ProjectContext:
    """Set this project's Jira project key and GitHub repo (PM or Admin)."""
    user.require("manage_project")
    await user.require_project(project_key)
    project = await get_project(project_key)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    jira_key = (
        request.jira_project_key
        if request.jira_project_key is not None
        else project.jira_project_key
    )
    repo = request.github_repo if request.github_repo is not None else project.github_repo
    await update_project_integrations(project_key, jira_key, repo)
    updated = await get_project(project_key)
    assert updated is not None
    logger.info(
        "Project %s integrations updated by %s: Jira=%s GitHub=%s",
        project_key,
        user.username,
        updated.jira_project_key,
        updated.github_repo,
    )
    return updated


@app.get("/projects/{project_key}/members", response_model=List[ProjectMember])
async def project_members(
    project_key: str, user: UserContext = Depends(get_current_user)
) -> List[ProjectMember]:
    await user.require_project(project_key)
    return await get_project_members(project_key)


class AddMemberRequest(BaseModel):
    user_id: int


@app.post("/projects/{project_key}/members", status_code=201)
async def add_member(
    project_key: str,
    request: AddMemberRequest,
    user: UserContext = Depends(get_current_user),
) -> Dict[str, str]:
    user.require("admin")
    await add_project_member(project_key, request.user_id)
    return {"status": "added"}


@app.delete("/projects/{project_key}/members/{user_id}")
async def delete_member(
    project_key: str, user_id: int, user: UserContext = Depends(get_current_user)
) -> Dict[str, str]:
    user.require("admin")
    if user_id == user.user_id:
        raise HTTPException(status_code=400, detail="You cannot remove yourself")
    removed = await remove_project_member(project_key, user_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Member not found")
    return {"status": "removed"}


# ---------------------------------------------------------------------------
# Meeting ingestion
# ---------------------------------------------------------------------------


@app.post("/meetings/upload", response_model=UploadResult)
async def upload_transcript(
    payload: TranscriptUploadPayload,
    user: UserContext = Depends(get_current_user),
) -> UploadResult:
    import time

    return await _ingest(payload, user, time.time())


@app.get("/sample-transcript")
async def sample_transcript(user: UserContext = Depends(get_current_user)) -> Dict[str, Any]:
    user.require("upload_meeting")
    sample = settings.DB_PATH.parent / "mock_transcript.json"
    if not sample.exists():
        raise HTTPException(status_code=404, detail="Sample transcript is not installed")
    return json.loads(sample.read_text(encoding="utf-8"))


@app.post("/upload_transcript", response_model=UploadResult)
async def upload_transcript_alias(
    payload: TranscriptUploadPayload,
    user: UserContext = Depends(get_current_user),
) -> UploadResult:
    import time

    return await _ingest(payload, user, time.time())


@app.post("/meetings/upload-file", response_model=UploadResult)
async def upload_file(
    file: UploadFile = File(...),
    project_key: Optional[str] = Query(None),
    is_private: bool = Query(False),
    series_id: Optional[str] = Query(None),
    title: Optional[str] = Query(None),
    meeting_date: Optional[str] = Query(None),
    attendees: Optional[str] = Query(None),
    user: UserContext = Depends(get_current_user),
) -> UploadResult:
    import time

    raw = await file.read()
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON transcript: {exc}")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Transcript JSON must be an object")
    normalised = _normalise_transcript_payload(data)
    normalised["project_key"] = project_key or normalised.get("project_key")
    normalised["is_private"] = bool(is_private or normalised.get("is_private"))
    normalised["series_id"] = series_id or normalised.get("series_id")
    if title:
        normalised["title"] = title
    if meeting_date:
        normalised["meeting_date"] = meeting_date
    if attendees:
        normalised["attendees"] = [a.strip() for a in attendees.split(",") if a.strip()]
    try:
        payload = TranscriptUploadPayload(**normalised)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Transcript shape invalid: {exc}")
    return await _ingest(payload, user, time.time())


@app.post("/upload_file", response_model=UploadResult)
async def upload_file_alias(
    file: UploadFile = File(...),
    project_key: Optional[str] = Query(None),
    is_private: bool = Query(False),
    series_id: Optional[str] = Query(None),
    title: Optional[str] = Query(None),
    meeting_date: Optional[str] = Query(None),
    attendees: Optional[str] = Query(None),
    user: UserContext = Depends(get_current_user),
) -> UploadResult:
    return await upload_file(
        file=file,
        project_key=project_key,
        is_private=is_private,
        series_id=series_id,
        title=title,
        meeting_date=meeting_date,
        attendees=attendees,
        user=user,
    )


@app.post("/meetings/upload-vtt", response_model=UploadResult)
async def upload_vtt(
    file: UploadFile = File(...),
    title: str = Query(...),
    meeting_date: str = Query(...),
    attendees: str = Query(""),
    project_key: Optional[str] = Query(None),
    is_private: bool = Query(False),
    series_id: Optional[str] = Query(None),
    user: UserContext = Depends(get_current_user),
) -> UploadResult:
    import time

    content = (await file.read()).decode("utf-8", errors="replace")
    lines = parse_vtt(content)
    if not lines:
        raise HTTPException(status_code=400, detail="No transcript cues found in the VTT file")
    payload = TranscriptUploadPayload(
        project_key=project_key,
        title=title,
        meeting_date=meeting_date,
        attendees=[a.strip() for a in attendees.split(",") if a.strip()],
        transcript=[TranscriptLine(**line) for line in lines],
        is_private=is_private,
        series_id=series_id,
        source="vtt_upload",
    )
    return await _ingest(payload, user, time.time())


class JsonUploadRequest(BaseModel):
    data: Dict[str, Any]


@app.post("/meetings/import-json", response_model=UploadResult)
async def import_json(
    request: JsonUploadRequest,
    project_key: Optional[str] = Query(None),
    is_private: bool = Query(False),
    series_id: Optional[str] = Query(None),
    title: Optional[str] = Query(None),
    meeting_date: Optional[str] = Query(None),
    attendees: Optional[str] = Query(None),
    user: UserContext = Depends(get_current_user),
) -> UploadResult:
    import time

    normalised = _normalise_transcript_payload(request.data)
    normalised["project_key"] = project_key or normalised.get("project_key")
    normalised["is_private"] = bool(is_private or normalised.get("is_private"))
    normalised["series_id"] = series_id or normalised.get("series_id")
    if title:
        normalised["title"] = title
    if meeting_date:
        normalised["meeting_date"] = meeting_date
    if attendees:
        normalised["attendees"] = [a.strip() for a in attendees.split(",") if a.strip()]
    try:
        payload = TranscriptUploadPayload(**normalised)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Transcript shape invalid: {exc}")
    return await _ingest(payload, user, time.time())


@app.get("/meetings", response_model=List[MeetingSummaryRecord])
async def list_meetings(
    project_key: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    user: UserContext = Depends(get_current_user),
) -> List[MeetingSummaryRecord]:
    keys = await _scoped_keys(user, project_key)
    return await get_meetings(keys, limit=limit)


@app.get("/meetings/{meeting_id}", response_model=MeetingRecord)
async def meeting_detail(
    meeting_id: str, user: UserContext = Depends(get_current_user)
) -> MeetingRecord:
    meeting = await get_meeting(meeting_id, include_transcript=True)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    await user.require_project(meeting.project_key)
    return meeting


@app.post("/meetings/{meeting_id}/reprocess", response_model=UploadResult)
async def reprocess_meeting(
    meeting_id: str, user: UserContext = Depends(get_current_user)
) -> UploadResult:
    import time

    meeting = await get_meeting(meeting_id, include_transcript=True)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    await user.require_project(meeting.project_key, "upload_meeting")
    if await is_series_private(meeting.series_id, meeting.project_key):
        raise HTTPException(status_code=409, detail="This recurring meeting series is private")
    started = time.time()
    await set_meeting_status(meeting_id, "processing")
    try:
        result = await _run_agent(
            meeting_id,
            meeting.project_key,
            meeting.title,
            meeting.meeting_date,
            meeting.transcript_lines,
            started,
        )
    except Exception as exc:
        return UploadResult(
            status="failed",
            meeting_id=meeting_id,
            project_key=meeting.project_key,
            message=str(exc),
        )
    drafts = await get_drafts(project_keys=[meeting.project_key], meeting_id=meeting_id)
    duration = time.time() - started
    alerts = await run_deadline_check([meeting.project_key])
    return UploadResult(
        status="success" if drafts else "failed",
        meeting_id=meeting_id,
        project_key=meeting.project_key,
        total_drafts_generated=len(drafts),
        draft_ids=[draft.id for draft in drafts],
        duration_seconds=round(duration, 2),
        within_sla=duration <= settings.SLA_SECONDS,
        llm_provider=result.get("llm_provider"),
        llm_model=result.get("llm_model"),
        alerts_created=alerts["alerts_created"],
    )


# ---------------------------------------------------------------------------
# Teams import
# ---------------------------------------------------------------------------


@app.get("/teams/meetings", response_model=List[TeamsMeeting])
async def teams_meetings(
    project_key: Optional[str] = Query(None),
    user: UserContext = Depends(get_current_user),
) -> List[TeamsMeeting]:
    key = await _scoped_project(user, project_key)
    try:
        meetings = await list_teams_meetings()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Teams connector failed: {exc}")
    result: List[TeamsMeeting] = []
    for meeting in meetings:
        existing = await get_meeting(meeting["id"])
        result.append(
            TeamsMeeting(
                **meeting,
                already_imported=existing is not None,
                is_private_series=await is_series_private(meeting.get("series_id"), key),
                mode="live" if settings.teams_live else "mock",
            )
        )
    return result


@app.post("/teams/import", response_model=UploadResult)
async def teams_import(
    request: TeamsImportRequest,
    user: UserContext = Depends(get_current_user),
) -> UploadResult:
    import time

    key = await _scoped_project(user, request.project_key, "upload_meeting")
    if request.is_private:
        return UploadResult(
            status="skipped",
            message="Meeting marked private. It was not read, processed or stored.",
        )
    try:
        data = await get_teams_meeting_transcript(request.meeting_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Teams transcript failed: {exc}")
    if await is_series_private(data.get("series_id"), key):
        return UploadResult(
            status="skipped",
            message="This recurring meeting series is private. It was not read, processed or stored.",
        )
    payload = TranscriptUploadPayload(
        meeting_id=data.get("meeting_id"),
        project_key=key,
        title=data.get("title") or "Teams meeting",
        meeting_date=data.get("meeting_date") or __import__("datetime").date.today().isoformat(),
        attendees=data.get("attendees") or [],
        transcript=[TranscriptLine(**line) for line in data.get("lines", [])],
        series_id=data.get("series_id"),
        source="teams",
        source_url=data.get("source_url"),
    )
    return await _ingest(payload, user, time.time())


# ---------------------------------------------------------------------------
# Privacy
# ---------------------------------------------------------------------------


@app.post("/meetings/{meeting_id}/private")
async def mark_meeting_private(
    meeting_id: str,
    request: PrivacyMarkRequest,
    user: UserContext = Depends(get_current_user),
) -> Dict[str, Any]:
    meeting = await get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    await user.require_project(meeting.project_key, "manage_private")
    if meeting.series_id:
        await add_private_series(
            meeting.series_id, meeting.project_key, request.reason, user.username
        )
    await purge_meeting(meeting_id)
    await recompute_forecast(meeting.project_key)
    logger.info("Meeting %s marked private and purged by %s", meeting_id, user.username)
    return {
        "status": "private",
        "meeting_id": meeting_id,
        "series_id": meeting.series_id,
        "purged": True,
    }


class SeriesPrivacyRequest(PrivacyMarkRequest):
    project_key: Optional[str] = None


@app.post("/series/{series_id}/private")
async def mark_series_private(
    series_id: str,
    request: SeriesPrivacyRequest,
    user: UserContext = Depends(get_current_user),
) -> Dict[str, str]:
    key = await _scoped_project(user, request.project_key, "manage_private")
    await add_private_series(series_id, key, request.reason, user.username)
    return {"status": "private", "series_id": series_id, "project_key": key}


@app.delete("/series/{series_id}/private")
async def unmark_series_private(
    series_id: str,
    project_key: Optional[str] = Query(None),
    user: UserContext = Depends(get_current_user),
) -> Dict[str, str]:
    key = await _scoped_project(user, project_key, "manage_private")
    removed = await remove_private_series(series_id, key)
    if not removed:
        raise HTTPException(status_code=404, detail="Private series not found")
    return {"status": "visible", "series_id": series_id}


@app.get("/privacy/series")
async def private_series(
    project_key: Optional[str] = Query(None),
    user: UserContext = Depends(get_current_user),
) -> List[Dict[str, Any]]:
    user.require("manage_private")
    keys = await _scoped_keys(user, project_key)
    return await get_private_series(keys)


# ---------------------------------------------------------------------------
# Draft review queue
# ---------------------------------------------------------------------------


@app.get("/drafts", response_model=List[DraftRecord])
async def list_drafts(
    project_key: Optional[str] = Query(None),
    meeting_id: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    draft_type: Optional[str] = Query(None),
    user: UserContext = Depends(get_current_user),
) -> List[DraftRecord]:
    _require_draft_reader(user)
    keys = await _scoped_keys(user, project_key)
    return await get_drafts(
        project_keys=keys, meeting_id=meeting_id, status=status_filter, draft_type=draft_type
    )


@app.get("/drafts/{draft_id}", response_model=DraftRecord)
async def draft_detail(
    draft_id: int, user: UserContext = Depends(get_current_user)
) -> DraftRecord:
    _require_draft_reader(user)
    draft = await get_draft_by_id(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    await user.require_project(draft.project_key)
    return draft


@app.post("/drafts/{draft_id}/approve", response_model=ApproveResult)
async def approve_draft_endpoint(
    draft_id: int, user: UserContext = Depends(get_current_user)
) -> ApproveResult:
    draft = await get_draft_by_id(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    await user.require_project(draft.project_key, "approve")
    try:
        return await approve_draft(draft.id, user.display_name, user.role)
    except ApprovalError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/drafts/batch-approve", response_model=BatchApproveResult)
async def batch_approve(
    request: BatchApproveRequest, user: UserContext = Depends(get_current_user)
) -> BatchApproveResult:
    results: List[ApproveResult] = []
    approved = 0
    failed = 0
    for draft_id in request.draft_ids:
        try:
            draft = await get_draft_by_id(draft_id)
            if not draft:
                raise ApprovalError("Draft not found")
            await user.require_project(draft.project_key, "approve")
            result = await approve_draft(draft.id, user.display_name, user.role)
            results.append(result)
            approved += 1 if result.sync_status in {"synced", "partial"} else 0
        except HTTPException as exc:
            failed += 1
            results.append(ApproveResult(draft_id=draft_id, status="failed", error=exc.detail))
        except Exception as exc:
            failed += 1
            results.append(ApproveResult(draft_id=draft_id, status="failed", error=str(exc)))
    return BatchApproveResult(approved=approved, failed=failed, results=results)


@app.post("/drafts/{draft_id}/edit", response_model=DraftRecord)
async def edit_draft_endpoint(
    draft_id: int,
    request: EditDraftRequest,
    user: UserContext = Depends(get_current_user),
) -> DraftRecord:
    draft = await get_draft_by_id(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    await user.require_project(draft.project_key, "edit")
    success = await edit_draft(
        draft_id,
        request.proposed_change,
        user.display_name,
        user.role,
        request.note,
    )
    if not success:
        raise HTTPException(status_code=409, detail="Draft cannot be edited in its current state")
    updated = await get_draft_by_id(draft_id)
    assert updated is not None
    return updated


@app.post("/drafts/{draft_id}/reject")
async def reject_draft_endpoint(
    draft_id: int,
    request: RejectDraftRequest,
    user: UserContext = Depends(get_current_user),
) -> Dict[str, Any]:
    draft = await get_draft_by_id(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    await user.require_project(draft.project_key, "reject")
    if not await reject_draft(draft_id, user.display_name, user.role, request.reason):
        raise HTTPException(status_code=409, detail="Draft is approved and cannot be rejected")
    return {"status": "rejected", "draft_id": draft_id}


@app.post("/drafts/{draft_id}/retry", response_model=ApproveResult)
async def retry_writeback(
    draft_id: int, user: UserContext = Depends(get_current_user)
) -> ApproveResult:
    draft = await get_draft_by_id(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    await user.require_project(draft.project_key, "approve")
    try:
        return await retry_draft_sync(draft.id, user.display_name, user.role)
    except ApprovalError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/history", response_model=List[HistoryRecord])
async def audit_history(
    project_key: Optional[str] = Query(None),
    user: UserContext = Depends(get_current_user),
) -> List[HistoryRecord]:
    _require_draft_reader(user)
    keys = await _scoped_keys(user, project_key)
    return await get_history(keys)


# ---------------------------------------------------------------------------
# Action items, notifications, forecast
# ---------------------------------------------------------------------------


@app.get("/action-items", response_model=List[ActionItemRecord])
async def action_items(
    project_key: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    owner: Optional[str] = Query(None),
    user: UserContext = Depends(get_current_user),
) -> List[ActionItemRecord]:
    keys = await _scoped_keys(user, project_key)
    if user.role == "Team Member":
        owner = user.username
    elif owner == "me":
        owner = user.username
    return await get_action_items(keys, owner_username=owner, status=status_filter)


@app.get("/action-items/{action_item_id}", response_model=ActionItemRecord)
async def action_item_detail(
    action_item_id: int, user: UserContext = Depends(get_current_user)
) -> ActionItemRecord:
    item = await get_action_item(action_item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Action item not found")
    await user.require_project(item.project_key)
    if user.role == "Team Member" and item.owner_username != user.username:
        raise HTTPException(status_code=403, detail="You can only view your own action items")
    return item


@app.patch("/action-items/{action_item_id}", response_model=ActionItemRecord)
async def update_action_item(
    action_item_id: int,
    request: UpdateActionItemRequest,
    user: UserContext = Depends(get_current_user),
) -> ActionItemRecord:
    item = await get_action_item(action_item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Action item not found")
    await user.require_project(item.project_key)
    if user.role == "Team Member" and item.owner_username != user.username:
        raise HTTPException(status_code=403, detail="You can only update your own action items")
    if user.role not in {"Project Manager", "Admin", "Team Member"}:
        raise HTTPException(status_code=403, detail="This role cannot update action items")
    await update_action_item_status(action_item_id, request.status)
    updated = await get_action_item(action_item_id)
    assert updated is not None
    forecast = await recompute_forecast(item.project_key)
    if request.status == "done" and item.owner_username:
        for pm in await get_project_pm_usernames(item.project_key):
            await create_notification(
                project_key=item.project_key,
                ntype="item_completed",
                recipient_username=pm,
                message=f"{user.display_name} marked '{item.description}' done.",
                meeting_id=item.meeting_id,
                action_item_id=item.id,
                link=f"/action-items/{item.id}",
                dedupe_key=f"completed:{item.id}",
            )
    logger.info(
        "Action item %s -> %s by %s (forecast %s)",
        action_item_id,
        request.status,
        user.username,
        forecast.health,
    )
    return updated


@app.get("/notifications", response_model=List[NotificationRecord])
async def notifications(
    unread_only: bool = Query(False),
    user: UserContext = Depends(get_current_user),
) -> List[NotificationRecord]:
    return await get_notifications(user.username, unread_only=unread_only)


@app.post("/notifications/read-all")
async def read_all_notifications(user: UserContext = Depends(get_current_user)) -> Dict[str, int]:
    return {"updated": await mark_all_notifications_read(user.username)}


@app.post("/notifications/{notification_id}/read")
async def read_notification(
    notification_id: int, user: UserContext = Depends(get_current_user)
) -> Dict[str, str]:
    if not await mark_notification_read(notification_id, user.username):
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"status": "read"}


@app.get("/forecast", response_model=ForecastRecord)
async def forecast(
    project_key: Optional[str] = Query(None),
    date_value: Optional[str] = Query(None, alias="date"),
    user: UserContext = Depends(get_current_user),
) -> ForecastRecord:
    key = await _scoped_project(user, project_key)
    return await get_forecast(key, date_value)


@app.get("/forecast/history", response_model=List[ForecastRecord])
async def forecast_history(
    project_key: Optional[str] = Query(None),
    user: UserContext = Depends(get_current_user),
) -> List[ForecastRecord]:
    key = await _scoped_project(user, project_key)
    return await get_forecast_history(key)


@app.post("/alerts/check")
async def check_alerts(
    project_key: Optional[str] = Query(None),
    user: UserContext = Depends(get_current_user),
) -> Dict[str, Any]:
    keys = await _scoped_keys(user, project_key)
    if user.role not in {"Project Manager", "Admin"}:
        raise HTTPException(status_code=403, detail="Only PMs and Admins can run alert checks")
    result = await run_deadline_check(keys)
    forecasts = result.pop("forecasts", [])
    result["forecast"] = forecasts[0] if forecasts else None
    return result


# ---------------------------------------------------------------------------
# Metrics and overview
# ---------------------------------------------------------------------------


@app.get("/metrics/cost", response_model=CostSummary)
async def cost_metrics(
    project_key: Optional[str] = Query(None),
    user: UserContext = Depends(get_current_user),
) -> CostSummary:
    user.require("view_metrics")
    keys = await _scoped_keys(user, project_key)
    summary = await get_llm_cost_summary(keys)
    summary.project_key = project_key
    return summary


@app.get("/metrics/sla", response_model=SlaSummary)
async def sla_metrics(
    project_key: Optional[str] = Query(None),
    user: UserContext = Depends(get_current_user),
) -> SlaSummary:
    user.require("view_metrics")
    keys = await _scoped_keys(user, project_key)
    summary = await get_sla_summary(keys)
    summary.project_key = project_key
    return summary


@app.get("/metrics/quality", response_model=QualityMetrics)
async def quality_metrics(
    project_key: Optional[str] = Query(None),
    user: UserContext = Depends(get_current_user),
) -> QualityMetrics:
    user.require("view_metrics")
    keys = await _scoped_keys(user, project_key)
    metrics = await get_quality_metrics(keys)
    metrics.project_key = project_key
    return metrics


@app.get("/metrics")
async def all_metrics(
    project_key: Optional[str] = Query(None),
    user: UserContext = Depends(get_current_user),
) -> Dict[str, Any]:
    user.require("view_metrics")
    keys = await _scoped_keys(user, project_key)
    return {
        "cost": await get_llm_cost_summary(keys),
        "sla": await get_sla_summary(keys),
        "quality": await get_quality_metrics(keys),
    }


@app.get("/overview")
async def project_overview(
    project_key: Optional[str] = Query(None),
    user: UserContext = Depends(get_current_user),
) -> Dict[str, Any]:
    key = await _scoped_project(user, project_key)
    project = await get_project(key)
    drafts = await get_drafts(project_keys=[key])
    approved_types = {"decision", "raid_item", "scope_change"}
    visible = [draft for draft in drafts if draft.status in {"approved", "edited"} and draft.type in approved_types] if user.role != "Project Manager" else drafts
    if user.role == "Team Member":
        visible = []
    action_items = await get_action_items([key])
    if user.role == "Team Member":
        action_items = [item for item in action_items if item.owner_username == user.username]
    meetings = await get_meetings([key], limit=10)
    can_view_metrics = user.role in {"Project Manager", "Sponsor", "Admin"}
    return {
        "project": project.model_dump() if project else None,
        "forecast": (await get_forecast(key)).model_dump(),
        "forecast_history": [item.model_dump() for item in await get_forecast_history(key, 14)],
        "quality": (await get_quality_metrics([key])).model_dump() if can_view_metrics else QualityMetrics().model_dump(),
        "drafts": [draft.model_dump() for draft in visible],
        "action_items": [item.model_dump() for item in action_items],
        "meetings": [meeting.model_dump() for meeting in meetings],
        "code_activity": [item.model_dump() for item in await get_code_activity([key], 25)] if can_view_metrics else [],
        "role": user.role,
    }


# ---------------------------------------------------------------------------
# Code activity
# ---------------------------------------------------------------------------


@app.get("/code-activity", response_model=List[CodeActivityRecord])
async def code_activity(
    project_key: Optional[str] = Query(None),
    user: UserContext = Depends(get_current_user),
) -> List[CodeActivityRecord]:
    keys = await _scoped_keys(user, project_key)
    return await get_code_activity(keys)


@app.post("/code-activity/sync", response_model=CodeActivitySyncResult)
async def sync_code_activity(
    project_key: Optional[str] = Query(None),
    user: UserContext = Depends(get_current_user),
) -> CodeActivitySyncResult:
    key = await _scoped_project(user, project_key, "manage_project")
    try:
        result = await link_code_activity(project_key=key)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"GitHub sync failed: {exc}")
    return CodeActivitySyncResult(**result)


# ---------------------------------------------------------------------------
# Integrations (Admin)
# ---------------------------------------------------------------------------


@app.get("/integrations", response_model=IntegrationsResponse)
async def integrations(
    project_key: Optional[str] = Query(None),
    user: UserContext = Depends(get_current_user),
) -> IntegrationsResponse:
    user.require("integrations")
    if project_key:
        await user.require_project(project_key)
    health = await connector_health()
    from backend.llm.client import get_llm_client

    client = get_llm_client()
    ai_live = client.provider.available and client.provider.name != "mock"
    return IntegrationsResponse(
        ai=IntegrationStatus(
            name="AI provider",
            key=client.provider.name,
            live=ai_live,
            mode="live" if ai_live else ("mock" if client.provider.name == "mock" else "unconfigured"),
            detail=f"{client.provider.name} / {client.provider.model}",
            configured=client.provider.available,
        ),
        jira=IntegrationStatus(name="Jira", key="jira", **health["jira"]),
        github=IntegrationStatus(name="GitHub", key="github", **health["github"]),
        teams=IntegrationStatus(name="Microsoft Teams", key="teams", **health["teams"]),
        database=IntegrationStatus(
            name="SQLite",
            key="database",
            live=True,
            mode="local",
            detail=str(settings.DB_PATH),
            configured=True,
        ),
    )


# ---------------------------------------------------------------------------
# Root and SPA
# ---------------------------------------------------------------------------

if settings.FRONTEND_DIST.exists():
    app.mount(
        "/app",
        StaticFiles(directory=str(settings.FRONTEND_DIST), html=True),
        name="frontend",
    )


@app.get("/")
async def root():
    if settings.FRONTEND_DIST.exists():
        return RedirectResponse(url="/app/")
    return JSONResponse(
        {
            "service": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "docs": "/docs",
            "frontend": "Run `npm --prefix frontend install && npm --prefix frontend run build` to serve the React app at /app/.",
        }
    )
