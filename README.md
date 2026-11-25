# AI Project Manager Agent

> **Production-grade AI Project Manager Agent**  
> Ingests project meeting transcripts, audits spoken status against live GitHub code activity, drafts grounded project updates with verbatim citations, and synchronizes approved changes back to Jira with human-in-the-loop approval. The UI is a React/Vite SPA served by FastAPI at `/app/`.

---

## 🏗️ Architecture & Tech Stack

- **Frontend:** React 19 + Vite 8 + TypeScript SPA, served by FastAPI at `/app/` (built with `npm --prefix frontend run build`). Dev mode: `./scripts/run.sh --dev` runs FastAPI on :8000 and Vite HMR on :5173.
- **Backend:** FastAPI (asynchronous REST API).
- **Orchestration:** LangGraph state machine (`AgentState`: fetch context → extract grounded drafts → persist → link code).
- **LLM:** Pluggable provider — Gemini (`gemini`), OpenAI-compatible/self-hosted (`openai_compatible`), or explicit mock (`mock`). Configured via `AI_PROVIDER` in `.env` / `backend/config/settings.yaml`. No silent mock fallback; mock output only when explicitly selected or `AI_ALLOW_MOCK_FALLBACK=true`.
- **Model Context Protocol (MCP):** Jira Cloud, GitHub and Microsoft Teams connectors registered as `MCPServer`s in `backend/mcp_servers.py` and called in-process. They can also be run as standalone servers for any external MCP client:
  `python -m backend.mcp_app jira|github|teams` (stdio by default; `MCP_TRANSPORT=sse|streamable-http` for HTTP). Live when credentials are configured; failures are surfaced, never silently faked.
- **Database:** SQLite (`aiosqlite`) with migrations, full audit history, and transcript-line traceability.
- **Auth:** Bearer session tokens via `/auth/login`; seeded demo accounts use password `demo1234`; project membership enforces access on every route.

---

## 📥 Meeting ingestion (documented path)

The agreed, production meeting-ingestion path is **upload/import of a transcript** (documented in the README), with Microsoft Teams as a secondary option when Graph credentials are configured:

| Path | Endpoint | Notes |
|------|----------|-------|
| Paste Granola/Wispr JSON | `POST /meetings/upload` | Body is `TranscriptUploadPayload` with numbered `transcript` lines. |
| Upload JSON transcript file | `POST /meetings/upload-file` | Multipart file upload; accepts Granola/Wispr-style JSON. |
| Upload Teams WebVTT | `POST /meetings/upload-vtt` | Teams WebVTT keeps speaker names and timestamps; `parse_vtt` splits into numbered lines. |
| Import from Microsoft Graph | `GET /teams/meetings` + `POST /teams/import` | Real Microsoft Graph connector + VTT parsing. Falls back to documented demo fixtures (clearly labelled `mode: mock`) when Teams is not configured. |
| Sample demo transcript | `GET /sample-transcript` | Loads `data/mock_transcript.json` for offline demos. |

Private meetings are rejected before storage: `is_private=true` is skipped in `_ingest`, and marking an existing meeting private purges its transcript, drafts, action items and notifications, and blocks its recurring series from future ingestion.

> **README assumption:** Teams transcription is not configured in this environment, so the UI labels the Teams path clearly and uses upload/import (JSON/WebVTT/sample) as the chosen meeting-source path. If you configure `TEAMS_ACCESS_TOKEN` (or client-credentials app registration), the Teams path becomes `live`.

---

## ⚡ Core Features & PRD Compliance

