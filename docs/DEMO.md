# End-to-End Testing & Demo Guide

This is the hands-on guide to run, test, and demo the whole system yourself. Everything below is
copy-pasteable from the repository root (`/Users/shivaalle/Documents/pm-agent`).

---

## 0. What you need

- Python 3.11+ (this machine: 3.14) with `.venv`
- Node 20+ (this machine: 26) with `npm`
- `.env` with live credentials (Gemini, Jira, GitHub) — already present here.
- Ports `8000` (and `5173` in dev mode) free.

---

## 1. Start everything (one command)

```bash
./scripts/run.sh
```

This builds the React app, starts FastAPI, and serves the SPA at:

- **Web app:** http://localhost:8000/app/
- **API docs (Swagger):** http://localhost:8000/docs
- **Health:** http://localhost:8000/health

For hot-reload development instead:

```bash
./scripts/run.sh --dev     # FastAPI :8000 + Vite HMR :5173 → http://localhost:5173/app/
```

Manual start (if you prefer separate terminals):

```bash
.venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
npm --prefix frontend run dev    # optional, dev only
```

Verify:

```bash
curl -fsS http://localhost:8000/health
# {"status":"ok","version":"2.0.0","database":"...","scheduler":"running"}
```

Demo logins (password `demo1234`): `sarah` (PM), `alex`/`david`/`elena` (Team Member),
`mark` (Sponsor), `admin` (Admin). All are members of project `KAN`.

---

## 2. Automated checks (run these first)

```bash
# Python: auth, grounding, approval gate, retry, privacy, alerts
.venv/bin/python -m pytest -q

# Accuracy harness (deterministic mock, no live writes)
.venv/bin/python -m evals.run --provider mock --out evals/results/mock.json

# Accuracy harness (live Gemini, read-only connectors)
.venv/bin/python -m evals.run --provider gemini --out evals/results/live.json

# Frontend: strict typecheck + production build
npm --prefix frontend run build

# Modern Web Guidance verification (installed plugin)
export DISABLE_TELEMETRY=1
npx -y modern-web-guidance@latest legacy-scan frontend/src
# → 0 findings from 38 qualified guides
```

Expected: `14 passed`, eval JSON with metrics, build succeeds, 0 legacy findings.

---

## 3. UI walkthrough (what to click, what to expect)

### A. Sign in as the PM
1. Open http://localhost:8000/app/ — you land on the sign-in page.
2. Click **Sarah Chen** (or type `sarah` / `demo1234`) → **Sign in**.
3. Top bar shows live mode chips: `AI live · gemini-…`, `Jira live`, `GitHub live`, `Teams upload`.

### B. Ingest a meeting
1. Go to **Meetings → Paste JSON → Load sample** (loads `data/mock_transcript.json`).
2. Optionally set title/date/attendees. Leave **Private meeting** unchecked.
3. Click **Upload and extract**.
   - Expect toast: "8 grounded drafts created in ~15–70s."
   - If live Gemini is slow, wait; SLA target is 300s.
4. Click **Review drafts** — goes to Review queue.

### C. Review and approve (the human gate)
1. Review queue shows cards: Decision, Action items, RAID, Scope change, Status updates.
   - Each card shows the **verbatim source quote** and **line numbers**.
   - `KAN-1`/`KAN-2` status cards show a red **Code/conversation mismatch** callout.
2. Open a card → **Approve… → Confirm approval and write to Jira**.
   - Expect toast: "Approved and written to Jira as KAN-N."
   - The draft now shows `Jira synced`.
3. Try the same for an Action item → it becomes a trackable action item.
4. Approve several at once with the checkboxes → **Approve selected**.

### D. Track delivery
1. **Action items** → the approved item has owner + due date + Jira key.
2. Owner (e.g. Elena) signs in and can **Mark done** only on their own item.
3. **Run deadline scan** → generates alerts (bell badge) for overdue items.
4. **Overview** → project pulse, forecast sparkline, needs-attention list.

### E. See measured quality
1. **Metrics** → AI cost (USD, tokens), SLA compliance % and p95, approval quality,
   forecast snapshots, GitHub code-activity links.
2. **Sync GitHub** to refresh code activity (expect 0 mismatches in this repo).

### F. Admin & privacy
1. Sign in as `admin` → **Admin** tab: integrations health, users, project members,
   private series, agreed baseline scope.
2. In **Meetings**, a meeting card has **Mark private** → confirm → transcript, drafts,
   action items and notifications are purged; its recurring series is blocked.

---

## 4. API E2E script (copy-paste, all features)

