import json
import logging
from typing import List, Optional
from fastapi import FastAPI, UploadFile, File, HTTPException, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.database import (
    init_db,
    save_meeting,
    get_meeting,
    get_drafts,
    get_draft_by_id,
    update_draft_status,
    get_history,
    get_project
)
from backend.models import (
    DraftRecord,
    HistoryRecord,
    ProjectContext,
    ApproveDraftRequest,
    EditDraftRequest,
    RejectDraftRequest
)
from backend.agent import pm_agent_app
from backend.mcp_servers import create_or_update_issue
from backend.config import settings
from backend.logger import get_logger, setup_logging

setup_logging()
logger = get_logger("main_api")

app = FastAPI(
    title="AI Project Manager Agent API",
    description="Autonomous PM Agent with LangGraph state orchestration and FastMCP integrations",
    version="1.0.0"
)

# Enable CORS for Streamlit frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup_event():
    await init_db()
    logger.info("Database initialized and ready.")

@app.get("/")
async def root():
    return {
        "service": "AI Project Manager Agent API",
        "status": "online",
        "endpoints": ["/upload_transcript", "/drafts", "/drafts/{id}/approve", "/history", "/project"]
    }

@app.get("/project", response_model=Optional[ProjectContext])
async def get_project_details(project_key: str = settings.JIRA_PROJECT_KEY):
    return await get_project(project_key)

class TranscriptUploadPayload(BaseModel):
    meeting_id: str
    title: str
    meeting_date: str
    attendees: List[str]
    transcript: List[dict] # list of {"line": int, "speaker": str, "text": str}
    is_private: bool = False

@app.post("/upload_transcript")
async def upload_transcript(payload: TranscriptUploadPayload):
    """
    Accepts a structured JSON transcript, numbers lines, and triggers LangGraph extraction pipeline.
    """
    # PRD Feature 12: Private meetings are never read or stored
    if payload.is_private:
        return {
            "status": "skipped",
            "message": "Meeting marked as private. In accordance with PRD privacy rules, it was not read or stored."
        }

    # Format transcript lines
    numbered_lines = []
    for item in payload.transcript:
        line_no = item.get("line", len(numbered_lines) + 1)
        speaker = item.get("speaker", "Speaker")
        text = item.get("text", "")
        numbered_lines.append(f"Line {line_no}: {speaker}: {text}")
    numbered_transcript = "\n".join(numbered_lines)

    # Save raw meeting to DB
    await save_meeting(
        meeting_id=payload.meeting_id,
        title=payload.title,
        meeting_date=payload.meeting_date,
        attendees=", ".join(payload.attendees),
        transcript=numbered_transcript,
        is_private=payload.is_private
    )

    # Execute LangGraph Agent State Machine
    initial_state = {
        "meeting_id": payload.meeting_id,
        "title": payload.title,
        "meeting_date": payload.meeting_date,
        "numbered_transcript": numbered_transcript,
        "agreed_scope": [],
        "jira_context": [],
        "github_prs": [],
        "github_commits": [],
        "generated_drafts": None,
        "inserted_draft_ids": []
    }

    result = await pm_agent_app.ainvoke(initial_state)
    drafts = await get_drafts(meeting_id=payload.meeting_id)

    return {
        "status": "success",
        "meeting_id": payload.meeting_id,
        "total_drafts_generated": len(drafts),
        "draft_ids": [d.id for d in drafts]
    }

@app.post("/upload_file")
async def upload_transcript_file(file: UploadFile = File(...)):
    """
    Direct file upload endpoint for Granola/Wispr JSON transcript files.
    """
    try:
        content = await file.read()
        data = json.loads(content.decode("utf-8"))
        payload = TranscriptUploadPayload(**data)
        return await upload_transcript(payload)
    except Exception as e:
        logger.error(f"Failed to process file upload: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid transcript file: {str(e)}")

@app.get("/drafts", response_model=List[DraftRecord])
async def list_drafts(
    meeting_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    draft_type: Optional[str] = Query(None)
):
    """
    Returns pending or filtered drafts from SQLite.
    """
    return await get_drafts(meeting_id=meeting_id, status=status, draft_type=draft_type)