| # | Feature | Implementation |
|---:|---------|----------------|
| 1 | Meeting ingestion | Granola/Wispr JSON, JSON file upload, WebVTT upload, Teams Graph import, sample transcript. |
| 2 | Minutes & decisions | Grounded drafts with mandatory verbatim source quotes; every draft cites exact transcript lines. |
| 3 | Action items | Resolves relative dates ("by Friday" → `YYYY-MM-DD`); unstated owners marked `"MISSING"` (never guessed). |
| 4 | RAID log | Risks, Assumptions, Issues, Dependencies with severity and update detection. |
| 5 | Scope creep detection | Compares spoken requests against `agreed_scope` baseline; flags genuine scope changes. |
| 6 | Update existing work | Proposes status changes on matching Jira issues using GitHub evidence, with a confidence score; unmatched issues go to the PM to decide. |
| 7 | PM review queue | Human approval gate: nothing writes to Jira without a recorded approval. Approve / Edit / Reject / Batch Approve / Retry. |
| 8 | Jira writeback | Status transitions, idempotency labels/keys, `sync_status`/`sync_error`, retry endpoint. Approval persisted before any Jira call. |
| 9 | A view for each user | PM, Team Member (own actions only), Sponsor (read-only), Admin (connectors/users/privacy). |
| 10 | Deadline alerts | Startup scheduler + `/alerts/check`; notifications deduplicated to owner + project PM per item/day; forecast recomputed from action-item state. |
| 11 | Change history | SQLite `history` table + `GET /history` audit trail (original draft, edits, who approved, when). |
| 12 | Private meetings | Private meetings/series rejected before storage; marking private purges all derived data. |
| 13 | Track code progress | Links GitHub PRs, commits and issues to Jira keys/action items; flags mismatches when spoken status conflicts with code. |

**Quality rules (not numbered features):** visible AI cost from provider-reported tokens, 5-minute
draft SLA, strict traceability, approval-first, replaceable LLM provider, and role/project access
control are all measured/enforced as described below.

---

## 🚀 Quick Start

```bash
# 1. Copy environment template
cp .env.example .env
# 2. Add live credentials to .env (GEMINI_API_KEY, Jira, GitHub) — or leave for offline mock mode
# 3. Run everything
./scripts/run.sh
```

- Web app: `http://localhost:8000/app/` (React SPA served by FastAPI)
- API docs: `http://localhost:8000/docs`
- Dev mode (Vite HMR): `./scripts/run.sh --dev` (FastAPI :8000 + Vite :5173)

Demo accounts (password `demo1234`): `sarah` (PM), `alex`/`david`/`elena` (Team Member), `mark` (Sponsor), `admin` (Admin). All seeded into project `KAN`.

```bash
# Backend only
.venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000

# Frontend dev only
VITE_API_PROXY=http://127.0.0.1:8000 npm --prefix frontend run dev
```

---

## ☁️ Free public demo (no real credentials)

A preloaded, credential-free version can be hosted for free on **Render** in about five minutes. It uses `AI_PROVIDER=mock`, deterministic mock Jira/GitHub connectors, and a sanitised snapshot at `data/seed_demo.db`, so the app opens with three meetings, 18 waiting drafts, linked code activity and seeded accounts. **No real keys, no real Jira writes.**

- Push to GitHub, then in Render choose **New → Blueprint** and select this repository. `render.yaml` sets every variable; no secrets are needed.
- Or create a **Web Service** manually: language **Docker**, build from `Dockerfile`, health check `/health`, free plan.
- Open `https://<service-name>.onrender.com/app/` and log in with `sarah` / `demo1234` (or any account above).

Important differences from local mode:

- The free instance **sleeps after 15 idle minutes** and takes about a minute to wake — give the panel a heads-up.
- Its filesystem is temporary. After a restart the database is recreated from `data/seed_demo.db`, so any approvals made by visitors disappear.
- Because connectors are mocked, approving a draft does **not** touch a real Jira project. The live Jira/GitHub path requires local credentials as described above.

To run the same mode locally without touching your live `.env`:

```bash
SEED_DEMO_DB=true PM_DB_PATH=/tmp/demo.db APP_ENV=production \
AI_PROVIDER=mock CONNECTORS_ALLOW_MOCK=true SCHEDULER_ENABLED=false \
JIRA_BASE_URL= JIRA_EMAIL= JIRA_API_TOKEN= GITHUB_TOKEN= \
.venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

---

## 🔐 Authentication & access control

- `POST /auth/login` returns a Bearer session token; `GET /auth/me` returns the user and their projects.
- Every route resolves the caller from the session token and enforces **role + project membership**.
- `Team Member` sees only their own action items and cannot approve, upload, or view metrics/drafts.
- `Admin` is the only role allowed integrations and user/project management.
- Jira is never written without a persisted approval record (`approve_draft_record` runs before any Jira call).

---

## 🎯 Per-project Jira & GitHub targets

The project dropdown scopes **what you can see** and now also **which external targets are used**.
Credentials stay global in `.env` (one Jira site + one GitHub token), but each project stores its own:

- **Jira project key** — used for issue context, idempotency lookup, and where new issues are created.
- **GitHub repository** — used for PRs, commits and issues during extraction and code sync.

Admins (or PMs) set these in **Admin → Project targets** or via:

```bash
curl -X PATCH -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"jira_project_key":"KAN","github_repo":"Shivaalle/pm-agent"}' \
  http://localhost:8000/projects/KAN
