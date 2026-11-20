from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# LLM extraction schemas (strict traceability and no guessing)
# ---------------------------------------------------------------------------


class Decision(BaseModel):
    decision: str = Field(description="Clear statement of what was decided")
    rationale: Optional[str] = Field(default="", description="Reasoning behind the decision if mentioned")
    source_quote: str = Field(description="Exact line number(s) and verbatim text from the transcript")


class ActionItem(BaseModel):
    task: str = Field(description="Actionable description of the task agreed upon")
    owner: str = Field(description="Task assignee, or 'MISSING' if not explicitly stated")
    due_date: str = Field(description="Due date as YYYY-MM-DD, or 'MISSING' if not explicitly stated")
    priority: Literal["High", "Medium", "Low"] = Field(default="Medium")
    source_quote: str = Field(description="Exact line number(s) and verbatim text from the transcript")


class RAIDItem(BaseModel):
    category: Literal["Risk", "Assumption", "Issue", "Dependency"]
    title: str
    description: str
    severity: Literal["High", "Medium", "Low"] = "Medium"
    owner: str = Field(default="MISSING")
    is_update_to_existing: bool = False
    existing_reference: Optional[str] = None
    source_quote: str


class ScopeChange(BaseModel):
    title: str
    description: str
    requested_by: str = Field(default="MISSING")
    impact_assessment: str
    source_quote: str


class StatusUpdate(BaseModel):
    jira_key: str = Field(description="Matching Jira ticket key (e.g. 'KAN-101') or 'UNMATCHED'")
    spoken_status: str
    proposed_jira_status: str
    github_evidence: str = "No GitHub evidence found"
    discrepancy_detected: bool = False
    discrepancy_details: Optional[str] = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source_quote: str


class MeetingDrafts(BaseModel):
    meeting_summary: str
    decisions: List[Decision] = Field(default_factory=list)
    action_items: List[ActionItem] = Field(default_factory=list)
    raid_items: List[RAIDItem] = Field(default_factory=list)
    scope_changes: List[ScopeChange] = Field(default_factory=list)
    status_updates: List[StatusUpdate] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Shared API models
# ---------------------------------------------------------------------------


ROLE_VALUES = ("Project Manager", "Team Member", "Sponsor", "Admin")
DraftType = Literal["decision", "action_item", "raid_item", "scope_change", "status_update"]
DraftStatus = Literal["waiting", "edited", "approved", "rejected"]
SyncStatus = Literal["pending", "synced", "partial", "failed", "not_required"]


class UserRecord(BaseModel):
    id: int
    username: str
    display_name: str
    email: Optional[str] = None
    role: str
    created_at: str


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    expires_at: str
    user: UserRecord
    projects: List[str] = Field(default_factory=list)


class CreateUserRequest(BaseModel):
    username: str
    display_name: Optional[str] = None
    email: Optional[str] = None
    role: Literal["Project Manager", "Team Member", "Sponsor", "Admin"] = "Team Member"
    password: Optional[str] = None
    project_key: Optional[str] = None


class ProjectContext(BaseModel):
    project_key: str
    name: str
    agreed_scope: List[str]
    team_members: List[str]
    jira_project_key: Optional[str] = None
    github_repo: Optional[str] = None
    pm_usernames: List[str] = Field(default_factory=list)
    member_count: int = 0


class ProjectMember(BaseModel):
    user_id: int
    username: str
    display_name: str
    email: Optional[str] = None
    role: str


# ---------------------------------------------------------------------------
# Meetings and transcripts
# ---------------------------------------------------------------------------


class TranscriptLine(BaseModel):
    line: int
    speaker: str
    text: str
    spoke_at: Optional[str] = None


class TranscriptUploadPayload(BaseModel):
    meeting_id: Optional[str] = None
    project_key: Optional[str] = None
    title: str
    meeting_date: str
    attendees: List[str] = Field(default_factory=list)
    transcript: List[TranscriptLine]
    is_private: bool = False
    series_id: Optional[str] = None
    source: str = "upload"
    source_url: Optional[str] = None

    @field_validator("meeting_date")
    @classmethod
    def valid_meeting_date(cls, value: str) -> str:
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("meeting_date must be YYYY-MM-DD") from exc
        return value


