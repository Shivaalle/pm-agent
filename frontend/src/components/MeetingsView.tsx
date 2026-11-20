import { useEffect, useState, type FormEvent } from "react";
import { api, ApiError } from "../api";
import type {
  Capabilities,
  Meeting,
  MeetingSummary,
  Project,
  Session,
  TeamsMeeting,
  UploadResult,
  ViewId,
} from "../types";
import { useToast } from "../toast";
import { formatDate, formatDateTime, titleCase } from "../utils";
import { Badge, Dialog, EmptyState, ErrorBlock, LoadingBlock, SectionHeader } from "./ui";

type Method = "paste" | "json" | "vtt" | "teams";

function todayIso() {
  const now = new Date();
  const offset = now.getTimezoneOffset();
  return new Date(now.getTime() - offset * 60_000).toISOString().slice(0, 10);
}

function statusTone(status: string): "success" | "danger" | "info" | "neutral" {
  if (status === "ready") return "success";
  if (status === "failed") return "danger";
  if (status === "processing") return "info";
  return "neutral";
}

export function MeetingsView({
  session,
  project,
  onNavigate,
  onRefreshNotifications,
  onDraftsAvailable,
}: {
  session: Session;
  project: Project | null;
  onNavigate: (view: ViewId) => void;
  onRefreshNotifications: () => void;
  onDraftsAvailable: () => void;
}) {
  const toast = useToast();
  const [meetings, setMeetings] = useState<MeetingSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [method, setMethod] = useState<Method>("paste");
  const [title, setTitle] = useState("");
  const [meetingDate, setMeetingDate] = useState(todayIso());
  const [attendees, setAttendees] = useState("");
  const [seriesId, setSeriesId] = useState("");
  const [isPrivate, setIsPrivate] = useState(false);
  const [paste, setPaste] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [lastResult, setLastResult] = useState<UploadResult | null>(null);
  const [detail, setDetail] = useState<Meeting | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [privateTarget, setPrivateTarget] = useState<MeetingSummary | null>(null);
  const [privateReason, setPrivateReason] = useState("");
  const [teams, setTeams] = useState<TeamsMeeting[] | null>(null);
  const [teamsBusy, setTeamsBusy] = useState(false);
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [reprocessing, setReprocessing] = useState<string | null>(null);

  const projectKey = project?.project_key ?? "";
  const canUpload = ["Project Manager", "Admin"].includes(session.user.role);

  async function loadMeetings() {
    if (!projectKey) return;
    setLoading(true);
    setError("");
    try {
      setMeetings(await api.meetings(projectKey));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load meetings.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadMeetings();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectKey]);

  useEffect(() => {
    if (!session) return;
    api.capabilities().then(setCapabilities).catch(() => setCapabilities(null));
  }, [session]);

  async function loadSample() {
    try {
      const sample = await api.sampleTranscript();
      setPaste(JSON.stringify(sample, null, 2));
      const sampleTitle = typeof sample.title === "string" ? sample.title : "";
      const sampleDate = typeof sample.meeting_date === "string" ? sample.meeting_date : "";
      const sampleAttendees = Array.isArray(sample.attendees) ? sample.attendees.join(", ") : "";
      if (sampleTitle) setTitle(sampleTitle);
      if (sampleDate) setMeetingDate(sampleDate);
      if (sampleAttendees) setAttendees(sampleAttendees);
      toast.info("Sample transcript loaded. You can edit it before extracting.");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Could not load the sample transcript.");
    }
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!projectKey) return;
    setBusy(true);
    setLastResult(null);
    try {
      let result: UploadResult;
      if (method === "json") {
        if (!file) throw new ApiError(400, "Choose a JSON transcript file first.");
        result = await api.uploadFile(file, projectKey, isPrivate, seriesId || undefined);
      } else if (method === "vtt") {
        if (!file) throw new ApiError(400, "Choose a WebVTT transcript file first.");
        if (!title.trim()) throw new ApiError(400, "Give the VTT meeting a title.");
        result = await api.uploadVtt(file, {
          title: title.trim(),
          meeting_date: meetingDate,
          attendees,
          project_key: projectKey,
          is_private: isPrivate,
          series_id: seriesId || undefined,
        });
      } else {
        if (!paste.trim()) throw new ApiError(400, "Paste transcript JSON first.");
        let data: Record<string, unknown>;
        try {
          data = JSON.parse(paste) as Record<string, unknown>;
        } catch {
          throw new ApiError(400, "Transcript JSON is not valid JSON.");
        }
        result = await api.importJson(data, projectKey, isPrivate, seriesId || undefined);
      }
      setLastResult(result);
      if (result.status === "success") {
        toast.success(
          `${result.total_drafts_generated} grounded draft${result.total_drafts_generated === 1 ? "" : "s"} created in ${result.duration_seconds ?? 0}s.`,
        );
      } else if (result.status === "skipped") {
        toast.info(result.message ?? "Meeting skipped.");
      } else {
        toast.error(result.message ?? "Extraction failed.");
      }
      await loadMeetings();
      onRefreshNotifications();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Upload failed.");
    } finally {
      setBusy(false);
    }
  }

  async function openDetail(meeting: MeetingSummary) {
    setDetailLoading(true);
    try {
      setDetail(await api.meeting(meeting.id));
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Could not load the meeting.");
    } finally {
      setDetailLoading(false);
    }
  }

  async function reprocess(meeting: MeetingSummary) {
    setReprocessing(meeting.id);
    try {
      const result = await api.reprocess(meeting.id);
      if (result.status === "success") toast.success(`Reprocessed · ${result.total_drafts_generated} drafts refreshed.`);
      else toast.error(result.message ?? "Reprocessing failed.");
      await loadMeetings();
      onRefreshNotifications();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Reprocessing failed.");
    } finally {
      setReprocessing(null);
    }
  }

  async function markPrivate() {
    if (!privateTarget) return;
    try {
      await api.markMeetingPrivate(privateTarget.id, privateReason);
      toast.success("Meeting purged. Its transcript, drafts and action items are gone.");
      setPrivateTarget(null);
      setPrivateReason("");
      await loadMeetings();
      onRefreshNotifications();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Could not mark the meeting private.");
    }
  }

  async function findTeams() {
    setTeamsBusy(true);
    try {
      setTeams(await api.teamsMeetings(projectKey));
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Teams connector failed.");
    } finally {
      setTeamsBusy(false);
    }
  }

  async function importTeams(meeting: TeamsMeeting) {
    setTeamsBusy(true);
    try {
      const result = await api.importTeamsMeeting(meeting.id, projectKey);
      if (result.status === "success") toast.success(`Imported ${meeting.subject} · ${result.total_drafts_generated} drafts.`);
      else toast.info(result.message ?? "Meeting skipped.");
      setTeams(null);
      await loadMeetings();
      onRefreshNotifications();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Teams import failed.");
    } finally {
      setTeamsBusy(false);
    }
  }

  if (!canUpload) {
    return <EmptyState title="Meetings are managed by the PM">Ask your Project Manager to upload the transcript.</EmptyState>;
  }

  return (
    <div className="view-stack">
      <SectionHeader
        eyebrow={`${projectKey} · ingestion`}
        title="Meetings"
        description="Choose one documented ingestion path: paste Granola/Wispr JSON, upload a transcript file, upload Teams WebVTT, or import from Microsoft Graph when configured."
        actions={
          <button type="button" className="button button-secondary" onClick={() => void loadMeetings()}>
            Refresh
          </button>
        }
      />

      <div className="content-grid meetings-grid">
        <section className="panel" aria-labelledby="ingest-title">
          <div className="panel-heading">
            <div>
              <h2 id="ingest-title">Import a transcript</h2>
              <p>The LLM only sees numbered transcript lines. Private meetings are rejected before storage.</p>
            </div>
          </div>

          <div className="segmented" role="tablist" aria-label="Import method">
            {(
              [
                ["paste", "Paste JSON"],
                ["json", "JSON file"],
                ["vtt", "WebVTT"],
                ["teams", "Teams"],
              ] as Array<[Method, string]>
            ).map(([value, label]) => (
              <button
                key={value}
                type="button"
                role="tab"
                aria-selected={method === value}
                className={`segment${method === value ? " segment-active" : ""}`}
                onClick={() => {
                  setMethod(value);
                  setFile(null);
                  setLastResult(null);
                }}
              >
                {label}
              </button>
            ))}
          </div>

          {method === "teams" ? (
            <div className="stack">
              <div className={`callout ${capabilities?.teams.mode === "live" ? "callout-info" : "callout-warning"}`}>
                <strong>
                  {capabilities?.teams.mode === "live"
                    ? "Microsoft Graph is connected."
                    : "Microsoft Graph is not connected."}
                </strong>
                <span>
                  {capabilities?.teams.mode === "live"
                    ? "List meetings and import the transcript for this project."
                    : "Upload JSON/WebVTT or connect Teams. The preview below uses documented demo fixtures and is labelled as such."}
                </span>
              </div>
              <button type="button" className="button button-primary" onClick={() => void findTeams()} disabled={teamsBusy}>
                {teamsBusy ? "Checking Teams…" : capabilities?.teams.mode === "live" ? "Find Teams meetings" : "Preview Teams demo fixtures"}
              </button>
              {teams ? (
                <ul className="teams-list">
                  {teams.map((meeting) => (
                    <li key={meeting.id}>
                      <div>
                        <strong>{meeting.subject}</strong>
                        <span>
                          {formatDateTime(meeting.start_time)} · {meeting.attendees.length} attendees
                          {meeting.series_id ? " · recurring" : ""}
                        </span>
                      </div>
                      <div className="inline-actions">
                        <Badge tone={meeting.mode === "live" ? "success" : "warning"}>{meeting.mode}</Badge>
                        {meeting.already_imported ? <Badge tone="neutral">Imported</Badge> : null}
                        <button
                          type="button"
                          className="button button-small button-secondary"
                          disabled={teamsBusy || meeting.is_private_series}
                          onClick={() => void importTeams(meeting)}
                          title={meeting.is_private_series ? "This recurring series is marked private" : undefined}
                        >
                          Import
                        </button>
                      </div>
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          ) : (
            <form className="stack" onSubmit={(event) => void handleSubmit(event)}>
              {method === "paste" ? (
                <div className="field">
                  <div className="label-row">
                    <span>Transcript JSON</span>
                    <button type="button" className="text-button" onClick={() => void loadSample()}>
                      Load sample
                    </button>
                  </div>
                  <textarea
                    rows={9}
                    value={paste}
                    onChange={(event) => setPaste(event.target.value)}
                    placeholder='{"title":"Sprint sync","meeting_date":"2026-11-20","transcript":[{"line":1,"speaker":"Sarah","text":"…"}]}'
                    spellCheck={false}
                  />
                  <span className="field-hint">Accepts the documented shape and Granola/Wispr-style transcripts.</span>
                </div>
              ) : (
                <label className="field">
                  <span>{method === "vtt" ? "WebVTT transcript" : "JSON transcript"}</span>
                  <input
                    type="file"
                    accept={method === "vtt" ? ".vtt,text/vtt" : ".json,application/json"}
                    onChange={(event) => setFile(event.target.files?.[0] ?? null)}
                  />
                  <span className="field-hint">
                    {method === "vtt"
                      ? "Teams WebVTT keeps speaker names and timestamps."
                      : "Use the documented transcript JSON shape."}
                  </span>
                </label>
              )}

              <div className="form-grid">
                <label className="field">
                  <span>{method === "json" ? "Title override (optional)" : "Meeting title"}</span>
                  <input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Sprint 15 planning" />
                </label>
                <label className="field">
                  <span>Meeting date</span>
                  <input type="date" value={meetingDate} onChange={(event) => setMeetingDate(event.target.value)} />
                </label>
              </div>
              <label className="field">
                <span>Attendees (comma separated)</span>
                <input value={attendees} onChange={(event) => setAttendees(event.target.value)} placeholder="Sarah Chen, Alex Rivera" />
              </label>
              <div className="form-grid">
                <label className="field">
                  <span>Recurring series ID (optional)</span>
                  <input value={seriesId} onChange={(event) => setSeriesId(event.target.value)} placeholder="series-standup-daily" />
                </label>
                <label className="checkbox-field">
                  <input type="checkbox" checked={isPrivate} onChange={(event) => setIsPrivate(event.target.checked)} />
                  <span>
                    <strong>Private meeting</strong>
                    <small>Do not read, process or store. Applied before ingestion.</small>
                  </span>
                </label>
              </div>

              {lastResult ? (
                <div className={`callout ${lastResult.status === "success" ? "callout-success" : lastResult.status === "skipped" ? "callout-info" : "callout-danger"}`}>
                  <strong>
                    {lastResult.status === "success"
                      ? `${lastResult.total_drafts_generated} grounded drafts`
                      : titleCase(lastResult.status)}
                  </strong>
                  <span>
                    {lastResult.message ||
                      `Meeting ${lastResult.meeting_id} · ${lastResult.within_sla ? "within SLA" : "outside SLA"} · ${lastResult.duration_seconds ?? 0}s · ${lastResult.llm_model ?? ""}`}
                  </span>
                  {lastResult.total_drafts_generated ? (
                    <div className="inline-actions">
                      <button type="button" className="button button-small button-primary" onClick={onDraftsAvailable}>
                        Review drafts
                      </button>
                    </div>
                  ) : null}
                </div>
              ) : null}

              <button type="submit" className="button button-primary" disabled={busy}>
                {busy ? "Extracting grounded drafts…" : isPrivate ? "Skip private meeting" : "Upload and extract"}
              </button>
              <p className="hint">
                Live mode calls {capabilities?.ai.provider ?? "the configured AI provider"} and fails visibly. It never
                silently substitutes fake drafts.
              </p>
            </form>
          )}
        </section>

        <section className="panel" aria-labelledby="meeting-list-title">
          <div className="panel-heading">
            <div>
              <h2 id="meeting-list-title">Project meetings</h2>
              <p>Transcripts, status and source path.</p>
            </div>
          </div>
          {loading ? <LoadingBlock label="Loading meetings…" /> : null}
          {error ? <ErrorBlock message={error} onRetry={() => void loadMeetings()} /> : null}
          {!loading && !error && meetings.length === 0 ? (
            <EmptyState title="No meetings yet">Import a transcript to start the review loop.</EmptyState>
          ) : null}
          <ul className="meeting-cards">
            {meetings.map((meeting) => (
              <li key={meeting.id} className="meeting-card">
                <div className="meeting-card-head">
                  <div>
                    <strong>{meeting.title}</strong>
                    <span>
                      {formatDate(meeting.meeting_date)} · {meeting.source} · {meeting.draft_count} drafts
                    </span>
                  </div>
                  <Badge tone={statusTone(meeting.status)}>{titleCase(meeting.status)}</Badge>
                </div>
                {meeting.summary ? <p className="meeting-summary">{meeting.summary}</p> : null}
                <div className="meeting-card-meta">
                  <span>{meeting.attendees.join(", ") || "No attendees recorded"}</span>
                  {meeting.series_id ? <Badge tone="neutral">{meeting.series_id}</Badge> : null}
                </div>
                <div className="meeting-card-actions">
                  <button type="button" className="text-button" onClick={() => void openDetail(meeting)}>
                    Transcript
                  </button>
                  <button
                    type="button"
                    className="text-button"
                    onClick={() => void reprocess(meeting)}
                    disabled={reprocessing === meeting.id}
                  >
                    {reprocessing === meeting.id ? "Reprocessing…" : "Reprocess"}
                  </button>
                  <button type="button" className="text-button text-danger" onClick={() => setPrivateTarget(meeting)}>
                    Mark private
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </section>
      </div>

      <Dialog
        open={Boolean(detail) || detailLoading}
        onClose={() => setDetail(null)}
        title={detail?.title ?? "Meeting transcript"}
        wide
        footer={
          detail ? (
            <button type="button" className="button button-secondary" onClick={() => onNavigate("review")}>
              Open review queue
            </button>
          ) : null
        }
      >
        {detailLoading ? <LoadingBlock label="Loading transcript…" /> : null}
        {detail ? (
          <div className="stack">
            <div className="meta-row">
              <Badge tone="info">{detail.source}</Badge>
              <span>{formatDate(detail.meeting_date)}</span>
              <span>{detail.attendees.join(", ")}</span>
            </div>
            {detail.summary ? <p>{detail.summary}</p> : null}
            <ol className="transcript-list">
              {detail.transcript_lines.map((line) => (
                <li key={line.line} id={`line-${line.line}`}>
                  <span className="line-number">Line {line.line}</span>
                  <div>
                    <strong>{line.speaker}</strong>
                    <p>{line.text}</p>
                  </div>
                </li>
              ))}
            </ol>
          </div>
        ) : null}
      </Dialog>

      <Dialog
        open={Boolean(privateTarget)}
        onClose={() => setPrivateTarget(null)}
        title="Purge this meeting?"
        footer={
          <>
            <button type="button" className="button button-quiet" onClick={() => setPrivateTarget(null)}>
              Cancel
            </button>
            <button type="button" className="button button-danger" onClick={() => void markPrivate()}>
              Mark private and purge
            </button>
          </>
        }
      >
        <p>
          <strong>{privateTarget?.title}</strong> will be removed permanently: transcript lines, drafts, action items and
          notifications. If it belongs to a recurring series, that series is blocked from future ingestion.
        </p>
        <label className="field">
          <span>Reason (optional audit note)</span>
          <input value={privateReason} onChange={(event) => setPrivateReason(event.target.value)} placeholder="HR / legal sensitive" />
        </label>
      </Dialog>
    </div>
  );
}
