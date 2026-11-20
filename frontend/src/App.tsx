import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError, setAuthToken, setUnauthorizedHandler } from "./api";
import type {
  Capabilities,
  LoginResponse,
  Notification,
  Role,
  Session,
  ViewId,
} from "./types";
import { formatDateTime, initials, relativeTime } from "./utils";
import { ToastProvider, useToast } from "./toast";
import { Login } from "./components/Login";
import { OverviewView } from "./components/OverviewView";
import { MeetingsView } from "./components/MeetingsView";
import { ReviewView } from "./components/ReviewView";
import { ActionItemsView } from "./components/ActionItemsView";
import { MetricsView } from "./components/MetricsView";
import { AdminView } from "./components/AdminView";

interface Tab {
  id: ViewId;
  label: string;
  roles: Role[];
}

const TABS: Tab[] = [
  { id: "overview", label: "Overview", roles: ["Project Manager", "Team Member", "Sponsor", "Admin"] },
  { id: "meetings", label: "Meetings", roles: ["Project Manager", "Admin"] },
  { id: "review", label: "Review queue", roles: ["Project Manager", "Admin"] },
  { id: "actions", label: "Action items", roles: ["Project Manager", "Team Member", "Sponsor", "Admin"] },
  { id: "metrics", label: "Metrics", roles: ["Project Manager", "Sponsor", "Admin"] },
  { id: "admin", label: "Admin", roles: ["Admin"] },
];

const DEFAULT_VIEW: Record<Role, ViewId> = {
  "Project Manager": "overview",
  "Team Member": "actions",
  Sponsor: "overview",
  Admin: "overview",
};

function modeTone(mode: string): "success" | "warning" | "neutral" {
  if (mode === "live") return "success";
  if (mode === "mock") return "warning";
  return "neutral";
}