class MeetingRecord(BaseModel):
    id: str
    project_key: str
    title: str
    meeting_date: str
    attendees: List[str]
    summary: Optional[str] = None
    is_private: bool = False
    series_id: Optional[str] = None
    source: str = "upload"
    source_url: Optional[str] = None
    status: str = "ready"
    error: Optional[str] = None
    transcript_available_at: Optional[str] = None
    draft_count: int = 0
    created_at: str
    transcript_lines: List[TranscriptLine] = Field(default_factory=list)


class MeetingSummaryRecord(BaseModel):
    id: str
    project_key: str
    title: str
    meeting_date: str
    attendees: List[str]
    summary: Optional[str] = None
    is_private: bool = False
    series_id: Optional[str] = None
    source: str = "upload"
    status: str = "ready"
    draft_count: int = 0
    created_at: str


class UploadResult(BaseModel):
    status: str
    meeting_id: Optional[str] = None
    project_key: Optional[str] = None
    total_drafts_generated: int = 0
    draft_ids: List[int] = Field(default_factory=list)
    duration_seconds: Optional[float] = None
    within_sla: Optional[bool] = None
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None
    message: Optional[str] = None
    alerts_created: int = 0


class TeamsMeeting(BaseModel):
    id: str
    subject: str
    start_time: Optional[str] = None
    organizer: Optional[str] = None
    attendees: List[str] = Field(default_factory=list)
    series_id: Optional[str] = None
    has_transcript: bool = True
    already_imported: bool = False
    is_private_series: bool = False
    source: str = "teams"
    mode: str = "live"


class TeamsImportRequest(BaseModel):
    meeting_id: str
    project_key: Optional[str] = None
    is_private: bool = False


# ---------------------------------------------------------------------------
# Drafts, review and history
# ---------------------------------------------------------------------------


class DraftRecord(BaseModel):
    id: int
    project_key: str
    meeting_id: str
    meeting_title: Optional[str] = None
    type: DraftType
    title: str
    proposed_change: Dict[str, Any]
    source_quote: str
    source_lines: List[int] = Field(default_factory=list)
    github_evidence: Optional[str] = None
    discrepancy_detected: bool = False
    discrepancy_details: Optional[str] = None
    confidence: float = 1.0
    status: DraftStatus = "waiting"
    jira_issue_key: Optional[str] = None
    sync_status: SyncStatus = "pending"
    sync_error: Optional[str] = None
    sync_attempts: int = 0
    idempotency_key: Optional[str] = None
    created_at: str
    updated_at: str


class EditDraftRequest(BaseModel):
    proposed_change: Dict[str, Any] | str
    note: Optional[str] = None


class RejectDraftRequest(BaseModel):
    reason: Optional[str] = None


class BatchApproveRequest(BaseModel):
    draft_ids: List[int] = Field(min_length=1)


class ApproveResult(BaseModel):
    draft_id: int
    status: str
    jira_issue_key: Optional[str] = None
    jira_url: Optional[str] = None
    sync_status: Optional[str] = None
    error: Optional[str] = None
    action_item_id: Optional[int] = None


class BatchApproveResult(BaseModel):
    approved: int
    failed: int
    results: List[ApproveResult]


class HistoryRecord(BaseModel):
    id: int
    draft_id: int
    project_key: str
    action: str
    user_name: Optional[str] = None
    user_role: str
    original_content: str
    final_content: str
    note: Optional[str] = None
    jira_ticket_id: Optional[str] = None
    timestamp: str


# ---------------------------------------------------------------------------
# Action items, notifications, forecast
# ---------------------------------------------------------------------------


class ActionItemRecord(BaseModel):
    id: int
    project_key: str
    meeting_id: Optional[str] = None
    draft_id: Optional[int] = None
    description: str
    owner_username: Optional[str] = None
    owner_display: Optional[str] = None
    due_date: Optional[str] = None
    status: Literal["open", "in_progress", "done", "cancelled"] = "open"
    priority: str = "Medium"
    jira_issue_key: Optional[str] = None
    source_quote: str = ""
    created_at: str
    updated_at: str

    @property
    def is_overdue(self) -> bool:
        return bool(
            self.due_date
            and self.due_date != "MISSING"
            and self.status in {"open", "in_progress"}
            and self.due_date < date.today().isoformat()
        )


