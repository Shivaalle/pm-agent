export type Role = "Project Manager" | "Team Member" | "Sponsor" | "Admin";
export type ViewId = "overview" | "meetings" | "review" | "actions" | "metrics" | "admin";
export type DraftStatus = "waiting" | "edited" | "approved" | "rejected";
export type SyncStatus = "pending" | "synced" | "partial" | "failed" | "not_required";
export type ActionStatus = "open" | "in_progress" | "done" | "cancelled";

export interface User {
  id: number;
  username: string;
  display_name: string;
  email?: string | null;
  role: Role;
  created_at: string;
}

export interface Project {
  project_key: string;
  name: string;
  agreed_scope: string[];
  team_members: string[];
  jira_project_key?: string | null;
  github_repo?: string | null;
  pm_usernames: string[];
  member_count: number;
}

export interface ProjectMember {
  user_id: number;
  username: string;
  display_name: string;
  email?: string | null;
  role: Role;
}

export interface LoginResponse {
  token: string;
  expires_at: string;
  user: User;
  projects: string[];
}

export interface Session {
  token: string;
  user: User;
  projects: Project[];
  expires_at: string;
}

export interface DemoAccount {
  username: string;
  display_name: string;
  role: Role;
}

export interface Capabilities {
  ai: { provider: string; model: string; mode: string };
  jira: { mode: string; project: string };
  github: { mode: string; repo?: string | null };
  teams: { mode: string };
  sla_seconds: number;
}

export interface TranscriptLine {
  line: number;
  speaker: string;
  text: string;
  spoke_at?: string | null;
}

export interface MeetingSummary {
  id: string;
  project_key: string;
  title: string;
  meeting_date: string;
  attendees: string[];
  summary?: string | null;
  is_private: boolean;
  series_id?: string | null;
  source: string;
  status: string;
  draft_count: number;
  created_at: string;
}

export interface Meeting extends MeetingSummary {
  source_url?: string | null;
  error?: string | null;
  transcript_available_at?: string | null;
  transcript_lines: TranscriptLine[];
}

export interface Draft {
  id: number;
  project_key: string;
  meeting_id: string;
  meeting_title?: string | null;
  type: "decision" | "action_item" | "raid_item" | "scope_change" | "status_update";
  title: string;
  proposed_change: Record<string, unknown>;
  source_quote: string;
  source_lines: number[];
  github_evidence?: string | null;
  discrepancy_detected: boolean;
  discrepancy_details?: string | null;
  confidence: number;
  status: DraftStatus;
  jira_issue_key?: string | null;
  sync_status: SyncStatus;
  sync_error?: string | null;
  sync_attempts: number;
  idempotency_key?: string | null;
  created_at: string;
  updated_at: string;
}

export interface ApproveResult {
  draft_id: number;
  status: string;
  jira_issue_key?: string | null;
  jira_url?: string | null;
  sync_status?: string | null;
  error?: string | null;
  action_item_id?: number | null;
}

export interface BatchApproveResult {
  approved: number;
  failed: number;
  results: ApproveResult[];
}

export interface ActionItem {
  id: number;
  project_key: string;
  meeting_id?: string | null;
  draft_id?: number | null;
  description: string;
  owner_username?: string | null;
  owner_display?: string | null;
  due_date?: string | null;
  status: ActionStatus;
  priority: string;
  jira_issue_key?: string | null;
  source_quote: string;
  created_at: string;
  updated_at: string;
}

export interface Notification {
  id: number;
  project_key: string;
  meeting_id?: string | null;
  draft_id?: number | null;
  action_item_id?: number | null;
  type: string;
  recipient_username: string;
  message: string;
  link?: string | null;
  read: boolean;
  created_at: string;
}

export interface Forecast {
  project_key: string;
  date: string;
  on_track: boolean;
  health: string;
  total_items: number;
  open_items: number;
  overdue_items: number;
  completed_items: number;
  notes?: string | null;
  updated_at: string;
}

export interface CostByMeeting {
  meeting_id: string;
  meeting_title?: string | null;
  project_key?: string | null;
  cost_usd: number;
  calls: number;
  total_tokens: number;
}

export interface CostSummary {
  project_key?: string | null;
  total_cost_usd: number;
  calls: number;
  total_tokens: number;
  avg_cost_usd: number;
  avg_cost_per_meeting_usd: number;
  by_meeting: CostByMeeting[];
  by_project: Array<Record<string, unknown>>;
  by_model: Array<Record<string, unknown>>;
}

export interface SlaSummary {
  project_key?: string | null;
  total_meetings: number;
  within_sla: number;
  sla_compliance_pct: number;
  avg_duration_seconds: number;
  p95_duration_seconds: number;
  last_duration_seconds?: number | null;
}

export interface QualityMetrics {
  project_key?: string | null;
  drafts_total: number;
  drafts_waiting: number;
  drafts_approved: number;
  drafts_edited: number;
  drafts_rejected: number;
  approved_without_edit_pct: number;
  action_items_total: number;
  action_items_overdue: number;
  scope_changes: number;
  discrepancies: number;
  meetings: number;
}

export interface Metrics {
  cost: CostSummary;
  sla: SlaSummary;
  quality: QualityMetrics;
}

export interface CodeActivity {
  id: number;
  project_key: string;
  repo: string;
  kind: "pr" | "commit" | "issue";
  external_id: string;
  title: string;
  author?: string | null;
  state?: string | null;
  url?: string | null;
  linked_jira_key?: string | null;
  linked_action_item_id?: number | null;
  mismatch: boolean;
  mismatch_details?: string | null;
  activity_at?: string | null;
  synced_at: string;
}

export interface UploadResult {
  status: string;
  meeting_id?: string | null;
  project_key?: string | null;
  total_drafts_generated: number;
  draft_ids: number[];
  duration_seconds?: number | null;
  within_sla?: boolean | null;
  llm_provider?: string | null;
  llm_model?: string | null;
  message?: string | null;
  alerts_created: number;
}

export interface TeamsMeeting {
  id: string;
  subject: string;
  start_time?: string | null;
  organizer?: string | null;
  attendees: string[];
  series_id?: string | null;
  has_transcript: boolean;
  already_imported: boolean;
  is_private_series: boolean;
  source: string;
  mode: string;
}

export interface Overview {
  project: Project | null;
  forecast: Forecast;
  forecast_history: Forecast[];
  quality: QualityMetrics;
  drafts: Draft[];
  action_items: ActionItem[];
  meetings: MeetingSummary[];
  code_activity: CodeActivity[];
  role: Role;
}

export interface IntegrationStatus {
  name: string;
  key: string;
  live: boolean;
  mode: string;
  detail: string;
  configured: boolean;
}

export interface IntegrationsResponse {
  ai: IntegrationStatus;
  jira: IntegrationStatus;
  github: IntegrationStatus;
  teams: IntegrationStatus;
  database: IntegrationStatus;
}

export interface PrivateSeries {
  series_id: string;
  project_key: string;
  reason?: string | null;
  created_by?: string | null;
  created_at: string;
}

export interface HistoryRecord {
  id: number;
  draft_id: number;
  project_key: string;
  action: string;
  user_name?: string | null;
  user_role: string;
  original_content: string;
  final_content: string;
  note?: string | null;
  jira_ticket_id?: string | null;
  timestamp: string;
}
