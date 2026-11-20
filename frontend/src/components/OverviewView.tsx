import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type { Overview, Project, Session, ViewId } from "../types";
import { useToast } from "../toast";
import {
  draftTypeLabel,
  formatDate,
  formatSeconds,
  healthLabel,
  isOverdue,
  relativeTime,
  titleCase,
} from "../utils";
import { Badge, EmptyState, ErrorBlock, LoadingBlock, SectionHeader, Stat } from "./ui";

export function OverviewView({
  session,
  project,
  onNavigate,
}: {
  session: Session;
  project: Project | null;
  onNavigate: (view: ViewId) => void;
  onRefreshNotifications: () => void;
}) {
  const toast = useToast();
  const [overview, setOverview] = useState<Overview | null>(null);
  const [slaP95, setSlaP95] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const projectKey = project?.project_key ?? "";

  useEffect(() => {
    if (!projectKey) return;
    let cancelled = false;
    setLoading(true);
    setError("");
    api
      .overview(projectKey)
      .then((data) => {
        if (!cancelled) setOverview(data);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : "Could not load the overview.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    if (["Project Manager", "Sponsor", "Admin"].includes(session.user.role)) {
      api
        .sla(projectKey)
        .then((data) => {
          if (!cancelled) setSlaP95(data.p95_duration_seconds);
        })
        .catch(() => {
          if (!cancelled) setSlaP95(null);
        });
    }
    return () => {
      cancelled = true;
    };
  }, [projectKey, session.user.role]);

  if (loading && !overview) return <LoadingBlock label="Loading project pulse…" />;
  if (error) return <ErrorBlock message={error} onRetry={() => window.location.reload()} />;
  if (!overview || !project) {
    return <EmptyState title="No project context">Ask an Admin to add you to a project.</EmptyState>;
  }

  const openItems = overview.action_items.filter((item) => item.status === "open" || item.status === "in_progress");
  const overdueItems = openItems.filter((item) => isOverdue(item.due_date, item.status));
  const attention = [...overview.action_items]
    .filter((item) => item.status === "open" || item.status === "in_progress")
    .sort((a, b) => {
      const aOver = isOverdue(a.due_date, a.status) ? 0 : 1;
      const bOver = isOverdue(b.due_date, b.status) ? 0 : 1;
      if (aOver !== bOver) return aOver - bOver;
      return (a.due_date ?? "9999").localeCompare(b.due_date ?? "9999");
    })
    .slice(0, 5);

  const pendingDrafts = overview.drafts.filter((draft) => draft.status === "waiting" || draft.status === "edited");
  const canReview = ["Project Manager", "Admin"].includes(session.user.role);
  const canSeeMetrics = ["Project Manager", "Sponsor", "Admin"].includes(session.user.role);
  const maxForecast = Math.max(...overview.forecast_history.map((item) => item.total_items), 1);
  const mismatches = overview.code_activity.filter((item) => item.mismatch);

  return (
    <div className="view-stack">
      <SectionHeader
        eyebrow={`${project.project_key} · ${session.user.role}`}
        title={`${project.name}`}
        description="A single, honest view of delivery: what was said, what was approved, what is late, and what the code says."
        actions={
          <>
            {canReview ? (
              <button type="button" className="button button-secondary" onClick={() => onNavigate("review")}>
                Review queue
                {pendingDrafts.length ? <span className="button-count">{pendingDrafts.length}</span> : null}
              </button>
            ) : null}
            {["Project Manager", "Admin"].includes(session.user.role) ? (
              <button type="button" className="button button-primary" onClick={() => onNavigate("meetings")}>
                Upload meeting
              </button>
            ) : null}
          </>
        }
      />

      <section className={`pulse-card pulse-${overview.forecast.health}`} aria-labelledby="pulse-title">
        <div>
          <span className="eyebrow">Project pulse</span>
          <h2 id="pulse-title">
            {healthLabel(overview.forecast.health)}
            {overview.forecast.overdue_items ? " — overdue work needs a decision." : " — delivery signals are healthy."}
          </h2>
          <p>{overview.forecast.notes}</p>
        </div>
        <div className="pulse-facts">
          <div>
            <strong>{overview.forecast.open_items}</strong>
            <span>open</span>
          </div>
          <div>
            <strong>{overview.forecast.completed_items}</strong>
            <span>done</span>
          </div>
          <div className={overview.forecast.overdue_items ? "fact-danger" : ""}>
            <strong>{overview.forecast.overdue_items}</strong>
            <span>overdue</span>
          </div>
        </div>
      </section>

      <section className="stat-grid" aria-label="Project summary">
        <Stat
          label="Open action items"
          value={openItems.length}
          detail={overdueItems.length ? `${overdueItems.length} overdue` : "Nothing overdue"}
          tone={overdueItems.length ? "danger" : undefined}
        />
        <Stat
          label={canReview ? "Drafts awaiting approval" : "Approved signals"}
          value={canReview ? pendingDrafts.length : overview.drafts.length}
          detail={canReview ? "Nothing writes to Jira without a decision" : "Decisions, RAID and scope changes"}
        />
        <Stat label="Meetings tracked" value={overview.meetings.length} detail="Latest 10 shown below" />
        <Stat
          label="Deadline SLA"
          value={canSeeMetrics ? formatSeconds(slaP95) : "—"}
          detail={canSeeMetrics ? "p95 meeting-to-drafts latency" : "Visible to PM, Sponsor and Admin"}
        />
      </section>

      <div className="content-grid">
        <section className="panel panel-span-2" aria-labelledby="attention-title">
          <div className="panel-heading">
            <div>
              <h2 id="attention-title">
                {session.user.role === "Team Member" ? "Your next actions" : "Needs attention"}
              </h2>
              <p>Overdue and soonest work, based on approved action items.</p>
            </div>
            <button type="button" className="text-button" onClick={() => onNavigate("actions")}>
              All action items →
            </button>
          </div>
          {attention.length === 0 ? (
            <EmptyState title="No open action items">
              Approve an action-item draft and it will appear here with an owner and due date.
            </EmptyState>
          ) : (
            <ul className="attention-list">
              {attention.map((item) => {
                const overdue = isOverdue(item.due_date, item.status);
                return (
                  <li key={item.id} className={`attention-item${overdue ? " attention-overdue" : ""}`}>
                    <div className="attention-main">
                      <div className="attention-title-row">
                        <strong>{item.description}</strong>
                        <Badge tone={overdue ? "danger" : item.status === "in_progress" ? "info" : "neutral"}>
                          {overdue ? "Overdue" : titleCase(item.status)}
                        </Badge>
                      </div>
                      <span className="attention-meta">
                        {item.owner_display || "Unassigned"} · due {formatDate(item.due_date)}
                        {item.jira_issue_key ? ` · ${item.jira_issue_key}` : ""}
                      </span>
                    </div>
                    <Badge tone={item.priority === "High" ? "warning" : "neutral"}>{item.priority}</Badge>
                  </li>
                );
              })}
            </ul>
          )}
        </section>

        <section className="panel" aria-labelledby="forecast-title">
          <div className="panel-heading">
            <div>
              <h2 id="forecast-title">Forecast history</h2>
              <p>Open work across recent snapshots.</p>
            </div>
          </div>
          {overview.forecast_history.length === 0 ? (
            <EmptyState title="No forecast yet">The forecast appears after the first approved action item.</EmptyState>
          ) : (
            <div className="spark-chart" role="img" aria-label="Open action items over recent days">
              {overview.forecast_history.toReversed().map((item) => (
                <div key={item.date} className="spark-column">
                  <div className="spark-track">
                    <div
                      className={`spark-bar spark-${item.health}`}
                      style={{ height: `${Math.max(8, (item.open_items / maxForecast) * 100)}%` }}
                      title={`${formatDate(item.date)}: ${item.open_items} open, ${item.overdue_items} overdue`}
                    />
                  </div>
                  <span>{item.date.slice(5)}</span>
                </div>
              ))}
            </div>
          )}
          <div className="forecast-note">
            <Badge tone={overview.forecast.health === "off_track" ? "danger" : overview.forecast.health === "at_risk" ? "warning" : "success"}>
              {healthLabel(overview.forecast.health)}
            </Badge>
            <span>Updated {relativeTime(overview.forecast.updated_at)}</span>
          </div>
        </section>

        <section className="panel" aria-labelledby="meetings-title">
          <div className="panel-heading">
            <div>
              <h2 id="meetings-title">Recent meetings</h2>
              <p>Source material behind every draft.</p>
            </div>
            {["Project Manager", "Admin"].includes(session.user.role) ? (
              <button type="button" className="text-button" onClick={() => onNavigate("meetings")}>
                Manage →
              </button>
            ) : null}
          </div>
          {overview.meetings.length === 0 ? (
            <EmptyState title="No meetings yet">Upload a transcript to create grounded drafts.</EmptyState>
          ) : (
            <ul className="meeting-list">
              {overview.meetings.slice(0, 5).map((meeting) => (
                <li key={meeting.id}>
                  <div>
                    <strong>{meeting.title}</strong>
                    <span>
                      {formatDate(meeting.meeting_date)} · {meeting.draft_count} draft{meeting.draft_count === 1 ? "" : "s"}
                    </span>
                  </div>
                  <Badge tone={meeting.status === "ready" ? "success" : meeting.status === "failed" ? "danger" : "info"}>
                    {titleCase(meeting.status)}
                  </Badge>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="panel panel-span-2" aria-labelledby="drafts-title">
          <div className="panel-heading">
            <div>
              <h2 id="drafts-title">{canReview ? "Review queue" : "Approved decisions, RAID and scope"}</h2>
              <p>
                {canReview
                  ? "Each item shows the exact transcript line it came from."
                  : "Only approved items are visible in this read-only view."}
              </p>
            </div>
            {canReview ? (
              <button type="button" className="text-button" onClick={() => onNavigate("review")}>
                Open queue →
              </button>
            ) : null}
          </div>
          {overview.drafts.length === 0 ? (
            <EmptyState title="Nothing waiting">
              {canReview ? "Upload a meeting or reprocess one to generate new drafts." : "Approved decisions will appear here."}
            </EmptyState>
          ) : (
            <ul className="draft-preview-list">
              {overview.drafts.slice(0, 6).map((draft) => (
                <li key={draft.id}>
                  <Badge tone={draft.type === "scope_change" ? "warning" : "accent"}>
                    {draftTypeLabel(draft.type)}
                  </Badge>
                  <div className="draft-preview-copy">
                    <strong>{draft.title}</strong>
                    <span>{draft.meeting_title || draft.meeting_id}</span>
                  </div>
                  {draft.discrepancy_detected ? <Badge tone="danger">Code mismatch</Badge> : null}
                  {canReview ? (
                    <button type="button" className="button button-small button-secondary" onClick={() => onNavigate("review")}>
                      Review
                    </button>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </section>

        {canSeeMetrics ? (
          <section className="panel panel-span-2" aria-labelledby="code-title">
            <div className="panel-heading">
              <div>
                <h2 id="code-title">Code vs. conversation</h2>
                <p>GitHub pull requests, commits and issues linked to Jira keys and action items.</p>
              </div>
              <button type="button" className="text-button" onClick={() => onNavigate("metrics")}>
                Quality metrics →
              </button>
            </div>
            {mismatches.length ? (
              <div className="callout callout-danger" role="note">
                <strong>{mismatches.length} code/conversation mismatch{mismatches.length === 1 ? "" : "es"}</strong>
                <span>Spoken status conflicts with open code work. Check the draft before approving.</span>
              </div>
            ) : null}
            {overview.code_activity.length === 0 ? (
              <EmptyState title="No code activity synced">Use “Sync GitHub” on the Metrics page.</EmptyState>
            ) : (
              <ul className="code-list">
                {overview.code_activity.slice(0, 5).map((activity) => (
                  <li key={`${activity.kind}-${activity.external_id}`}>
                    <Badge tone="neutral">{activity.kind.toUpperCase()}</Badge>
                    <div>
                      <strong>{activity.title}</strong>
                      <span>
                        {activity.linked_jira_key || "unlinked"} · {activity.state || "unknown"} · {relativeTime(activity.activity_at)}
                      </span>
                    </div>
                    {activity.mismatch ? <Badge tone="danger">Mismatch</Badge> : <Badge tone="success">Consistent</Badge>}
                  </li>
                ))}
              </ul>
            )}
          </section>
        ) : null}
      </div>

      {session.user.role === "Project Manager" && overdueItems.length ? (
        <button
          type="button"
          className="button button-secondary"
          onClick={async () => {
            try {
              const result = await api.checkAlerts(projectKey);
              toast.success(`Alert scan complete · ${result.overdue_items} overdue · ${result.alerts_created} alert(s) created.`);
              onNavigate("actions");
            } catch (err) {
              toast.error(err instanceof ApiError ? err.message : "Alert scan failed");
            }
          }}
        >
          Run deadline scan now
        </button>
      ) : null}
    </div>
  );
}
