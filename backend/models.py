from typing import List, Optional, Literal
from pydantic import BaseModel, Field

# --- LLM Extraction Models (Enforcing Strict Traceability & No Guessing) ---

class Decision(BaseModel):
    decision: str = Field(description="Clear statement of what was decided")
    rationale: Optional[str] = Field(default="", description="Reasoning behind the decision if mentioned")
    source_quote: str = Field(description="Exact line number(s) and verbatim text from the transcript. MANDATORY.")

class ActionItem(BaseModel):
    task: str = Field(description="Actionable description of the task agreed upon")
    owner: str = Field(description="Task assignee. Must be 'MISSING' if not explicitly stated in the transcript.")
    due_date: str = Field(description="Target date resolved to YYYY-MM-DD or 'MISSING' if not explicitly stated.")
    priority: Literal["High", "Medium", "Low"] = Field(default="Medium", description="Suggested priority")
    source_quote: str = Field(description="Exact line number(s) and verbatim text from the transcript. MANDATORY.")

class RAIDItem(BaseModel):
    category: Literal["Risk", "Assumption", "Issue", "Dependency"] = Field(
        description="Type of RAID item: Risk (might go wrong), Assumption, Issue (already an issue), Dependency (blocked by external)"
    )
    title: str = Field(description="Concise title of the risk/issue/assumption/dependency")
    description: str = Field(description="Detailed explanation")
    severity: Literal["High", "Medium", "Low"] = Field(default="Medium")
    owner: str = Field(default="MISSING", description="Owner or 'MISSING' if not specified")
    is_update_to_existing: bool = Field(
        default=False, 
        description="True if this updates an existing known project risk/issue rather than a new one"
    )
    existing_reference: Optional[str] = Field(default=None, description="Reference ID if updating existing")
    source_quote: str = Field(description="Exact line number(s) and verbatim text from the transcript. MANDATORY.")

class ScopeChange(BaseModel):
    title: str = Field(description="Short title of the proposed scope addition or modification")
    description: str = Field(description="Detailed description of what was requested outside the agreed baseline scope")
    requested_by: str = Field(default="MISSING", description="Person who requested it, or 'MISSING'")
    impact_assessment: str = Field(description="Likely impact on timeline, architecture, or resources")
    source_quote: str = Field(description="Exact line number(s) and verbatim text from the transcript. MANDATORY.")

class StatusUpdate(BaseModel):
    jira_key: str = Field(description="Matching Jira ticket key (e.g. 'PM-101') or 'UNMATCHED'")
    spoken_status: str = Field(description="Verbatim claim spoken in meeting (e.g. 'Auth API is done')")
    proposed_jira_status: str = Field(description="Target Jira status (e.g. 'Done', 'In Progress', 'Blocked')")
    github_evidence: str = Field(
        default="No GitHub evidence found", 
        description="Citations from GitHub PRs/commits supporting or contradicting the update"
    )
    discrepancy_detected: bool = Field(
        default=False, 
        description="True if spoken status contradicts actual GitHub activity (e.g. spoken 'done' but PR is still open)"
    )
    discrepancy_details: Optional[str] = Field(
        default=None, 
        description="Explanation of the conflict between spoken status and Git activity"
    )
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Confidence score from 0.0 to 1.0")
    source_quote: str = Field(description="Exact line number(s) and verbatim text from the transcript. MANDATORY.")

class MeetingDrafts(BaseModel):
    meeting_summary: str = Field(description="Executive 2-3 paragraph summary of the meeting")
    decisions: List[Decision] = Field(default_factory=list, description="Decisions made during the meeting")
    action_items: List[ActionItem] = Field(default_factory=list, description="Action items agreed upon")
    raid_items: List[RAIDItem] = Field(default_factory=list, description="RAID log items identified")
    scope_changes: List[ScopeChange] = Field(default_factory=list, description="Scope change requests identified")
    status_updates: List[StatusUpdate] = Field(default_factory=list, description="Status updates mapped to tracker & Git")


# --- Database & API Transfer Models ---

class DraftRecord(BaseModel):
    id: int
    meeting_id: str
    type: Literal["decision", "action_item", "raid_item", "scope_change", "status_update"]
    title: str
    proposed_change: str  # JSON-encoded payload or formatted text
    source_quote: str
    github_evidence: Optional[str] = None
    discrepancy_detected: bool = False
    discrepancy_details: Optional[str] = None
    confidence: float = 1.0
    status: Literal["waiting", "approved", "edited", "rejected"] = "waiting"
    jira_issue_key: Optional[str] = None
    created_at: str
    updated_at: str

class HistoryRecord(BaseModel):
    id: int
    draft_id: int
    action: Literal["approved", "edited", "rejected"]
    user_role: str
    original_content: str
    final_content: str
    jira_ticket_id: Optional[str] = None
    timestamp: str

class MeetingRecord(BaseModel):
    id: str
    title: str
    meeting_date: str
    attendees: str
    summary: Optional[str] = None
    is_private: bool = False
    created_at: str

class ProjectContext(BaseModel):
    project_key: str
    name: str
    agreed_scope: List[str]
    team_members: List[str]

# API Request Payloads
class ApproveDraftRequest(BaseModel):
    user_role: str = "Project Manager"

class EditDraftRequest(BaseModel):
    updated_proposed_change: str
    user_role: str = "Project Manager"

class RejectDraftRequest(BaseModel):
    reason: Optional[str] = None
    user_role: str = "Project Manager"