function Shell() {
  const toast = useToast();
  const [session, setSession] = useState<Session | null>(null);
  const [checking, setChecking] = useState(true);
  const [projectKey, setProjectKey] = useState("");
  const [view, setView] = useState<ViewId>("overview");
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [notifOpen, setNotifOpen] = useState(false);
  const [waitingCount, setWaitingCount] = useState(0);

  const endSession = useCallback(() => {
    setAuthToken(null);
    localStorage.removeItem("pm_agent_token");
    setSession(null);
    setProjectKey("");
    setView("overview");
    setNotifications([]);
    setCapabilities(null);
    setNotifOpen(false);
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      endSession();
      toast.error("Your session expired. Sign in again.");
    });
    return () => setUnauthorizedHandler(null);
  }, [endSession, toast]);

  useEffect(() => {
    const token = localStorage.getItem("pm_agent_token");
    if (!token) {
      setChecking(false);
      return;
    }
    setAuthToken(token);
    api
      .me()
      .then((data) => {
        setSession({
          token,
          user: data.user,
          projects: data.projects,
          expires_at: "",
        });
      })
      .catch(() => {
        setAuthToken(null);
        localStorage.removeItem("pm_agent_token");
      })
      .finally(() => setChecking(false));
  }, []);

  const handleLogin = useCallback(async (result: LoginResponse) => {
    setAuthToken(result.token);
    localStorage.setItem("pm_agent_token", result.token);
    const me = await api.me().catch(() => null);
    setSession({
      token: result.token,
      user: me?.user ?? result.user,
      projects: me?.projects ?? [],
      expires_at: result.expires_at,
    });
  }, []);

  const project = useMemo(
    () => session?.projects.find((item) => item.project_key === projectKey) ?? null,
    [session, projectKey],
  );

  useEffect(() => {
    if (!session) return;
    if (session.projects.length === 0) {
      setProjectKey("");
      return;
    }
    if (!session.projects.some((item) => item.project_key === projectKey)) {
      setProjectKey(session.projects[0].project_key);
    }
  }, [session, projectKey]);

  useEffect(() => {
    if (!session) return;
    setView(DEFAULT_VIEW[session.user.role]);
  }, [session]);

  useEffect(() => {
    if (!session) return;
    api.capabilities().then(setCapabilities).catch(() => setCapabilities(null));
  }, [session]);

  const loadNotifications = useCallback(async () => {
    if (!session) return;
    try {
      setNotifications(await api.notifications());
    } catch {
      /* a transient notification failure should not break the page */
    }
  }, [session]);

  useEffect(() => {
    if (!session) return;
    void loadNotifications();
    const timer = window.setInterval(() => void loadNotifications(), 60_000);
    return () => window.clearInterval(timer);
  }, [session, loadNotifications]);

  useEffect(() => {
    if (!session || !projectKey) return;
    if (!["Project Manager", "Admin"].includes(session.user.role)) {
      setWaitingCount(0);
      return;
    }
    let cancelled = false;
    void Promise.all([
      api.drafts({ project_key: projectKey, status: "waiting" }),
      api.drafts({ project_key: projectKey, status: "edited" }),
    ])
      .then(([waiting, edited]) => {
        if (!cancelled) setWaitingCount(waiting.length + edited.length);
      })
      .catch(() => {
        if (!cancelled) setWaitingCount(0);
      });
    return () => {
      cancelled = true;
    };
  }, [session, projectKey, view]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setNotifOpen(false);
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  async function signOut() {
    try {
      await api.logout();
    } catch {
      /* local sign-out still succeeds */
    }
    endSession();
  }

  async function openNotification(notification: Notification) {
    if (!notification.read) {
      try {
        await api.readNotification(notification.id);
        setNotifications((current) =>
          current.map((item) => (item.id === notification.id ? { ...item, read: true } : item)),
        );
      } catch {
        /* keep it visibly unread */
      }
    }
    setNotifOpen(false);
    if (notification.action_item_id) setView("actions");
    else if (notification.draft_id && ["Project Manager", "Admin"].includes(session?.user.role ?? "")) {
      setView("review");
    }
  }

  async function markAllRead() {
    try {
      await api.readAllNotifications();
      setNotifications((current) => current.map((item) => ({ ...item, read: true })));
      toast.success("All notifications marked read.");
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : "Could not update notifications.");
    }
  }

  if (checking) {
    return (
      <div className="boot-screen" role="status">
        <span className="spinner-ring" aria-hidden="true" />
        <span>Restoring your session…</span>
      </div>
    );
  }

  if (!session) {
    return (
      <Login
        onLogin={(result) => {
          void handleLogin(result);
        }}
      />
    );
  }

  if (session.projects.length === 0) {
    return (
      <main className="boot-screen">
        <h1>No project access</h1>
        <p>Your account is signed in but is not a member of a project. Ask an Admin to add you.</p>
        <button className="button button-secondary" type="button" onClick={() => void signOut()}>
          Sign out
        </button>
      </main>
    );
  }

  const allowedTabs = TABS.filter((tab) => tab.roles.includes(session.user.role));
  const unread = notifications.filter((item) => !item.read).length;

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      <header className="topbar">
        <div className="topbar-brand">
          <span className="brand-mark brand-mark-small" aria-hidden="true">
            PM
          </span>
          <div>
            <strong>AI Project Manager</strong>
            <span className="brand-subtitle">Grounded delivery agent</span>
          </div>
        </div>

        <div className="topbar-center">
          <label className="project-picker">
            <span className="sr-only">Active project</span>
            <select
              value={projectKey}
              onChange={(event) => setProjectKey(event.target.value)}
              aria-label="Active project"
            >
              {session.projects.map((item) => (
                <option key={item.project_key} value={item.project_key}>
                  {item.name} · {item.project_key}
                </option>
              ))}
            </select>
          </label>
          {capabilities ? (
            <div className="mode-chips" aria-label="Integration modes">
              <span className={`mode-chip mode-${modeTone(capabilities.ai.mode)}`}>
                AI {capabilities.ai.mode}
                <span className="mode-detail"> · {capabilities.ai.model}</span>
              </span>
              <span className={`mode-chip mode-${modeTone(capabilities.jira.mode)}`}>
                Jira {capabilities.jira.mode}
              </span>
              <span className={`mode-chip mode-${modeTone(capabilities.github.mode)}`}>
                GitHub {capabilities.github.mode}
              </span>
              <span className="mode-chip mode-neutral">Teams {capabilities.teams.mode}</span>
            </div>
          ) : null}
        </div>

        <div className="topbar-actions">
          <div className="notif-wrap">
            <button
              type="button"
              className="icon-button bell-button"
              aria-label={`Notifications${unread ? `, ${unread} unread` : ""}`}
              aria-expanded={notifOpen}
              onClick={() => {
                setNotifOpen((open) => !open);
                if (!notifOpen) void loadNotifications();
              }}
            >
              <span aria-hidden="true">◔</span>
              {unread ? <span className="notif-count">{unread > 9 ? "9+" : unread}</span> : null}
            </button>
          </div>
          <div className="user-chip" title={session.user.email ?? session.user.username}>
            <span className="avatar avatar-small" aria-hidden="true">
              {initials(session.user.display_name)}
            </span>
            <span className="user-meta">
              <strong>{session.user.display_name}</strong>
              <small>{session.user.role}</small>
            </span>
          </div>
          <button type="button" className="button button-quiet" onClick={() => void signOut()}>
            Sign out
          </button>
        </div>
      </header>

      <nav className="tabbar" aria-label="Sections">
        {allowedTabs.map((tab) => (
          <button
            key={tab.id}
            type="button"
            className={`tab${view === tab.id ? " tab-active" : ""}`}
            aria-current={view === tab.id ? "page" : undefined}
            onClick={() => setView(tab.id)}
          >
            {tab.label}
            {tab.id === "review" && waitingCount ? (
              <span className="tab-count">{waitingCount}</span>
            ) : null}
          </button>
        ))}
      </nav>

      <main id="main-content" className="main-content">
        {view === "overview" ? (
          <OverviewView
            session={session}
            project={project}
            onNavigate={setView}
            onRefreshNotifications={() => void loadNotifications()}
          />
        ) : null}
        {view === "meetings" ? (
          <MeetingsView
            session={session}
            project={project}
            onNavigate={setView}
            onRefreshNotifications={() => void loadNotifications()}
            onDraftsAvailable={() => setView("review")}
          />
        ) : null}
        {view === "review" ? (
          <ReviewView
            session={session}
            project={project}
            onChanged={() => {
              void loadNotifications();
              setWaitingCount((count) => Math.max(0, count - 1));
            }}
          />
        ) : null}
        {view === "actions" ? (
          <ActionItemsView session={session} project={project} onRefreshNotifications={() => void loadNotifications()} />
        ) : null}
        {view === "metrics" ? <MetricsView session={session} project={project} /> : null}
        {view === "admin" ? <AdminView session={session} project={project} /> : null}
      </main>

      {notifOpen ? (
        <div className="popover-layer" onClick={() => setNotifOpen(false)}>
          <section
            className="notif-panel"
            role="dialog"
            aria-label="Notifications"
            onClick={(event) => event.stopPropagation()}
          >
            <header className="notif-header">
              <div>
                <h2>Notifications</h2>
                <p>{unread ? `${unread} unread` : "You're all caught up"}</p>
              </div>
              <div className="notif-header-actions">
                {unread ? (
                  <button type="button" className="text-button" onClick={() => void markAllRead()}>
                    Mark all read
                  </button>
                ) : null}
                <button
                  type="button"
                  className="icon-button"
                  aria-label="Close notifications"
                  onClick={() => setNotifOpen(false)}
                >
                  ×
                </button>
              </div>
            </header>
            <ul className="notif-list">
              {notifications.length === 0 ? (
                <li className="notif-empty">Deadline alerts and approvals will appear here.</li>
              ) : null}
              {notifications.slice(0, 20).map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    className={`notif-item${item.read ? "" : " notif-unread"}`}
                    onClick={() => void openNotification(item)}
                  >
                    <span className={`notif-dot notif-${item.type}`} aria-hidden="true" />
                    <span className="notif-copy">
                      <strong>{item.message}</strong>
                      <small>
                        {relativeTime(item.created_at)} · {formatDateTime(item.created_at)}
                      </small>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        </div>
      ) : null}
    </div>
  );
}

export default function App() {
  return (
    <ToastProvider>
      <Shell />
    </ToastProvider>
  );
}
