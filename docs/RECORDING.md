# One-minute demo recording guide

Goal: a single continuous screen recording, **≤ 60 seconds**, showing a meeting arriving →
grounded drafts → PM approval → an approved update appearing in Jira. Upload the finished file
to a link anyone can open without signing in (Google Drive "anyone with the link", Loom, or
YouTube unlisted/public) and put that link in your submission.

## Before you press record (2 minutes)

1. `./scripts/run.sh` — app at **http://localhost:8000/app/**.
2. Confirm live chips in the top bar read `AI live`, `Jira live`, `GitHub live`.
3. Confirm project targets: **Admin → Project targets** = Jira `KAN`, GitHub `Shivaalle/pm-agent`.
4. Browser: open the app, sign in as **sarah / demo1234**, and leave the **Review queue** tab
   open with pending drafts. Maximize the window and hide bookmarks.
5. Keep Jira open in a second tab, logged in, on the `KAN` board — save it as the last shot.

## Shot list (total ~55 s)

| Time | Action on screen | What the viewer sees |
|------|------------------|----------------------|
| 0–5 s | Overview tab. Point at project pulse and `AI live / Jira live / GitHub live` chips. | Live integrations, not a mock. |
| 5–12 s | **Meetings** tab → **Paste JSON** → **Load sample** → **Upload and extract**. | A real transcript arriving with speakers and line numbers. |
| 12–20 s | Result toast: "N grounded drafts created". Click **Review drafts**. | Drafts were generated within the 5-min SLA. |
| 20–30 s | Open a draft card. Zoom in on the **verbatim quote + "Lines x, y"** badge and the red **Code mismatch** callout on a status card. | Traceability and the GitHub reality check. |
| 30–40 s | **Approve… → Confirm approval and write to Jira**. Show the "Approved and written to Jira as KAN-…" toast. | Human approval gate, then live write. |
| 40–48 s | **Metrics** tab: cost, SLA %, approval quality. | Measured, not guessed. |
| 48–55 s | Switch to the Jira tab and show the new/updated issue (KAN-4…); refresh if needed. | The approved change is really in Jira. |

## If you need a clean slate

- Pending drafts were consumed? Upload a transcript again (Meetings → Load sample → Upload).
  Reprocessing replaces only `waiting` drafts, approved history is preserved.
- To show a fresh Jira issue, approve a **decision** draft (it creates a new issue rather than
  transitioning an existing one).

## Checklist before sharing

- [ ] Duration ≤ 60 s (most players show this in "info").
- [ ] No credentials, tokens or `.env` contents visible on screen.
- [ ] Jira URL visible in the final shot.
- [ ] Link opens without sign-in; test it in a private/incognito window.
- [ ] Paste the link into the submission and README if desired.
