# AI Project Manager Agent

> **Production-grade AI Project Manager Agent**  
> Ingests project meeting transcripts, audits spoken status against live GitHub code activity, drafts grounded project updates with verbatim citations, and synchronizes approved changes back to Jira with human-in-the-loop approval. The UI is a React/Vite SPA served by FastAPI at `/app/`.

---

## 🏗️ Architecture & Tech Stack

- **Frontend:** React 19 + Vite 8 + TypeScript SPA, served by FastAPI at `/app/` (built with `npm --prefix frontend run build`). Dev mode: `./scripts/run.sh --dev` runs FastAPI on :8000 and Vite HMR on :5173.
- **Backend:** FastAPI (asynchronous REST API).
- **Orchestration:** LangGraph state machine (`AgentState`: fetch context → extract grounded drafts → persist → link code).
- **LLM:** Pluggable provider — Gemini (`gemini`), OpenAI-compatible/self-hosted (`openai_compatible`), or explicit mock (`mock`). Configured via `AI_PROVIDER` in `.env` / `backend/config/settings.yaml`. No silent mock fallback; mock output only when explicitly selected or `AI_ALLOW_MOCK_FALLBACK=true`.
- **Model Context Protocol (MCP):** Jira Cloud, GitHub and Microsoft Teams connectors. Live when credentials are configured; failures are surfaced, never silently faked.
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
| 6 & 13 | GitHub reality check | `discrepancy_detected` flag + `github_evidence` linking when spoken status conflicts with open PRs/commits. |
| 7 | PM review queue | Human approval gate: nothing writes to Jira without a recorded approval. Approve / Edit / Reject / Batch Approve / Retry. |
| 8 | Jira writeback | Status transitions, idempotency labels/keys, `sync_status`/`sync_error`, retry endpoint. Approval persisted before any Jira call. |
| 9 | Multi-role views | PM, Team Member (own actions only), Sponsor (read-only), Admin (connectors/users/privacy). |
| 10 | Deadline alerts + forecast | Startup scheduler + `/alerts/check`; notifications deduplicated to owner + project PM per item/day; forecast recomputed from action-item state. |
| 11 | Change history | SQLite `history` table + `GET /history` audit trail (who approved/edited, when, Jira ticket). |
| 12 | Privacy | Private meetings/series rejected before storage; marking private purges all derived data. |
| 14 | Visible AI cost | Provider-reported tokens × price table (`backend/config/settings.yaml`); cost per model/meeting on Metrics page. |
| 15 | 5-minute SLA | SLA measured ingestion→drafts; p95/avg/last latency + compliance % on Metrics page. |

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

## 🔐 Authentication & access control

- `POST /auth/login` returns a Bearer session token; `GET /auth/me` returns the user and their projects.
- Every route resolves the caller from the session token and enforces **role + project membership**.
- `Team Member` sees only their own action items and cannot approve, upload, or view metrics/drafts.
- `Admin` is the only role allowed integrations and user/project management.
- Jira is never written without a persisted approval record (`approve_draft_record` runs before any Jira call).

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

Results are written as JSON with action-item recall/precision, owner/date accuracy, scope detection, decision/RAID/status recall, citation coverage, and per-meeting breakdowns. Latest mock result: `evals/results/mock.json`.

---

## 🔄 Replaceable AI provider

Configure `AI_PROVIDER` in `.env`:

- `gemini` (default): `gemini-3.5-flash-lite`, cost tracked from usage.
- `openai_compatible`: any OpenAI-compatible server (Ollama, vLLM, LM Studio) via `OPENAI_BASE_URL` / `OPENAI_MODEL`.
- `mock`: deterministic offline provider for tests and explicit offline demos.

Mock is **never** used as a hidden fallback when a real provider fails — live failures surface as errors. `AI_ALLOW_MOCK_FALLBACK=true` is required to add mock as a final candidate.

---

## 📋 Assumptions & design decisions

1. **Meeting source:** Upload/import (JSON, WebVTT, Teams Graph) is the chosen ingestion path; Teams is labelled as a fallback until Graph credentials are configured.
2. **Strict traceability:** Any draft without a verified transcript source quote is discarded by `validate_source_quote`; unsourced/invalid output is never saved.
3. **Human approval is a hard gate:** approval is recorded in SQLite before any Jira call; retries are idempotent and never create duplicate issues.
4. **No silent fake data:** live Jira/GitHub/Gemini failures are surfaced; mock data only when credentials are absent or `mock` is explicitly selected.
5. **Cost is an estimate** from provider-reported tokens × the price table in `backend/config/settings.yaml`, not an invoice.
6. **SLA target** is 300 seconds (5 minutes), configurable via `SLA_SECONDS`.
7. **Private series:** future meetings in a private series are skipped before storage; existing meetings must be marked private separately to purge.