class UpdateActionItemRequest(BaseModel):
    status: Literal["open", "in_progress", "done", "cancelled"]


class NotificationRecord(BaseModel):
    id: int
    project_key: str
    meeting_id: Optional[str] = None
    draft_id: Optional[int] = None
    action_item_id: Optional[int] = None
    type: str
    recipient_username: str
    message: str
    link: Optional[str] = None
    read: bool = False
    created_at: str


class ForecastRecord(BaseModel):
    project_key: str
    date: str
    on_track: bool
    health: str
    total_items: int = 0
    open_items: int = 0
    overdue_items: int = 0
    completed_items: int = 0
    notes: Optional[str] = None
    updated_at: str


class AlertCheckResult(BaseModel):
    checked: int
    alerts_created: int
    overdue_items: int
    forecast: Optional[ForecastRecord] = None


# ---------------------------------------------------------------------------
# Code activity
# ---------------------------------------------------------------------------


class CodeActivityRecord(BaseModel):
    id: int
    project_key: str
    repo: str
    kind: Literal["pr", "commit", "issue"]
    external_id: str
    title: str
    author: Optional[str] = None
    state: Optional[str] = None
    url: Optional[str] = None
    linked_jira_key: Optional[str] = None
    linked_action_item_id: Optional[int] = None
    mismatch: bool = False
    mismatch_details: Optional[str] = None
    activity_at: Optional[str] = None
    synced_at: str


class CodeActivitySyncResult(BaseModel):
    synced: int
    mismatches: int
    prs: int
    commits: int
    issues: int


# ---------------------------------------------------------------------------
# Metrics, integrations, privacy
# ---------------------------------------------------------------------------


class CostByMeeting(BaseModel):
    meeting_id: str
    meeting_title: Optional[str] = None
    project_key: Optional[str] = None
    cost_usd: float = 0.0
    calls: int = 0
    total_tokens: int = 0


class CostSummary(BaseModel):
    project_key: Optional[str] = None
    total_cost_usd: float = 0.0
    calls: int = 0
    total_tokens: int = 0
    avg_cost_usd: float = 0.0
    avg_cost_per_meeting_usd: float = 0.0
    by_meeting: List[CostByMeeting] = Field(default_factory=list)
    by_project: List[Dict[str, Any]] = Field(default_factory=list)
    by_model: List[Dict[str, Any]] = Field(default_factory=list)


class SlaSummary(BaseModel):
    project_key: Optional[str] = None
    total_meetings: int = 0
    within_sla: int = 0
    sla_compliance_pct: float = 0.0
    avg_duration_seconds: float = 0.0
    p95_duration_seconds: float = 0.0
    last_duration_seconds: Optional[float] = None


class QualityMetrics(BaseModel):
    project_key: Optional[str] = None
    drafts_total: int = 0
    drafts_waiting: int = 0
    drafts_approved: int = 0
    drafts_edited: int = 0
    drafts_rejected: int = 0
    approved_without_edit_pct: float = 1.0
    action_items_total: int = 0
    action_items_overdue: int = 0
    scope_changes: int = 0
    discrepancies: int = 0
    meetings: int = 0


class IntegrationStatus(BaseModel):
    name: str
    key: str
    live: bool
    mode: str
    detail: str
    configured: bool


class IntegrationsResponse(BaseModel):
    ai: IntegrationStatus
    jira: IntegrationStatus
    github: IntegrationStatus
    teams: IntegrationStatus
    database: IntegrationStatus


class PrivateSeriesRecord(BaseModel):
    series_id: str
    project_key: str
    reason: Optional[str] = None
    created_by: Optional[str] = None
    created_at: str


class PrivacyMarkRequest(BaseModel):
    reason: Optional[str] = None


class HealthResponse(BaseModel):
    status: str
    version: str
    database: str
    scheduler: str
