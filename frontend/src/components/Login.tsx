import { useEffect, useState, type FormEvent } from "react";
import { api, ApiError } from "../api";
import type { DemoAccount, LoginResponse, Role } from "../types";
import { initials, titleCase } from "../utils";

const FALLBACK_ACCOUNTS: DemoAccount[] = [
  { username: "sarah", display_name: "Sarah Chen", role: "Project Manager" },
  { username: "alex", display_name: "Alex Rivera", role: "Team Member" },
  { username: "david", display_name: "David Park", role: "Team Member" },
  { username: "elena", display_name: "Elena Rossi", role: "Team Member" },
  { username: "mark", display_name: "Mark Johnson", role: "Sponsor" },
  { username: "admin", display_name: "Admin", role: "Admin" },
];

const ROLE_COPY: Record<Role, string> = {
  "Project Manager": "Review and approve grounded drafts, track delivery and alert the team.",
  "Team Member": "See your assigned actions and close them out without the noise.",
  Sponsor: "Read-only progress, forecast and quality signals.",
  Admin: "Connectors, users and privacy controls.",
};

export function Login({ onLogin }: { onLogin: (session: LoginResponse) => void }) {
  const [username, setUsername] = useState("sarah");
  const [password, setPassword] = useState("demo1234");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [accounts, setAccounts] = useState<DemoAccount[]>(FALLBACK_ACCOUNTS);
  const [demoPassword, setDemoPassword] = useState("demo1234");

  useEffect(() => {
    let cancelled = false;
    api
      .demoAccounts()
      .then((data) => {
        if (cancelled) return;
        setAccounts(data.accounts);
        setDemoPassword(data.password);
      })
      .catch(() => {
        /* fallback accounts keep the sign-in screen useful offline */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function signIn(nextUsername: string, nextPassword: string) {
    setBusy(true);
    setError("");
    try {
      const session = await api.login(nextUsername, nextPassword);
      onLogin(session);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Sign in failed");
    } finally {
      setBusy(false);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    void signIn(username.trim(), password);
  }

  return (
    <main className="login-page">
      <section className="login-story" aria-labelledby="login-title">
        <div className="brand-lockup">
          <span className="brand-mark" aria-hidden="true">
            PM
          </span>
          <span>AI Project Manager Agent</span>
        </div>
        <div className="story-copy">
          <span className="eyebrow eyebrow-inverse">Grounded, approved, auditable</span>
          <h1 id="login-title">Meetings become delivery — not busywork.</h1>
          <p>
            Every draft is tied to exact transcript lines. A human approves it. Only then does the
            agent write to Jira. Deadline alerts, cost and SLA stay visible the whole way.
          </p>
          <ul className="story-points">
            <li>
              <strong>Traceable</strong> — unsourced output is discarded, never guessed.
            </li>
            <li>
              <strong>Human-gated</strong> — approval is recorded before any Jira write.
            </li>
            <li>
              <strong>Replaceable AI</strong> — Gemini, an OpenAI-compatible server, or explicit mock.
            </li>
          </ul>
        </div>
        <p className="story-footnote">Local demo · seeded accounts · SQLite audit trail</p>
      </section>

      <section className="login-panel" aria-label="Sign in">
        <div className="login-card">
          <div className="login-heading">
            <span className="eyebrow">Welcome back</span>
            <h2>Sign in to the project room</h2>
            <p>Use a seeded demo account or your own credentials.</p>
          </div>

          <form onSubmit={submit} className="stack">
            <label className="field">
              <span>Username</span>
              <input
                autoComplete="username"
                spellCheck={false}
                autoCorrect="off"
                autoCapitalize="none"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                required
              />
            </label>
            <label className="field">
              <span>Password</span>
              <input
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                required
              />
            </label>
            {error ? (
              <p className="form-error" role="alert">
                {error}
              </p>
            ) : null}
            <button className="button button-primary button-full" type="submit" disabled={busy}>
              {busy ? "Signing in…" : "Sign in"}
            </button>
          </form>

          <div className="demo-accounts">
            <div className="demo-heading">
              <h3>Demo roles</h3>
              <span className="hint">password: {demoPassword}</span>
            </div>
            <ul className="account-grid">
              {accounts.map((account) => (
                <li key={account.username}>
                  <button
                    type="button"
                    className="account-button"
                    onClick={() => {
                      setUsername(account.username);
                      setPassword(demoPassword);
                      void signIn(account.username, demoPassword);
                    }}
                    disabled={busy}
                  >
                    <span className="avatar" aria-hidden="true">
                      {initials(account.display_name)}
                    </span>
                    <span>
                      <strong>{account.display_name}</strong>
                      <small>{titleCase(account.role)}</small>
                    </span>
                  </button>
                  <span className="account-copy">{ROLE_COPY[account.role]}</span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </section>
    </main>
  );
}