@app.post("/drafts/{draft_id}/approve")
async def approve_draft(draft_id: int, request: ApproveDraftRequest):
    """
    Approves a draft, records audit history, and executes idempotent writeback to Jira.
    """
    draft = await get_draft_by_id(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    # If already approved, return without duplicating (Idempotency)
    if draft.status == "approved" and draft.jira_issue_key:
        return {
            "status": "already_approved",
            "draft_id": draft_id,
            "jira_issue_key": draft.jira_issue_key,
            "message": "Draft already approved and synced to Jira."
        }

    # Parse proposed change to construct Jira writeback payload
    jira_payload = {}
    try:
        content_dict = json.loads(draft.proposed_change)
    except Exception:
        content_dict = {"title": draft.title, "description": draft.proposed_change}

    if draft.type == "action_item":
        task_desc = f"{content_dict.get('task', draft.title)}\n\nAssigned: {content_dict.get('owner', 'MISSING')}\nDue Date: {content_dict.get('due_date', 'MISSING')}\n\n[Transcript Quote]\n{draft.source_quote}"
        jira_payload = {
            "summary": f"[Action] {content_dict.get('task', draft.title)[:80]}",
            "description": task_desc,
            "assignee": content_dict.get("owner") if content_dict.get("owner") != "MISSING" else None
        }
    elif draft.type == "status_update":
        jira_key = content_dict.get("jira_key")
        jira_payload = {
            "issue_key": jira_key,
            "status": content_dict.get("proposed_jira_status"),
            "description": f"Spoken update: {content_dict.get('spoken_status')}\nEvidence: {draft.github_evidence}\nQuote: {draft.source_quote}"
        }
    elif draft.type == "raid_item":
        jira_payload = {
            "summary": f"[{content_dict.get('category', 'Risk')}] {content_dict.get('title', draft.title)[:80]}",
            "description": f"{content_dict.get('description', '')}\nSeverity: {content_dict.get('severity', 'Medium')}\nOwner: {content_dict.get('owner', 'MISSING')}\n\n[Quote]: {draft.source_quote}"
        }
    elif draft.type == "scope_change":
        jira_payload = {
            "summary": f"[Scope Change] {content_dict.get('title', draft.title)[:80]}",
            "description": f"Requested by: {content_dict.get('requested_by', 'MISSING')}\nImpact: {content_dict.get('impact_assessment', '')}\n\n[Quote]: {draft.source_quote}"
        }
    else: # decision
        jira_payload = {
            "summary": f"[Decision] {content_dict.get('decision', draft.title)[:80]}",
            "description": f"Rationale: {content_dict.get('rationale', '')}\n\n[Quote]: {draft.source_quote}"
        }

    # Call FastMCP Jira Tool for writeback
    write_result = await create_or_update_issue(jira_payload)
    jira_issue_key = write_result.get("issue_key")

    # Update database status & write audit log
    await update_draft_status(
        draft_id=draft_id,
        new_status="approved",
        user_role=request.user_role,
        jira_ticket_id=jira_issue_key
    )

    return {
        "status": "approved",
        "draft_id": draft_id,
        "jira_issue_key": jira_issue_key,
        "write_result": write_result
    }

@app.post("/drafts/{draft_id}/edit")
async def edit_draft(draft_id: int, request: EditDraftRequest):
    """
    Edits a draft and logs original vs edited content into audit history.
    """
    success = await update_draft_status(
        draft_id=draft_id,
        new_status="edited",
        user_role=request.user_role,
        final_content=request.updated_proposed_change
    )
    if not success:
        raise HTTPException(status_code=404, detail="Draft not found")
    return {"status": "edited", "draft_id": draft_id}

@app.post("/drafts/{draft_id}/reject")
async def reject_draft(draft_id: int, request: RejectDraftRequest):
    """
    Rejects a draft and logs action to audit history.
    """
    success = await update_draft_status(
        draft_id=draft_id,
        new_status="rejected",
        user_role=request.user_role
    )
    if not success:
        raise HTTPException(status_code=404, detail="Draft not found")
    return {"status": "rejected", "draft_id": draft_id}

@app.get("/history", response_model=List[HistoryRecord])
async def list_audit_history():
    """
    Returns full history and audit log of decisions, approvals, and edits.
    """
    return await get_history()
