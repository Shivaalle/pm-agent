import { useEffect, useMemo, useState } from "react";
import { api, ApiError } from "../api";
import type { ActionItem, Project, Session } from "../types";
import { useToast } from "../toast";
import { formatDate, isOverdue, titleCase } from "../utils";
import { Badge, EmptyState, ErrorBlock, LoadingBlock, SectionHeader, Stat } from "./ui";

export function ActionItemsView({
  session,
  project,
}: {
  session: Session;
  project: Project | null;
  onRefreshNotifications: () => void;
}) {
  const toast = useToast();
  const [items, setItems] = useState<ActionItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [statusFilter, setStatusFilter] = useState("open_all");
  const [busyId, setBusyId] = useState<number | null>(null);
  const [scanBusy, setScanBusy] = useState(false);

  const projectKey = project?.project_key ?? "";
  const canUpdate = ["Project Manager", "Admin", "Team Member"].includes(session.user.role);

  async function loadItems() {
    if (!projectKey) return;
    setLoading(true);
    setError("");
    try {
      setItems(await api.actionItems(projectKey));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load action items.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadItems();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectKey]);

  const filtered = useMemo(() => {
    return items.filter((item) => {
      if (statusFilter === "open_all") return item.status === "open" || item.status === "in_progress";
      if (statusFilter === "all") return true;
      return item.status === statusFilter;
    });
  }, [items, statusFilter]);

  const openItems = items.filter((item) => item.status === "open" || item.status === "in_progress");
  const overdue = openItems.filter((item) => isOverdue(item.due_date, item.status));
  const done = items.filter((item) => item.status === "done");

  async function updateStatus(item: ActionItem, status: string) {
    setBusyId(item.id);
    try {
      const updated = await api.updateActionItem(item.id, status);
      setItems((current) => current.map((entry) => (entry.id === item.id ? updated : entry)));
      toast.success(`“${item.description.slice(0, 50)}” marked ${titleCase(status)}.`);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Could not update the action item.");
    } finally {
      setBusyId(null);
    }
  }

  async function runScan() {
    setScanBusy(true);
    try {
      const result = await api.checkAlerts(projectKey);
      toast.success(`Scan complete · ${result.overdue_items} overdue · ${result.alerts_created} alert(s) created.`);
      await loadItems();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Alert scan failed.");
    } finally {
      setScanBusy(false);
    }
  }

  return (
    <div className="view-stack">
      <SectionHeader
        eyebrow={`${projectKey} · ${session.user.role === "Team Member" ? "your work" : "delivery"}`}
        title="Action items"
        description={
          session.user.role === "Team Member"
            ? "Only your assigned action items are shown. Update status as work moves."
            : "Approved action items become trackable work with owners, due dates and deadline alerts."
        }
        actions={
          <>
            {["Project Manager", "Admin"].includes(session.user.role) ? (
              <button type="button" className="button button-secondary" onClick={() => void runScan()} disabled={scanBusy}>
                {scanBusy ? "Scanning…" : "Run deadline scan"}
              </button>
            ) : null}
            <button type="button" className="button button-secondary" onClick={() => void loadItems()}>
              Refresh
            </button>
          </>
        }
      />

      <section className="stat-grid" aria-label="Action item summary">
        <Stat label="Open" value={openItems.length} detail="Open + in progress" />
        <Stat label="Overdue" value={overdue.length} detail={overdue.length ? "Owner and PM alerted" : "Nothing late"} tone={overdue.length ? "danger" : undefined} />
        <Stat label="Done" value={done.length} detail="Completed work" />
        <Stat label="Total" value={items.length} detail="Approved action items" />
      </section>

      <div className="filter-bar">
        <label className="inline-field">
          <span>Status</span>
          <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
            <option value="open_all">Open & in progress</option>
            <option value="open">Open</option>
            <option value="in_progress">In progress</option>
            <option value="done">Done</option>
            <option value="cancelled">Cancelled</option>
            <option value="all">All</option>
          </select>
        </label>
        <span className="hint">
          {session.user.role === "Team Member"
            ? "The API enforces that you only see or update your own items."
            : "Deadline alerts are deduplicated per item, recipient and day."}
        </span>
      </div>

      {loading ? <LoadingBlock label="Loading action items…" /> : null}
      {error ? <ErrorBlock message={error} onRetry={() => void loadItems()} /> : null}
      {!loading && !error && filtered.length === 0 ? (
        <EmptyState title="No action items match">
          Approve an action-item draft in the review queue. Overdue alerts appear in the notification drawer.
        </EmptyState>
      ) : null}

      <ul className="action-list">
        {filtered.map((item) => {
          const late = isOverdue(item.due_date, item.status);
          return (
            <li key={item.id} className={`action-card${late ? " action-late" : ""}`}>
              <div className="action-main">
                <div className="action-title-row">
                  <h3>{item.description}</h3>
                  <div className="inline-actions">
                    <Badge tone={item.priority === "High" ? "warning" : "neutral"}>{item.priority}</Badge>
                    <Badge tone={late ? "danger" : item.status === "done" ? "success" : item.status === "in_progress" ? "info" : "neutral"}>
                      {late ? "Overdue" : titleCase(item.status)}
                    </Badge>
                  </div>
                </div>
                <div className="action-meta">
                  <span>Owner: {item.owner_display || "Unassigned"}</span>
                  <span className={late ? "text-danger" : ""}>Due: {formatDate(item.due_date)}</span>
                  {item.jira_issue_key ? <span>Jira: {item.jira_issue_key}</span> : null}
                  {item.meeting_id ? <span>From: {item.meeting_id}</span> : null}
                </div>
                {item.source_quote ? (
                  <details className="source-details">
                    <summary>Transcript evidence</summary>
                    <blockquote>{item.source_quote}</blockquote>
                  </details>
                ) : null}
              </div>
              {canUpdate ? (
                <div className="action-controls">
                  {item.status === "open" ? (
                    <button type="button" className="button button-small button-secondary" disabled={busyId === item.id} onClick={() => void updateStatus(item, "in_progress")}>
                      Start
                    </button>
                  ) : null}
                  {item.status !== "done" && item.status !== "cancelled" ? (
                    <button type="button" className="button button-small button-primary" disabled={busyId === item.id} onClick={() => void updateStatus(item, "done")}>
                      Mark done
                    </button>
                  ) : null}
                  {item.status !== "cancelled" && item.status !== "done" ? (
                    <button type="button" className="button button-small button-quiet" disabled={busyId === item.id} onClick={() => void updateStatus(item, "cancelled")}>
                      Cancel
                    </button>
                  ) : null}
                  {item.status === "done" || item.status === "cancelled" ? (
                    <button type="button" className="button button-small button-secondary" disabled={busyId === item.id} onClick={() => void updateStatus(item, "open")}>
                      Reopen
                    </button>
                  ) : null}
                </div>
              ) : (
                <Badge tone="neutral">Read only</Badge>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
