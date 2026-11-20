import { useEffect, useMemo, useState, type FormEvent } from "react";
import { api, ApiError } from "../api";
import type { IntegrationsResponse, PrivateSeries, Project, ProjectMember, Session, User } from "../types";
import { useToast } from "../toast";
import { formatDateTime, titleCase } from "../utils";
import { Badge, Dialog, EmptyState, ErrorBlock, LoadingBlock, SectionHeader } from "./ui";

const ROLES = ["Project Manager", "Team Member", "Sponsor", "Admin"];

function modeTone(mode: string): "success" | "warning" | "danger" | "neutral" {
  if (mode === "live") return "success";
  if (mode === "mock") return "warning";
  if (mode === "unconfigured") return "danger";
  return "neutral";
}

export function AdminView({ session, project }: { session: Session; project: Project | null }) {
  const toast = useToast();
  const [integrations, setIntegrations] = useState<IntegrationsResponse | null>(null);
  const [users, setUsers] = useState<User[]>([]);
  const [members, setMembers] = useState<ProjectMember[]>([]);
  const [series, setSeries] = useState<PrivateSeries[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [showSeries, setShowSeries] = useState(false);
  const [form, setForm] = useState({
    username: "",
    display_name: "",
    email: "",
    role: "Team Member",
    password: "",
  });
  const [seriesForm, setSeriesForm] = useState({ series_id: "", reason: "" });
  const [memberToAdd, setMemberToAdd] = useState("");

  const projectKey = project?.project_key ?? "";

  async function load() {
    if (!projectKey) return;
    setLoading(true);
    setError("");
    try {
      const [integrationData, userData, memberData, seriesData] = await Promise.all([
        api.integrations(projectKey),
        api.users(projectKey),
        api.members(projectKey),
        api.privateSeries(projectKey),
      ]);
      setIntegrations(integrationData);
      setUsers(userData);
      setMembers(memberData);
      setSeries(seriesData);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load admin data.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectKey]);

  const nonMembers = useMemo(
    () => users.filter((user) => !members.some((member) => member.user_id === user.id)),
    [users, members],
  );

  async function createUser(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      await api.createUser({
        username: form.username.trim(),
        display_name: form.display_name.trim() || undefined,
        email: form.email.trim() || undefined,
        role: form.role,
        password: form.password || undefined,
        project_key: projectKey,
      });
      toast.success(`Created ${form.username} and added them to ${projectKey}.`);
      setShowCreate(false);
      setForm({ username: "", display_name: "", email: "", role: "Team Member", password: "" });
      await load();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Could not create the user.");
    } finally {
      setBusy(false);
    }
  }

  async function addMember() {
    if (!memberToAdd) return;
    setBusy(true);
    try {
      await api.addMember(projectKey, Number(memberToAdd));
      toast.success("Member added to the project.");
      setMemberToAdd("");
      await load();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Could not add the member.");
    } finally {
      setBusy(false);
    }
  }

  async function removeMember(member: ProjectMember) {
    setBusy(true);
    try {
      await api.removeMember(projectKey, member.user_id);
      toast.success(`${member.display_name} removed from ${projectKey}.`);
      await load();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Could not remove the member.");
    } finally {
      setBusy(false);
    }
  }

  async function addSeries(event: FormEvent) {
    event.preventDefault();
    if (!seriesForm.series_id.trim()) return;
    setBusy(true);
    try {
      await api.addPrivateSeries(seriesForm.series_id.trim(), projectKey, seriesForm.reason || undefined);
      toast.success("Recurring series blocked from ingestion.");
      setShowSeries(false);
      setSeriesForm({ series_id: "", reason: "" });
      await load();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Could not mark the series private.");
    } finally {
      setBusy(false);
    }
  }

  async function removeSeries(item: PrivateSeries) {
    setBusy(true);
    try {
      await api.removePrivateSeries(item.series_id, projectKey);
      toast.success("Series is visible again.");
      await load();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Could not update the series.");
    } finally {
      setBusy(false);
    }
  }

  if (loading && !integrations) return <LoadingBlock label="Loading admin controls…" />;
  if (error) return <ErrorBlock message={error} onRetry={() => void load()} />;

  return (
    <div className="view-stack">
      <SectionHeader
        eyebrow={`${projectKey} · administration`}
        title="Admin"
        description="Connector truth, project membership and privacy controls. Live failures stay visible; they are never papered over with fake success."
        actions={
          <button type="button" className="button button-secondary" onClick={() => void load()}>
            Refresh
          </button>
        }
      />

      <section aria-labelledby="integration-title" className="view-stack">
        <div className="panel-heading">
          <div>
            <h2 id="integration-title">Integrations</h2>
            <p>Mode and connectivity for each external system.</p>
          </div>
        </div>
        {integrations ? (
          <div className="integration-grid">
            {[integrations.ai, integrations.jira, integrations.github, integrations.teams, integrations.database].map((item) => (
              <article key={item.key} className="integration-card">
                <div className="integration-head">
                  <h3>{item.name}</h3>
                  <Badge tone={modeTone(item.mode)}>{item.mode}</Badge>
                </div>
                <p>{item.detail}</p>
                <span className={`status-dot ${item.live ? "status-live" : "status-off"}`}>
                  {item.live ? "Connected" : item.configured ? "Configured, not reachable" : "Not configured"}
                </span>
              </article>
            ))}
          </div>
        ) : null}
      </section>

      <div className="content-grid">
        <section className="panel" aria-labelledby="members-title">
          <div className="panel-heading">
            <div>
              <h2 id="members-title">Project members</h2>
              <p>Access is enforced per project on every API route.</p>
            </div>
            <Badge tone="neutral">{members.length} members</Badge>
          </div>
          <ul className="member-list">
            {members.map((member) => (
              <li key={member.user_id}>
                <div>
                  <strong>{member.display_name}</strong>
                  <span>
                    @{member.username} · {member.role}
                  </span>
                </div>
                {member.user_id === session.user.id ? (
                  <Badge tone="info">You</Badge>
                ) : (
                  <button type="button" className="text-button text-danger" onClick={() => void removeMember(member)} disabled={busy}>
                    Remove
                  </button>
                )}
              </li>
            ))}
          </ul>
          <div className="inline-form">
            <label className="inline-field">
              <span>Add existing user</span>
              <select value={memberToAdd} onChange={(event) => setMemberToAdd(event.target.value)}>
                <option value="">Choose a user</option>
                {nonMembers.map((user) => (
                  <option key={user.id} value={user.id}>
                    {user.display_name} · {user.role}
                  </option>
                ))}
              </select>
            </label>
            <button type="button" className="button button-secondary" onClick={() => void addMember()} disabled={busy || !memberToAdd}>
              Add to project
            </button>
          </div>
        </section>

        <section className="panel" aria-labelledby="users-title">
          <div className="panel-heading">
            <div>
              <h2 id="users-title">Users</h2>
              <p>Create a role-scoped account.</p>
            </div>
            <button type="button" className="button button-small button-primary" onClick={() => setShowCreate(true)}>
              New user
            </button>
          </div>
          {users.length === 0 ? (
            <EmptyState title="No users">Create the first account.</EmptyState>
          ) : (
            <ul className="member-list">
              {users.map((user) => (
                <li key={user.id}>
                  <div>
                    <strong>{user.display_name}</strong>
                    <span>
                      @{user.username} · {user.role}
                    </span>
                  </div>
                  {members.some((member) => member.user_id === user.id) ? <Badge tone="success">Member</Badge> : <Badge tone="neutral">No access</Badge>}
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="panel panel-span-2" aria-labelledby="privacy-title">
          <div className="panel-heading">
            <div>
              <h2 id="privacy-title">Private recurring series</h2>
              <p>Future meetings in these series are skipped before storage. Existing meetings can be purged from the Meetings page.</p>
            </div>
            <button type="button" className="button button-small button-secondary" onClick={() => setShowSeries(true)}>
              Block a series
            </button>
          </div>
          {series.length === 0 ? (
            <EmptyState title="No private series">All recurring series are currently eligible for ingestion.</EmptyState>
          ) : (
            <ul className="member-list">
              {series.map((item) => (
                <li key={`${item.series_id}-${item.project_key}`}>
                  <div>
                    <strong>{item.series_id}</strong>
                    <span>
                      {item.reason || "No reason recorded"} · {item.created_by || "unknown"} · {formatDateTime(item.created_at)}
                    </span>
                  </div>
                  <button type="button" className="text-button" onClick={() => void removeSeries(item)} disabled={busy}>
                    Make visible
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="panel panel-span-2" aria-labelledby="scope-title">
          <div className="panel-heading">
            <div>
              <h2 id="scope-title">Agreed baseline scope</h2>
              <p>The agent compares meeting requests against this list before proposing a scope change.</p>
            </div>
          </div>
          <ul className="scope-list">
            {project?.agreed_scope.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </section>
      </div>

      <Dialog
        open={showCreate}
        onClose={() => setShowCreate(false)}
        title="Create a user"
        footer={
          <>
            <button type="button" className="button button-quiet" onClick={() => setShowCreate(false)}>
              Cancel
            </button>
            <button type="submit" form="create-user-form" className="button button-primary" disabled={busy}>
              {busy ? "Creating…" : "Create user"}
            </button>
          </>
        }
      >
        <form id="create-user-form" className="stack" onSubmit={(event) => void createUser(event)}>
          <div className="form-grid">
            <label className="field">
              <span>Username</span>
              <input value={form.username} onChange={(event) => setForm({ ...form, username: event.target.value })} required />
            </label>
            <label className="field">
              <span>Display name</span>
              <input value={form.display_name} onChange={(event) => setForm({ ...form, display_name: event.target.value })} />
            </label>
          </div>
          <label className="field">
            <span>Email</span>
            <input type="email" value={form.email} onChange={(event) => setForm({ ...form, email: event.target.value })} />
          </label>
          <div className="form-grid">
            <label className="field">
              <span>Role</span>
              <select value={form.role} onChange={(event) => setForm({ ...form, role: event.target.value })}>
                {ROLES.map((role) => (
                  <option key={role} value={role}>
                    {titleCase(role)}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Password (optional)</span>
              <input
                type="password"
                autoComplete="new-password"
                value={form.password}
                onChange={(event) => setForm({ ...form, password: event.target.value })}
                placeholder="Defaults to the demo password"
              />
            </label>
          </div>
          <p className="hint">The user is created and added to {projectKey}. Role and project access are enforced server-side.</p>
        </form>
      </Dialog>

      <Dialog
        open={showSeries}
        onClose={() => setShowSeries(false)}
        title="Block a recurring series"
        footer={
          <>
            <button type="button" className="button button-quiet" onClick={() => setShowSeries(false)}>
              Cancel
            </button>
            <button type="submit" form="series-form" className="button button-primary" disabled={busy || !seriesForm.series_id.trim()}>
              Block series
            </button>
          </>
        }
      >
        <form id="series-form" className="stack" onSubmit={(event) => void addSeries(event)}>
          <label className="field">
            <span>Series ID</span>
            <input
              value={seriesForm.series_id}
              onChange={(event) => setSeriesForm({ ...seriesForm, series_id: event.target.value })}
              placeholder="series-standup-daily"
              required
            />
          </label>
          <label className="field">
            <span>Reason (optional)</span>
            <input value={seriesForm.reason} onChange={(event) => setSeriesForm({ ...seriesForm, reason: event.target.value })} placeholder="Contains personnel matters" />
          </label>
          <p className="hint">This blocks new uploads and Teams imports for the series. Existing meetings must be marked private separately.</p>
        </form>
      </Dialog>
    </div>
  );
}
