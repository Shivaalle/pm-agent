import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type { Capabilities, CodeActivity, Forecast, Metrics, Project, Session } from "../types";
import { useToast } from "../toast";
import { formatDate, formatMoney, formatSeconds, healthLabel, relativeTime } from "../utils";
import { Badge, EmptyState, ErrorBlock, LoadingBlock, SectionHeader, Stat } from "./ui";

function valueOf(row: Record<string, unknown>, key: string): number {
  const value = row[key];
  return typeof value === "number" ? value : Number(value ?? 0) || 0;
}

function textOf(row: Record<string, unknown>, key: string): string {
  const value = row[key];
  return typeof value === "string" ? value : String(value ?? "");
}

export function MetricsView({ project }: { session: Session; project: Project | null }) {
  const toast = useToast();
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [forecastHistory, setForecastHistory] = useState<Forecast[]>([]);
  const [activity, setActivity] = useState<CodeActivity[]>([]);
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [syncing, setSyncing] = useState(false);

  const projectKey = project?.project_key ?? "";

  async function load() {
    if (!projectKey) return;
    setLoading(true);
    setError("");
    try {
      const [metricData, history, code] = await Promise.all([
        api.metrics(projectKey),
        api.forecastHistory(projectKey),
        api.codeActivity(projectKey),
      ]);
      setMetrics(metricData);
      setForecastHistory(history);
      setActivity(code);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load metrics.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
    api.capabilities().then(setCapabilities).catch(() => setCapabilities(null));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectKey]);

  async function syncCode() {
    setSyncing(true);
    try {
      const result = await api.syncCode(projectKey);
      toast.success(`Synced ${result.synced} code events · ${result.mismatches} mismatch${result.mismatches === 1 ? "" : "es"} flagged.`);
      await load();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "GitHub sync failed.");
    } finally {
      setSyncing(false);
    }
  }

  if (loading && !metrics) return <LoadingBlock label="Loading measured metrics…" />;
  if (error) return <ErrorBlock message={error} onRetry={() => void load()} />;
  if (!metrics) return <EmptyState title="No metrics yet">Approve drafts and record action-item progress to populate metrics.</EmptyState>;

  const maxModelCost = Math.max(...metrics.cost.by_model.map((row) => valueOf(row, "cost_usd")), 0.0001);
  const slaPercent = Math.min(100, Math.max(0, metrics.sla.sla_compliance_pct));
  const totalDrafts = metrics.quality.drafts_total || 1;

  return (
    <div className="view-stack">
      <SectionHeader
        eyebrow={`${projectKey} · measured, not estimated`}
        title="Metrics"
        description="Real LLM cost, meeting-to-draft SLA, approval quality and code-vs-conversation consistency. Everything is recorded from actual runs."
        actions={
          <>
            <button type="button" className="button button-secondary" onClick={() => void syncCode()} disabled={syncing}>
              {syncing ? "Syncing GitHub…" : "Sync GitHub"}
            </button>
            <button type="button" className="button button-secondary" onClick={() => void load()}>
              Refresh
            </button>
          </>
        }
      />

      <section className="stat-grid" aria-label="Key metrics">
        <Stat label="AI cost" value={formatMoney(metrics.cost.total_cost_usd)} detail={`${metrics.cost.calls} calls · ${metrics.cost.total_tokens.toLocaleString()} tokens`} />
        <Stat label="Cost per meeting" value={formatMoney(metrics.cost.avg_cost_per_meeting_usd)} detail={`Average across ${metrics.sla.total_meetings} meeting(s)`} />
        <Stat
          label="SLA compliance"
          value={`${slaPercent.toFixed(0)}%`}
          detail={`${metrics.sla.within_sla}/${metrics.sla.total_meetings} within ${capabilities?.sla_seconds ?? 300}s`}
          tone={metrics.sla.total_meetings && slaPercent < 90 ? "warning" : "success"}
        />
        <Stat label="p95 latency" value={formatSeconds(metrics.sla.p95_duration_seconds)} detail={`Last run ${formatSeconds(metrics.sla.last_duration_seconds)}`} />
      </section>

      <div className="content-grid">
        <section className="panel panel-span-2" aria-labelledby="cost-title">
          <div className="panel-heading">
            <div>
              <h2 id="cost-title">Visible AI cost</h2>
              <p>Provider-reported tokens × configured price. The model is replaceable; cost follows the actual provider.</p>
            </div>
            <Badge tone="info">{capabilities?.ai.provider ?? "AI"} · {capabilities?.ai.model ?? "model"}</Badge>
          </div>
          {metrics.cost.by_model.length === 0 ? (
            <EmptyState title="No LLM calls recorded">Run a meeting through the agent to see token and cost accounting.</EmptyState>
          ) : (
            <div className="stack">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Provider / model</th>
                    <th>Calls</th>
                    <th>Tokens</th>
                    <th>Cost</th>
                  </tr>
                </thead>
                <tbody>
                  {metrics.cost.by_model.map((row) => {
                    const cost = valueOf(row, "cost_usd");
                    return (
                      <tr key={`${textOf(row, "provider")}-${textOf(row, "model")}`}>
                        <td>
                          <strong>{textOf(row, "model")}</strong>
                          <span className="cell-sub">{textOf(row, "provider")}</span>
                        </td>
                        <td>{valueOf(row, "calls")}</td>
                        <td>{valueOf(row, "total_tokens").toLocaleString()}</td>
                        <td>
                          <div className="bar-cell">
                            <span>{formatMoney(cost)}</span>
                            <span className="bar-track">
                              <span className="bar-fill" style={{ width: `${(cost / maxModelCost) * 100}%` }} />
                            </span>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              {metrics.cost.by_meeting.length ? (
                <div>
                  <h3 className="subheading">Most expensive meetings</h3>
                  <ul className="rank-list">
                    {metrics.cost.by_meeting.slice(0, 5).map((row) => (
                      <li key={`${row.project_key}-${row.meeting_id}`}>
                        <span>{row.meeting_title || row.meeting_id}</span>
                        <strong>{formatMoney(row.cost_usd)}</strong>
                        <small>{row.total_tokens.toLocaleString()} tokens</small>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </div>
          )}
        </section>

        <section className="panel" aria-labelledby="sla-title">
          <div className="panel-heading">
            <div>
              <h2 id="sla-title">5-minute SLA</h2>
              <p>Meeting ingestion to grounded drafts.</p>
            </div>
          </div>
          <div className="gauge" role="img" aria-label={`SLA compliance ${slaPercent.toFixed(0)} percent`}>
            <div className="gauge-track">
              <div className={`gauge-fill${slaPercent < 90 ? " gauge-warn" : ""}`} style={{ width: `${slaPercent}%` }} />
            </div>
            <strong>{slaPercent.toFixed(0)}%</strong>
          </div>
          <dl className="fact-list">
            <div>
              <dt>Average</dt>
              <dd>{formatSeconds(metrics.sla.avg_duration_seconds)}</dd>
            </div>
            <div>
              <dt>p95</dt>
              <dd>{formatSeconds(metrics.sla.p95_duration_seconds)}</dd>
            </div>
            <div>
              <dt>Last</dt>
              <dd>{formatSeconds(metrics.sla.last_duration_seconds)}</dd>
            </div>
            <div>
              <dt>Target</dt>
              <dd>{capabilities?.sla_seconds ?? 300}s</dd>
            </div>
          </dl>
        </section>

        <section className="panel" aria-labelledby="quality-title">
          <div className="panel-heading">
            <div>
              <h2 id="quality-title">Approval quality</h2>
              <p>Human behaviour and scope signal.</p>
            </div>
          </div>
          <dl className="fact-list">
            <div>
              <dt>Drafts</dt>
              <dd>{metrics.quality.drafts_total}</dd>
            </div>
            <div>
              <dt>Waiting</dt>
              <dd>{metrics.quality.drafts_waiting}</dd>
            </div>
            <div>
              <dt>Approved</dt>
              <dd>{metrics.quality.drafts_approved}</dd>
            </div>
            <div>
              <dt>Edited</dt>
              <dd>{metrics.quality.drafts_edited}</dd>
            </div>
            <div>
              <dt>Rejected</dt>
              <dd>{metrics.quality.drafts_rejected}</dd>
            </div>
            <div>
              <dt>Approved without edit</dt>
              <dd>{metrics.quality.approved_without_edit_pct.toFixed(1)}%</dd>
            </div>
            <div>
              <dt>Scope changes</dt>
              <dd>{metrics.quality.scope_changes}</dd>
            </div>
            <div>
              <dt>Discrepancies</dt>
              <dd>{metrics.quality.discrepancies}</dd>
            </div>
          </dl>
          <div className="funnel" aria-label="Draft approval funnel">
            <span style={{ width: `${(metrics.quality.drafts_waiting / totalDrafts) * 100}%` }} className="funnel-waiting" title="Waiting" />
            <span style={{ width: `${(metrics.quality.drafts_approved / totalDrafts) * 100}%` }} className="funnel-approved" title="Approved" />
            <span style={{ width: `${(metrics.quality.drafts_rejected / totalDrafts) * 100}%` }} className="funnel-rejected" title="Rejected" />
          </div>
        </section>

        <section className="panel panel-span-2" aria-labelledby="forecast-history-title">
          <div className="panel-heading">
            <div>
              <h2 id="forecast-history-title">Forecast snapshots</h2>
              <p>Derived from action-item state, never from a static plan.</p>
            </div>
          </div>
          {forecastHistory.length === 0 ? (
            <EmptyState title="No snapshots yet">The daily forecast is written after an approval or alert scan.</EmptyState>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Health</th>
                  <th>Open</th>
                  <th>Overdue</th>
                  <th>Done</th>
                </tr>
              </thead>
              <tbody>
                {forecastHistory.map((item) => (
                  <tr key={item.date}>
                    <td>{formatDate(item.date)}</td>
                    <td>
                      <Badge tone={item.health === "off_track" ? "danger" : item.health === "at_risk" ? "warning" : "success"}>
                        {healthLabel(item.health)}
                      </Badge>
                    </td>
                    <td>{item.open_items}</td>
                    <td className={item.overdue_items ? "text-danger" : ""}>{item.overdue_items}</td>
                    <td>{item.completed_items}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>

        <section className="panel panel-span-2" aria-labelledby="code-activity-title">
          <div className="panel-heading">
            <div>
              <h2 id="code-activity-title">Code activity links</h2>
              <p>GitHub PRs, commits and issues mapped to Jira keys and approved action items.</p>
            </div>
            <Badge tone="neutral">{capabilities?.github.repo ?? "repo"}</Badge>
          </div>
          {activity.length === 0 ? (
            <EmptyState title="No code activity synced">Use “Sync GitHub” to read live PRs, commits and issues.</EmptyState>
          ) : (
            <ul className="code-list">
              {activity.map((item) => (
                <li key={`${item.kind}-${item.external_id}`} className={item.mismatch ? "code-mismatch" : ""}>
                  <Badge tone="neutral">{item.kind.toUpperCase()}</Badge>
                  <div>
                    <strong>{item.title}</strong>
                    <span>
                      {item.external_id} · {item.linked_jira_key || "unlinked"} · {item.state || "unknown"} ·{" "}
                      {relativeTime(item.activity_at)}
                    </span>
                    {item.mismatch_details ? <small className="text-danger">{item.mismatch_details}</small> : null}
                  </div>
                  <div className="inline-actions">
                    {item.url ? (
                      <a href={item.url} target="_blank" rel="noreferrer" className="text-button">
                        Open ↗
                      </a>
                    ) : null}
                    {item.mismatch ? <Badge tone="danger">Mismatch</Badge> : <Badge tone="success">Consistent</Badge>}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      <p className="hint">
        Cost uses provider-reported token usage and the price table in <code>backend/config/settings.yaml</code>. It is an
        estimate for visibility, not an invoice.
      </p>
    </div>
  );
}