```

Leave a field empty to fall back to `JIRA_PROJECT_KEY` / `GITHUB_REPO` from `.env`. Existing approved
drafts keep their original Jira issue; only new drafts and code sync use the updated targets.

**Projects and access:** a user only sees projects they are a member of. Admins can create a project
(**Admin → Projects → New project**, or `POST /projects`), set its targets, then add/remove members
under **Project members**. Example: `OP` → Jira `OP`, GitHub `Shivaalle/opsbuddy-agentic-flow`,
with `shiva_alle` as its PM and no membership in `KAN`; `sarah` remains PM of `KAN`. The dropdown
then shows only the projects each user belongs to, and deadline alerts go to that project's PM.

---

## 🧪 Eval harness & tests

Run the unit/auth/privacy/accuracy suite:

```bash
.venv/bin/python -m pytest -q
```

Run the measured golden-meeting accuracy harness (default: explicit mock provider, offline connectors — never touches live Jira/GitHub):

```bash
.venv/bin/python -m evals.run --provider mock --out evals/results/mock.json
```

Live measured run against the real LLM provider:

```bash
.venv/bin/python -m evals.run --provider gemini --out evals/results/live.json
```

Results are written as JSON with action-item recall/precision, owner/date accuracy, scope detection, decision/RAID/status recall, citation coverage, and per-meeting breakdowns. Latest results: `evals/results/mock.json` and `evals/results/live.json` (Gemini, 10.5 s for 4 meetings).

---

## 🔄 Replaceable AI provider

Configure `AI_PROVIDER` in `.env`:

- `gemini` (default): `gemini-3.5-flash-lite`, cost tracked from usage.
- `openai_compatible`: any OpenAI-compatible server (Ollama, vLLM, LM Studio) via `OPENAI_BASE_URL` / `OPENAI_MODEL`.
- `mock`: deterministic offline provider for tests and explicit offline demos.

Mock is **never** used as a hidden fallback when a real provider fails — live failures surface as errors. `AI_ALLOW_MOCK_FALLBACK=true` is required to add mock as a final candidate.

---

## 📚 Go deeper

- **[docs/AI_PM_Agent_Writeup.docx](docs/AI_PM_Agent_Writeup.docx)** — the official one-page submission write-up (Word document, plain language) answering the core problem, approach, hardest decision, and what we left out.
- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — system architecture diagram, complete workflow, and decision gates.

---

## 📋 Assumptions & design decisions

1. **Meeting source — option chosen:** *Upload/import of a ready-made transcript* (Granola/Wispr JSON, Teams WebVTT, or pasted JSON). We used this because no Microsoft 365 plan with Teams transcription was available, so the Graph transcript endpoint could not be enabled. The real Microsoft Graph connector is still implemented and becomes `live` as soon as `TEAMS_ACCESS_TOKEN` (or app credentials) are set; everything after ingestion is identical whichever source is used.
2. **One organisation, several projects:** each project stores its own Jira key and GitHub repo; credentials are shared across the organisation.
3. **Simple sign-in, real roles:** username/password demo sessions are acceptable, but every route enforces role and project membership server-side.
4. **English meetings:** all prompts, date phrases and discrepancy heuristics assume English.
5. **No model training on company data:** transcripts are only sent to the selected inference provider for extraction; no fine-tuning or embedding training is performed. A self-hosted provider can be selected for data residency.
6. **Strict traceability:** any draft without a verified transcript source quote is discarded by `validate_source_quote`; unsourced/invalid output is never saved.
7. **Human approval is a hard gate:** approval is recorded in SQLite before any Jira call; retries are idempotent and never create duplicate issues.
8. **No silent fake data:** live Jira/GitHub/Gemini failures are surfaced; mock data only when credentials are absent or `mock` is explicitly selected.
9. **Cost is an estimate** from provider-reported tokens × the price table in `backend/config/settings.yaml`, not an invoice.
10. **SLA target** is 300 seconds (5 minutes), configurable via `SLA_SECONDS`.
11. **Private series:** future meetings in a private series are skipped before storage; existing meetings must be marked private separately to purge.
12. **Notifications:** deadline and approval alerts are delivered in-product; outbound Teams messages are not sent (see the write-up).
