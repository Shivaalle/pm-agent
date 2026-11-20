import { useEffect, useMemo, useState } from "react";
import { api, ApiError } from "../api";
import type { ApproveResult, Draft, Project, Session } from "../types";
import { useToast } from "../toast";
import { draftTypeLabel, formatDateTime, titleCase } from "../utils";
import { Badge, Dialog, EmptyState, ErrorBlock, LoadingBlock, SectionHeader } from "./ui";

function draftTone(draft: Draft): "danger" | "warning" | "success" | "neutral" | "accent" {
  if (draft.discrepancy_detected) return "danger";
  if (draft.type === "scope_change") return "warning";
  if (draft.status === "approved") return "success";
  if (draft.status === "rejected") return "neutral";
  return "accent";
}

function syncTone(status: string): "success" | "warning" | "danger" | "neutral" {
  if (status === "synced") return "success";
  if (status === "partial") return "warning";
  if (status === "failed") return "danger";
  return "neutral";
}

export function ReviewView({
  session,
  project,
  onChanged,
}: {
  session: Session;
  project: Project | null;
  onChanged: () => void;
}) {
  const toast = useToast();
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [statusFilter, setStatusFilter] = useState("pending");
  const [typeFilter, setTypeFilter] = useState("");
  const [selected, setSelected] = useState<number[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [active, setActive] = useState<Draft | null>(null);
  const [editor, setEditor] = useState("");
  const [note, setNote] = useState("");
  const [editorError, setEditorError] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirmApprove, setConfirmApprove] = useState(false);
  const [rejectReason, setRejectReason] = useState("");
  const [showReject, setShowReject] = useState(false);
  const [batchConfirm, setBatchConfirm] = useState(false);
  const [lastResult, setLastResult] = useState<ApproveResult | null>(null);

  const projectKey = project?.project_key ?? "";

  async function loadDrafts() {
    if (!projectKey) return;
    setLoading(true);
    setError("");
    try {
      const all = await api.drafts({ project_key: projectKey });
      setDrafts(all);
      setSelected((current) => current.filter((id) => all.some((draft) => draft.id === id && ["waiting", "edited"].includes(draft.status))));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load the review queue.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadDrafts();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectKey]);

  useEffect(() => {
    if (!activeId) {
      setActive(null);
      return;
    }
    let cancelled = false;
    api
      .draft(activeId)
      .then((draft) => {
        if (cancelled) return;
        setActive(draft);
        setEditor(JSON.stringify(draft.proposed_change, null, 2));
        setEditorError("");
        setNote("");
        setConfirmApprove(false);
        setShowReject(false);
        setRejectReason("");
        setLastResult(null);
      })
      .catch((err: unknown) => {
        if (!cancelled) toast.error(err instanceof ApiError ? err.message : "Could not open the draft.");
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeId]);

  const filtered = useMemo(() => {
    return drafts
      .filter((draft) => {
        if (statusFilter === "pending") return draft.status === "waiting" || draft.status === "edited";
        if (statusFilter === "all") return true;
        return draft.status === statusFilter;
      })
      .filter((draft) => !typeFilter || draft.type === typeFilter)
      .sort((a, b) => b.id - a.id);
  }, [drafts, statusFilter, typeFilter]);

  const pending = drafts.filter((draft) => draft.status === "waiting" || draft.status === "edited");
  const selectable = filtered.filter((draft) => draft.status === "waiting" || draft.status === "edited");
  const allSelected = selectable.length > 0 && selectable.every((draft) => selected.includes(draft.id));

  function toggle(id: number) {
    setSelected((current) => (current.includes(id) ? current.filter((value) => value !== id) : [...current, id]));
  }

  async function refreshActive() {
    await loadDrafts();
    if (activeId) {
      try {
        const draft = await api.draft(activeId);
        setActive(draft);
      } catch {
        setActive(null);
        setActiveId(null);
      }
    }
    onChanged();
  }

  async function saveEdit() {
    if (!active) return;
    let parsed: Record<string, unknown>;
    try {
      const value = JSON.parse(editor) as unknown;
      if (!value || typeof value !== "object" || Array.isArray(value)) {
        setEditorError("The proposed change must be a JSON object.");
        return;
      }
      parsed = value as Record<string, unknown>;
    } catch {
      setEditorError("That is not valid JSON. Fix it before saving.");
      return;
    }
    setBusy(true);
    setEditorError("");
    try {
      await api.editDraft(active.id, parsed, note || undefined);
      toast.success("Draft edited. The change is recorded in the audit history.");
      await refreshActive();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Could not save the draft.");
    } finally {
      setBusy(false);
    }
  }

  async function approve() {
    if (!active) return;
    setBusy(true);
    try {
      const result = await api.approveDraft(active.id);
      setLastResult(result);
      setConfirmApprove(false);
      if (result.sync_status === "synced") {
        toast.success(`Approved and written to Jira${result.jira_issue_key ? ` as ${result.jira_issue_key}` : ""}.`);
      } else if (result.sync_status === "failed") {
        toast.error(`Approved, but Jira write failed: ${result.error ?? "unknown error"}. Retry from this dialog.`);
      } else {
        toast.info(`Approved with a partial Jira write: ${result.error ?? "check the sync status"}.`);
      }
      await refreshActive();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Approval failed.");
    } finally {
      setBusy(false);
    }
  }

  async function reject() {
    if (!active) return;
    setBusy(true);
    try {
      await api.rejectDraft(active.id, rejectReason || undefined);
      toast.success("Draft rejected. Nothing was written to Jira.");
      setShowReject(false);
      await refreshActive();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Could not reject the draft.");
    } finally {
      setBusy(false);
    }
  }

  async function retry() {
    if (!active) return;
    setBusy(true);
    try {
      const result = await api.retryDraft(active.id);
      setLastResult(result);
      if (result.sync_status === "synced") toast.success("Jira sync retried successfully.");
      else toast.error(result.error ?? "Retry failed again.");
      await refreshActive();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Retry failed.");
    } finally {
      setBusy(false);
    }
  }

  async function batchApprove() {
    setBusy(true);
    try {
      const result = await api.batchApprove(selected);
      setBatchConfirm(false);
      setSelected([]);
      if (result.failed) {
        toast.error(`${result.approved} approved, ${result.failed} failed. Open the drafts for details.`);
      } else {
        toast.success(`${result.approved} draft${result.approved === 1 ? "" : "s"} approved and written to Jira.`);
      }
      await loadDrafts();
      onChanged();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Batch approval failed.");
    } finally {
      setBusy(false);
    }
  }

  if (!["Project Manager", "Admin"].includes(session.user.role)) {
    return <EmptyState title="Review is limited to the Project Manager">Approvals are a human gate and require the PM role.</EmptyState>;
  }

  return (
    <div className="view-stack">
      <SectionHeader
        eyebrow={`${projectKey} · human approval gate`}
        title="Review queue"
        description="Nothing writes to Jira until you approve. Every draft cites exact transcript lines; edited and rejected items stay in the audit history."
        actions={
          <>
            {selected.length ? (
              <button type="button" className="button button-primary" onClick={() => setBatchConfirm(true)}>
                Approve selected ({selected.length})
              </button>
            ) : null}
            <button type="button" className="button button-secondary" onClick={() => void loadDrafts()}>
              Refresh
            </button>
          </>
        }
      />

      <div className="filter-bar">
        <div className="segmented" role="tablist" aria-label="Draft status">
          {[
            ["pending", `Pending (${pending.length})`],
            ["approved", "Approved"],
            ["rejected", "Rejected"],
            ["all", "All"],
          ].map(([value, label]) => (
            <button
              key={value}
              type="button"
              role="tab"
              aria-selected={statusFilter === value}
              className={`segment${statusFilter === value ? " segment-active" : ""}`}
              onClick={() => {
                setStatusFilter(value);
                setSelected([]);
              }}
            >
              {label}
            </button>
          ))}
        </div>
        <label className="inline-field">
          <span>Type</span>
          <select value={typeFilter} onChange={(event) => setTypeFilter(event.target.value)}>
            <option value="">All types</option>
            <option value="decision">Decision</option>
            <option value="action_item">Action item</option>
            <option value="raid_item">RAID</option>
            <option value="scope_change">Scope change</option>
            <option value="status_update">Status update</option>
          </select>
        </label>
        {selectable.length ? (
          <label className="checkbox-inline">
            <input
              type="checkbox"
              checked={allSelected}
              onChange={() => setSelected(allSelected ? [] : selectable.map((draft) => draft.id))}
            />
            <span>Select all pending</span>
          </label>
        ) : null}
      </div>

      {loading ? <LoadingBlock label="Loading drafts…" /> : null}
      {error ? <ErrorBlock message={error} onRetry={() => void loadDrafts()} /> : null}
      {!loading && !error && filtered.length === 0 ? (
        <EmptyState title="Nothing here">
          Upload a meeting to generate grounded drafts, or choose a different filter.
        </EmptyState>
      ) : null}

      <ul className="draft-grid">
        {filtered.map((draft) => {
          const selectableDraft = draft.status === "waiting" || draft.status === "edited";
          return (
            <li key={draft.id} className={`draft-card${draft.discrepancy_detected ? " draft-card-alert" : ""}`}>
              <div className="draft-card-top">
                {selectableDraft ? (
                  <label className="checkbox-inline compact">
                    <input
                      type="checkbox"
                      checked={selected.includes(draft.id)}
                      onChange={() => toggle(draft.id)}
                      aria-label={`Select ${draft.title}`}
                    />
                  </label>
                ) : null}
                <Badge tone={draftTone(draft)}>{draftTypeLabel(draft.type)}</Badge>
                <span className="draft-id">#{draft.id}</span>
                <div className="spacer" />
                {draft.status !== "waiting" ? <Badge tone={draft.status === "approved" ? "success" : "neutral"}>{titleCase(draft.status)}</Badge> : null}
                {draft.status === "approved" ? (
                  <Badge tone={syncTone(draft.sync_status)}>Jira {draft.sync_status}</Badge>
                ) : null}
              </div>
              <h3>{draft.title}</h3>
              <p className="draft-meeting">{draft.meeting_title || draft.meeting_id}</p>
              <blockquote className="source-quote">
                <span>Source</span>
                {draft.source_quote}
              </blockquote>
              <div className="draft-meta">
                <span>Lines {draft.source_lines.join(", ") || "unverified"}</span>
                <span>{Math.round(draft.confidence * 100)}% confidence</span>
                {draft.jira_issue_key ? <span>{draft.jira_issue_key}</span> : null}
              </div>
              {draft.discrepancy_detected ? (
                <div className="callout callout-danger">
                  <strong>Code / conversation mismatch</strong>
                  <span>{draft.discrepancy_details}</span>
                </div>
              ) : null}
              <div className="draft-card-actions">
                <button type="button" className="button button-small button-secondary" onClick={() => setActiveId(draft.id)}>
                  Open
                </button>
                {selectableDraft ? (
                  <button
                    type="button"
                    className="button button-small button-primary"
                    onClick={() => {
                      setActiveId(draft.id);
                      setConfirmApprove(true);
                    }}
                  >
                    Review & approve
                  </button>
                ) : null}
                {draft.status === "approved" && ["failed", "partial"].includes(draft.sync_status) ? (
                  <button
                    type="button"
                    className="button button-small button-secondary"
                    onClick={async () => {
                      try {
                        const result = await api.retryDraft(draft.id);
                        toast.success(result.sync_status === "synced" ? "Jira sync retried." : `Retry: ${result.sync_status}`);
                        await loadDrafts();
                      } catch (err) {
                        toast.error(err instanceof ApiError ? err.message : "Retry failed.");
                      }
                    }}
                  >
                    Retry Jira
                  </button>
                ) : null}
              </div>
            </li>
          );
        })}
      </ul>

      <Dialog
        open={Boolean(activeId)}
        onClose={() => setActiveId(null)}
        title={active ? active.title : "Draft"}
        wide
        footer={
          active ? (
            <div className="dialog-actions">
              {active.status === "approved" && ["failed", "partial"].includes(active.sync_status) ? (
                <button type="button" className="button button-secondary" onClick={() => void retry()} disabled={busy}>
                  Retry Jira write
                </button>
              ) : null}
              {active.status === "waiting" || active.status === "edited" ? (
                <>
                  <button type="button" className="button button-quiet" onClick={() => setShowReject(true)} disabled={busy}>
                    Reject
                  </button>
                  <button type="button" className="button button-secondary" onClick={() => void saveEdit()} disabled={busy}>
                    Save edit
                  </button>
                  <button type="button" className="button button-primary" onClick={() => setConfirmApprove(true)} disabled={busy}>
                    Approve…
                  </button>
                </>
              ) : (
                <button type="button" className="button button-secondary" onClick={() => setActiveId(null)}>
                  Close
                </button>
              )}
            </div>
          ) : null
        }
      >
        {!active ? (
          <LoadingBlock label="Loading draft…" />
        ) : (
          <div className="stack">
            <div className="meta-row">
              <Badge tone={draftTone(active)}>{draftTypeLabel(active.type)}</Badge>
              <Badge tone={active.status === "approved" ? "success" : "neutral"}>{titleCase(active.status)}</Badge>
              {active.status === "approved" ? <Badge tone={syncTone(active.sync_status)}>Jira {active.sync_status}</Badge> : null}
              <span>{active.meeting_title || active.meeting_id}</span>
              <span>Updated {formatDateTime(active.updated_at)}</span>
            </div>

            <section className="quote-block" aria-label="Transcript evidence">
              <div className="quote-heading">
                <span>Exact transcript evidence</span>
                <Badge tone={active.source_lines.length ? "success" : "danger"}>
                  {active.source_lines.length ? `Lines ${active.source_lines.join(", ")}` : "No verified source"}
                </Badge>
              </div>
              <blockquote>{active.source_quote}</blockquote>
              {active.github_evidence ? <p className="hint">GitHub evidence: {active.github_evidence}</p> : null}
            </section>

            {active.discrepancy_detected ? (
              <div className="callout callout-danger">
                <strong>Discrepancy flagged by the agent</strong>
                <span>{active.discrepancy_details}</span>
              </div>
            ) : null}

            {active.status === "waiting" || active.status === "edited" ? (
              <>
                <label className="field">
                  <span>Proposed change (JSON)</span>
                  <textarea
                    rows={12}
                    value={editor}
                    onChange={(event) => setEditor(event.target.value)}
                    spellCheck={false}
                    className="code-editor"
                  />
                  {editorError ? <span className="form-error">{editorError}</span> : null}
                </label>
                <label className="field">
                  <span>Edit note (optional, retained in audit history)</span>
                  <input value={note} onChange={(event) => setNote(event.target.value)} placeholder="Clarified the owner" />
                </label>
              </>
            ) : (
              <section>
                <h3 className="subheading">Approved content</h3>
                <pre className="json-view">{JSON.stringify(active.proposed_change, null, 2)}</pre>
              </section>
            )}

            {lastResult ? (
              <div className={`callout ${lastResult.sync_status === "synced" ? "callout-success" : "callout-danger"}`}>
                <strong>
                  {lastResult.jira_issue_key ? `Jira ${lastResult.jira_issue_key}` : "Jira write not completed"}
                </strong>
                <span>{lastResult.error || `Sync status: ${lastResult.sync_status}`}</span>
                {lastResult.jira_url ? (
                  <a href={lastResult.jira_url} target="_blank" rel="noreferrer" className="text-button">
                    Open in Jira ↗
                  </a>
                ) : null}
              </div>
            ) : null}

            {confirmApprove ? (
              <div className="confirm-panel" role="group" aria-label="Confirm approval">
                <h3>Approve this draft?</h3>
                <p>
                  Approval is recorded first. Then the agent writes to Jira using idempotency key{" "}
                  <code>{active.idempotency_key ?? "generated"}</code>. If the write fails, the draft stays approved and
                  can be retried safely.
                </p>
                <div className="inline-actions">
                  <button type="button" className="button button-quiet" onClick={() => setConfirmApprove(false)}>
                    Cancel
                  </button>
                  <button type="button" className="button button-primary" onClick={() => void approve()} disabled={busy}>
                    {busy ? "Approving…" : "Confirm approval and write to Jira"}
                  </button>
                </div>
              </div>
            ) : null}

            {showReject ? (
              <div className="confirm-panel" role="group" aria-label="Confirm rejection">
                <h3>Reject this draft?</h3>
                <p>Nothing will be written to Jira. The rejection is retained in the audit history.</p>
                <label className="field">
                  <span>Reason (optional)</span>
                  <input value={rejectReason} onChange={(event) => setRejectReason(event.target.value)} />
                </label>
                <div className="inline-actions">
                  <button type="button" className="button button-quiet" onClick={() => setShowReject(false)}>
                    Cancel
                  </button>
                  <button type="button" className="button button-danger" onClick={() => void reject()} disabled={busy}>
                    Reject draft
                  </button>
                </div>
              </div>
            ) : null}
          </div>
        )}
      </Dialog>

      <Dialog
        open={batchConfirm}
        onClose={() => setBatchConfirm(false)}
        title={`Approve ${selected.length} draft${selected.length === 1 ? "" : "s"}?`}
        footer={
          <>
            <button type="button" className="button button-quiet" onClick={() => setBatchConfirm(false)}>
              Cancel
            </button>
            <button type="button" className="button button-primary" onClick={() => void batchApprove()} disabled={busy}>
              {busy ? "Approving…" : "Approve and write to Jira"}
            </button>
          </>
        }
      >
        <p>
          Each selected draft is recorded as approved before its Jira write. Failures remain approved and can be retried
          individually without creating duplicate issues.
        </p>
      </Dialog>
    </div>
  );
}
