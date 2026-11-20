import type {
  ActionItem,
  ApproveResult,
  BatchApproveResult,
  Capabilities,
  CodeActivity,
  CostSummary,
  DemoAccount,
  Draft,
  Forecast,
  HistoryRecord,
  IntegrationsResponse,
  LoginResponse,
  Meeting,
  MeetingSummary,
  Metrics,
  Notification,
  Overview,
  PrivateSeries,
  Project,
  ProjectMember,
  QualityMetrics,
  SlaSummary,
  TeamsMeeting,
  UploadResult,
  User,
} from "./types";

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

let authToken: string | null = null;
let unauthorizedHandler: (() => void) | null = null;

export function setAuthToken(token: string | null) {
  authToken = token;
}

export function setUnauthorizedHandler(handler: (() => void) | null) {
  unauthorizedHandler = handler;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (authToken) headers.set("Authorization", `Bearer ${authToken}`);
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, { ...init, headers });
  } catch {
    throw new ApiError(0, "Cannot reach the API. Is the backend running on port 8000?");
  }
  if (response.status === 401 && authToken) {
    unauthorizedHandler?.();
  }
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const data = await response.json();
      if (typeof data?.detail === "string") detail = data.detail;
      else if (Array.isArray(data?.detail)) {
        detail = data.detail.map((item: { msg?: string }) => item.msg || "Invalid value").join("; ");
      }
    } catch {
      /* keep status text */
    }
    throw new ApiError(response.status, detail);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function query(params: Record<string, string | number | boolean | null | undefined>) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export const api = {
  login: (username: string, password: string) =>
    request<LoginResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  logout: () => request<{ status: string }>("/auth/logout", { method: "POST" }),
  me: () =>
    request<{ user: User; projects: Project[] }>("/auth/me"),
  demoAccounts: () =>
    request<{ password: string; accounts: DemoAccount[] }>("/auth/demo-accounts"),
  capabilities: () => request<Capabilities>("/capabilities"),

  projects: () => request<Project[]>("/projects"),
  createProject: (body: {
    project_key: string;
    name: string;
    agreed_scope?: string[];
    team_members?: string[];
    jira_project_key?: string | null;
    github_repo?: string | null;
  }) => request<Project>("/projects", { method: "POST", body: JSON.stringify(body) }),
  project: (key: string) => request<Project>(`/projects/${encodeURIComponent(key)}`),
  updateProject: (
    key: string,
    body: { jira_project_key?: string | null; github_repo?: string | null },
  ) =>
    request<Project>(`/projects/${encodeURIComponent(key)}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  members: (key: string) =>
    request<ProjectMember[]>(`/projects/${encodeURIComponent(key)}/members`),
  users: (projectKey?: string) => request<User[]>(`/users${query({ project_key: projectKey })}`),
  createUser: (body: {
    username: string;
    display_name?: string;
    email?: string;
    role: string;
    password?: string;
    project_key?: string;
  }) => request<User>("/users", { method: "POST", body: JSON.stringify(body) }),
  addMember: (key: string, userId: number) =>
    request<{ status: string }>(`/projects/${encodeURIComponent(key)}/members`, {
      method: "POST",
      body: JSON.stringify({ user_id: userId }),
    }),
  removeMember: (key: string, userId: number) =>
    request<{ status: string }>(`/projects/${encodeURIComponent(key)}/members/${userId}`, {
      method: "DELETE",
    }),

  overview: (projectKey?: string) =>
    request<Overview>(`/overview${query({ project_key: projectKey })}`),
  meetings: (projectKey?: string) =>
    request<MeetingSummary[]>(`/meetings${query({ project_key: projectKey })}`),
  meeting: (id: string) => request<Meeting>(`/meetings/${encodeURIComponent(id)}`),
  reprocess: (id: string) =>
    request<UploadResult>(`/meetings/${encodeURIComponent(id)}/reprocess`, { method: "POST" }),
  markMeetingPrivate: (id: string, reason?: string) =>
    request<{ status: string; series_id?: string | null; purged: boolean }>(
      `/meetings/${encodeURIComponent(id)}/private`,
      { method: "POST", body: JSON.stringify({ reason }) },
    ),
  sampleTranscript: () => request<Record<string, unknown>>("/sample-transcript"),

  uploadFile: (file: File, projectKey: string, isPrivate: boolean, seriesId?: string) => {
    const form = new FormData();
    form.append("file", file);
    return request<UploadResult>(
      `/meetings/upload-file${query({
        project_key: projectKey,
        is_private: isPrivate,
        series_id: seriesId,
      })}`,
      { method: "POST", body: form },
    );
  },
  uploadVtt: (
    file: File,
    fields: {
      title: string;
      meeting_date: string;
      attendees?: string;
      project_key: string;
      is_private: boolean;
      series_id?: string;
    },
  ) => {
    const form = new FormData();
    form.append("file", file);
    return request<UploadResult>(
      `/meetings/upload-vtt${query(fields)}`,
      { method: "POST", body: form },
    );
  },
  importJson: (data: Record<string, unknown>, projectKey: string, isPrivate: boolean, seriesId?: string) =>
    request<UploadResult>(
      `/meetings/import-json${query({
        project_key: projectKey,
        is_private: isPrivate,
        series_id: seriesId,
      })}`,
      { method: "POST", body: JSON.stringify({ data }) },
    ),

  teamsMeetings: (projectKey?: string) =>
    request<TeamsMeeting[]>(`/teams/meetings${query({ project_key: projectKey })}`),
  importTeamsMeeting: (meetingId: string, projectKey: string) =>
    request<UploadResult>("/teams/import", {
      method: "POST",
      body: JSON.stringify({ meeting_id: meetingId, project_key: projectKey }),
    }),

  drafts: (params: {
    project_key?: string;
    meeting_id?: string;
    status?: string;
    draft_type?: string;
  }) => request<Draft[]>(`/drafts${query(params)}`),
  draft: (id: number) => request<Draft>(`/drafts/${id}`),
  approveDraft: (id: number) =>
    request<ApproveResult>(`/drafts/${id}/approve`, { method: "POST" }),
  batchApprove: (ids: number[]) =>
    request<BatchApproveResult>("/drafts/batch-approve", {
      method: "POST",
      body: JSON.stringify({ draft_ids: ids }),
    }),
  editDraft: (id: number, proposedChange: Record<string, unknown>, note?: string) =>
    request<Draft>(`/drafts/${id}/edit`, {
      method: "POST",
      body: JSON.stringify({ proposed_change: proposedChange, note }),
    }),
  rejectDraft: (id: number, reason?: string) =>
    request<{ status: string }>(`/drafts/${id}/reject`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),
  retryDraft: (id: number) =>
    request<ApproveResult>(`/drafts/${id}/retry`, { method: "POST" }),
  history: (projectKey?: string) =>
    request<HistoryRecord[]>(`/history${query({ project_key: projectKey })}`),

  actionItems: (projectKey?: string, status?: string) =>
    request<ActionItem[]>(`/action-items${query({ project_key: projectKey, status })}`),
  updateActionItem: (id: number, status: string) =>
    request<ActionItem>(`/action-items/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ status }),
    }),

  notifications: (unreadOnly = false) =>
    request<Notification[]>(`/notifications${query({ unread_only: unreadOnly })}`),
  readNotification: (id: number) =>
    request<{ status: string }>(`/notifications/${id}/read`, { method: "POST" }),
  readAllNotifications: () =>
    request<{ updated: number }>("/notifications/read-all", { method: "POST" }),

  forecast: (projectKey?: string) =>
    request<Forecast>(`/forecast${query({ project_key: projectKey })}`),
  forecastHistory: (projectKey?: string) =>
    request<Forecast[]>(`/forecast/history${query({ project_key: projectKey })}`),
  checkAlerts: (projectKey?: string) =>
    request<{ checked: number; alerts_created: number; overdue_items: number }>(
      `/alerts/check${query({ project_key: projectKey })}`,
      { method: "POST" },
    ),

  metrics: (projectKey?: string) =>
    request<Metrics>(`/metrics${query({ project_key: projectKey })}`),
  cost: (projectKey?: string) =>
    request<CostSummary>(`/metrics/cost${query({ project_key: projectKey })}`),
  sla: (projectKey?: string) =>
    request<SlaSummary>(`/metrics/sla${query({ project_key: projectKey })}`),
  quality: (projectKey?: string) =>
    request<QualityMetrics>(`/metrics/quality${query({ project_key: projectKey })}`),

  codeActivity: (projectKey?: string) =>
    request<CodeActivity[]>(`/code-activity${query({ project_key: projectKey })}`),
  syncCode: (projectKey?: string) =>
    request<{ synced: number; mismatches: number; prs: number; commits: number; issues: number }>(
      `/code-activity/sync${query({ project_key: projectKey })}`,
      { method: "POST" },
    ),

  integrations: (projectKey?: string) =>
    request<IntegrationsResponse>(`/integrations${query({ project_key: projectKey })}`),
  privateSeries: (projectKey?: string) =>
    request<PrivateSeries[]>(`/privacy/series${query({ project_key: projectKey })}`),
  addPrivateSeries: (seriesId: string, projectKey: string, reason?: string) =>
    request<{ status: string }>(`/series/${encodeURIComponent(seriesId)}/private`, {
      method: "POST",
      body: JSON.stringify({ project_key: projectKey, reason }),
    }),
  removePrivateSeries: (seriesId: string, projectKey: string) =>
    request<{ status: string }>(
      `/series/${encodeURIComponent(seriesId)}/private${query({ project_key: projectKey })}`,
      { method: "DELETE" },
    ),
};