```bash
set -e
BASE=http://127.0.0.1:8000

# --- login as PM ---
TOKEN=$(curl -s -X POST $BASE/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"sarah","password":"demo1234"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
H="Authorization: Bearer $TOKEN"

echo "--- capabilities ---"
curl -s -H "$H" $BASE/capabilities | python3 -m json.tool

echo "--- upload sample via Gemini ---"
curl -s -H "$H" -F "file=@data/mock_transcript.json;type=application/json" \
  "$BASE/meetings/upload-file?project_key=KAN" | python3 -m json.tool

echo "--- drafts with citations ---"
curl -s -H "$H" "$BASE/drafts?project_key=KAN" | python3 -c '
import sys,json
for d in json.load(sys.stdin):
    print(d["id"], d["type"], d["status"], "lines", d["source_lines"], "mismatch", d["discrepancy_detected"])
'

echo "--- approve a decision (writes to Jira) ---"
curl -s -X POST -H "$H" $BASE/drafts/14/approve | python3 -m json.tool

echo "--- action items / alerts / cost / SLA ---"
curl -s -H "$H" "$BASE/action-items?project_key=KAN" | python3 -m json.tool
curl -s -X POST -H "$H" "$BASE/alerts/check?project_key=KAN" | python3 -m json.tool
curl -s -H "$H" "$BASE/metrics/cost?project_key=KAN" | python3 -m json.tool
curl -s -H "$H" "$BASE/metrics/sla?project_key=KAN" | python3 -m json.tool

echo "--- role enforcement: Alex (Team Member) ---"
ALEX=$(curl -s -X POST $BASE/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"alex","password":"demo1234"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
for path in metrics drafts; do
  printf "alex /%s -> " "$path"
  curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $ALEX" "$BASE/$path?project_key=KAN"
done   # expect 403, 403

echo "--- privacy: mark a meeting private then verify purge ---"
curl -s -X POST -H "$H" -H 'Content-Type: application/json' \
  -d '{"reason":"demo"}' $BASE/meetings/MEET-20261119-01/private | python3 -m json.tool
curl -s -o /dev/null -w 'after purge: %{http_code}\n' -H "$H" $BASE/meetings/MEET-20261119-01  # 404
```

> Note: the last step purges the demo meeting. Do it at the **end** of the demo, or use a
> throwaway meeting ID (upload a custom meeting first).

---

## 5. A 5-minute demo narrative

1. **Problem (30s):** "Meetings produce updates, but status is spoken, not written. PMs chase
   people, and AI guesses get shipped to Jira."
2. **Ingest (60s):** Upload the sample transcript. Show the numbered transcript detail view.
3. **Grounding (60s):** Open a draft. Point at the **verbatim quote + line numbers**. Emphasise:
   unsourced drafts are discarded by the database layer.
4. **Human gate (60s):** Approve a decision → show toast "written to Jira as KAN-N", then open
   the Jira URL. Show the red **code mismatch** on the KAN-1 status card.
5. **Delivery (30s):** Action item with owner/Jira key; alerts; forecast sparkline.
6. **Trust (30s):** Metrics — measured cost, 5-min SLA p95, approval quality; Admin integrations
   showing `live` vs `mock`.
7. **Privacy (30s):** Mark private → everything gone; recurring series blocked.

---

## 6. Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| `Cannot reach the API` in UI | Backend not on :8000 — run `./scripts/run.sh`. |
| Sign-in 401 | Password is `demo1234`; sessions expire in 24h — sign in again. |
| Upload status `failed`, 0 drafts | Live provider unreachable; it fails visibly (no silent mock). Check `logs/uvicorn.log` or set `AI_PROVIDER=mock`. |
| Jira write `failed` | Draft stays **approved** with `sync_error`; use **Retry Jira** — idempotent, no duplicate. |
| Alex cannot see a view | Role enforced server-side; use the right account. |
| Old build in browser | Hard-refresh (Cmd/Ctrl+Shift+R) or rebuild: `npm --prefix frontend run build`. |
| `legacy-scan` findings after edits | `npx -y modern-web-guidance@latest retrieve <guide-id>` and fix. |

---

## 7. Modern Web Guidance reference (the plugin)

```bash
export DISABLE_TELEMETRY=1
npx modern-web-guidance@latest search "<topic>"        # find guides
npx modern-web-guidance@latest retrieve "<guide-id>"   # read a guide
npx modern-web-guidance@latest legacy-scan frontend/src # scan for legacy patterns
```

Guides applied to this frontend: `autofill-sign-up-form`, `js` (toReversed), `dialog`
(showModal + `closedby="any"` + scroll containment), `improve-text-layout-and-legibility`
(`text-wrap: balance`), `persistent-toast-notifications` (named region + focus-pause),
`color` (forced-colors fallbacks), plus light/dark `color-scheme` and `prefers-reduced-motion`.
