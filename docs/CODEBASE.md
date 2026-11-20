# Codebase Map — know the whole system in your hands

Read this once and you can navigate, explain, and modify any part of the project.

---

## 1. Architecture at a glance

```
Browser (React SPA at /app/)
   │  Bearer token in localStorage
   ▼
FastAPI  backend/main.py            ← auth, project scoping, all REST routes
   │
   ├─ backend/auth.py               ← role + project permission checks
   ├─ backend/agent.py              ← LangGraph: context → draft → save → code link
   │     ├─ backend/llm/*           ← pluggable Gemini / OpenAI-compatible / mock
   │     └─ backend/mcp_servers.py  ← Jira, GitHub, Teams connectors (MCP tools)
   ├─ backend/services.py           ← approval gate, Jira writeback, retry, alerts, forecast
   ├─ backend/database.py           ← SQLite schema, migrations, all persistence
   └─ backend/models.py             ← Pydantic contracts (LLM schemas + API records)
   │
   ▼
SQLite data/agent.db                ← meetings, transcripts, drafts, actions, audit, cost, SLA
```

**External systems:** Jira Cloud (writeback + issue context), GitHub (PRs/commits/issues),
Microsoft Graph/Teams (optional meeting transcript), Gemini or a self-hosted LLM.

---

## 2. Directory tree (every file)

### Backend (`backend/`)

| File | Responsibility |
|------|----------------|
| `main.py` | FastAPI app, lifespan (DB init + alert scheduler), all endpoints, SPA static mount at `/app`, `/auth/*`, `/meetings/*`, `/teams/*`, `/drafts/*`, `/history`, `/action-items/*`, `/notifications/*`, `/forecast*`, `/alerts/check`, `/metrics*`, `/overview`, `/code-activity*`, `/integrations`, `/privacy/series`, `/series/*`, `/sample-transcript`, `/capabilities`, `/health`, `/`. |
| `auth.py` | `UserContext` with `require(action)` and `require_project(key, action)`; `ROLE_ALLOWED` matrix; Bearer session parsing; admin helpers. |
| `agent.py` | `SYSTEM_PROMPT` (strict traceability/no-guessing rules); `AgentState`; four LangGraph nodes: `fetch_context`, `draft_and_cross_reference`, `save_drafts`, `code_link`; compiled `pm_agent_app`. |
| `services.py` | `build_jira_payload()`, `approve_draft()` (**persists approval before any Jira call**), `retry_draft_sync()`, `run_deadline_check()`, `link_code_activity()`, notification helpers. |
| `database.py` | `SCHEMA` + `INDEXES`, idempotent `init_db()` with legacy migrations + transcript backfill, seed users/project, all CRUD, `validate_source_quote()` (drops ungrounded drafts), `normalise_due_date()`, forecast recompute, cost/SLA/quality metrics, `purge_meeting()`. |
| `models.py` | LLM extraction schemas (`Decision`, `ActionItem`, `RAIDItem`, `ScopeChange`, `StatusUpdate`, `MeetingDrafts`) and API records (`DraftRecord`, `MeetingRecord`, `ActionItemRecord`, `NotificationRecord`, `ForecastRecord`, `CodeActivityRecord`, `CostSummary`, `SlaSummary`, `QualityMetrics`, `IntegrationsResponse`, …). |
| `mcp_servers.py` | Jira Cloud REST (search, transitions, idempotent create/update, label lookup, comments), GitHub REST (PRs, commits, issues), Teams Graph (token, meeting list, transcript, VTT parser). Deterministic mock fixtures only when credentials absent. `ConnectorError` on live failure. |
| `llm/client.py` | `LLMClient`: primary + optional backup, bounded retries, usage/cost trail, explicit-mock gating (`AI_ALLOW_MOCK_FALLBACK`). |
| `llm/providers/base.py` | `LLMProvider` interface, `LLMResponse`, `LLMUsage`. |
| `llm/providers/gemini.py` | Gemini via `google-genai`, strict `response_schema`. |
| `llm/providers/openai_compatible.py` | Any OpenAI-compatible server (Ollama/vLLM/LM Studio). |
| `llm/providers/mock.py` | Deterministic offline provider for tests/explicit offline demos. |
| `config/loader.py` | `Settings`: loads `.env` secrets + `settings.yaml` params; computes `jira_live`, `github_live`, `teams_live`, `model_price()`. |
| `config/settings.yaml` | Non-secret config: provider, model pricing, SLA (300s), scope baseline, scheduler, Jira/GitHub/Teams defaults. |
| `logger.py` | Shared logging setup. |

### Frontend (`frontend/src/`)

| File | Responsibility |
|------|----------------|
| `main.tsx` | React root (`StrictMode`), imports `styles.css`. |
| `App.tsx` | Session bootstrap from localStorage, top bar (project picker + live/mock chips), tab bar with role-filtered views, notification drawer, routes to the 7 views. |
| `types.ts` | All API/entity TypeScript interfaces (must mirror `backend/models.py`). |
| `api.ts` | Typed `fetch` wrapper: Bearer header, 401 handler, `ApiError`, `query()` builder, every endpoint. |
| `toast.tsx` | `ToastProvider` + `useToast()` — named live region, focus-pause on dismissal. |
| `utils.ts` | Date/number/health/role formatting helpers. |
| `components/Login.tsx` | Sign-in page with demo-role cards. |
| `components/OverviewView.tsx` | Project pulse, stats, needs-attention list, forecast sparkline, recent meetings/drafts, code-vs-conversation. |
| `components/MeetingsView.tsx` | 4 ingestion methods (paste JSON / JSON file / WebVTT / Teams), sample loader, transcript detail dialog, reprocess, mark-private purge. |
| `components/ReviewView.tsx` | Review queue: filters, select-all, draft card, JSON editor, approve/reject, **batch approve**, Jira retry, idempotency-key confirmation. |
| `components/ActionItemsView.tsx` | Owner-scoped action list, status transitions (open/in_progress/done/cancelled), deadline scan. |
| `components/MetricsView.tsx` | Cost table + per-meeting, SLA gauge, quality funnel, forecast history, code-activity links, GitHub sync. |
| `components/AdminView.tsx` | Integration health, users, project members, private recurring series, agreed scope. |
| `components/ui.tsx` | Shared `Spinner`, `LoadingBlock`, `ErrorBlock`, `Badge`, `Stat`, `EmptyState`, `SectionHeader`, native `<dialog>` wrapper. |
| `styles.css` | Design tokens (light/dark), all component styles, responsive breakpoints, forced-colors + reduced-motion support. |

### Other

| Path | Purpose |
|------|---------|
| `scripts/run.sh` | One-command bootstrap (build React + FastAPI) and `--dev` mode. |
| `requirements.txt` | Python deps (FastAPI, google-genai, langgraph, mcp, aiosqlite, yaml, multipart, pytest). |
| `.env.example` | Template for secrets + provider/connector overrides. |
| `data/mock_transcript.json` | Sample meeting used by the demo and eval fixtures. |
| `evals/run.py` + `evals/golden/*.json` | Accuracy harness and golden meetings with human labels. |
| `evals/results/*.json` | Measured metric outputs (gitignored). |
| `tests/` | pytest suite (conftest sets isolated DB + offline providers). |
| `docs/DEMO.md` | Hands-on E2E test + demo script (this guide's companion). |

---

## 3. The five core flows (trace the code)

### 3.1 Ingest → grounded drafts
`POST /meetings/upload` (or upload-file / import-json / upload-vtt / teams-import)
→ `_ingest()` in `main.py`: project scope check → private/series check → `save_meeting()`
→ `_run_agent()` → LangGraph `fetch_context` (Jira + GitHub context) → `draft_and_cross_reference`
(LLM `MeetingDrafts`) → `save_drafts` (**`validate_source_quote` discards unsourced drafts**,
records cost + SLA) → `code_link` (persist GitHub links + mismatches) → deadline check → `UploadResult`.

### 3.2 Human approval → Jira writeback (hard gate)
`POST /drafts/{id}/approve` → `services.approve_draft()`:
1. `build_jira_payload()` — may raise before any write (e.g. unmatched status update).
2. `approve_draft_record()` — **persists `status=approved` + history FIRST**.
3. `create_or_update_issue()` — idempotency label/key, status transitions.
4. `record_sync_attempt()` → `sync_status` synced/partial/failed + `sync_error`.
5. For action items: `create_action_item()` + owner notification + forecast recompute.
6. `POST /drafts/{id}/retry` only works on approved failed/partial drafts — safe, no duplicates.

### 3.3 Deadline alerts + forecast
Scheduler in `main.py` calls `run_deadline_check()` every `ALERT_SCAN_INTERVAL_SECONDS`;
`/alerts/check` triggers on demand. Overdue open items → deduplicated notifications to
**owner + project PM** (`deadline:{item}:{today}:{recipient}`) → `recompute_forecast()`
(health on_track / at_risk / off_track) from live action-item state.

### 3.4 Access control
Every route: `Depends(get_current_user)` → Bearer token → session lookup → `UserContext`.
`require(action)` checks `ROLE_ALLOWED`; `require_project(key)` checks membership.
- PM: approve/edit/reject/upload/manage_private/view_metrics.
- Team Member: read own action items only; no drafts/metrics/upload.
- Sponsor: read-only + metrics.
- Admin: integrations, users, project membership.

### 3.5 Privacy
Private meeting/series rejected **before storage** in `_ingest` and Teams import.
`POST /meetings/{id}/private` → add series to `private_series` → `purge_meeting()` deletes
transcript lines, drafts, history, action items, notifications, meeting row.

---

## 4. Database tables (`data/agent.db`)

`projects`, `users`, `sessions`, `project_members`, `meetings`, `transcript_lines`,
`drafts`, `history`, `action_items`, `notifications`, `forecast`, `llm_calls`, `sla_records`,
`agent_runs`, `code_activity`, `private_series`.

Key integrity rules enforced in code:
- `transcript_lines(meeting_id, line_no)` unique; drafts cite verified line numbers only.
- `notifications.dedupe_key` unique (prevents alert spam).
- `forecast(project_key, date)` unique (daily snapshot upsert).
- Reprocessing a meeting replaces only `waiting` drafts; approved/rejected + audit preserved.

---

## 5. API endpoint groups (Swagger at /docs)

| Group | Endpoints |
|-------|-----------|
| Auth | `POST /auth/login`, `POST /auth/logout`, `GET /auth/me`, `GET /auth/demo-accounts` |
| Meta | `GET /health`, `GET /capabilities`, `GET /sample-transcript` |
| Projects/users | `GET /projects`, `GET /projects/{key}`, `GET/POST /projects/{key}/members`, `DELETE …/{user_id}`, `GET/POST /users` |
| Meetings | `POST /meetings/upload`, `/upload-file`, `/import-json`, `/upload-vtt`, `GET /meetings`, `GET /meetings/{id}`, `POST /meetings/{id}/reprocess`, `POST /meetings/{id}/private` |
| Teams | `GET /teams/meetings`, `POST /teams/import` |
| Drafts | `GET /drafts`, `GET /drafts/{id}`, `POST /{id}/approve`, `POST /batch-approve`, `POST /{id}/edit`, `POST /{id}/reject`, `POST /{id}/retry`, `GET /history` |
| Actions | `GET /action-items`, `GET /action-items/{id}`, `PATCH /action-items/{id}` |
| Notifications | `GET /notifications`, `POST /{id}/read`, `POST /read-all` |
| Forecast/alerts | `GET /forecast`, `GET /forecast/history`, `POST /alerts/check` |
| Metrics | `GET /metrics`, `/metrics/cost`, `/metrics/sla`, `/metrics/quality`, `GET /overview` |
| Code | `GET /code-activity`, `POST /code-activity/sync` |
| Admin | `GET /integrations`, `GET /privacy/series`, `POST/DELETE /series/{id}/private` |

---

## 6. Where to change what

| I want to… | Edit |
|-----------|------|
| Change LLM model / provider / pricing | `backend/config/settings.yaml`, `.env` |
| Change extraction rules / prompt | `backend/agent.py` `SYSTEM_PROMPT` |
| Change grounding strictness | `backend/database.py` `validate_source_quote()` |
| Change approval / Jira payload | `backend/services.py` `build_jira_payload()`, `approve_draft()` |
| Add an API route | `backend/main.py` (+ `models.py` schema, `api.ts` client, `types.ts`) |
| Change roles/permissions | `backend/auth.py` `ROLE_ALLOWED` |
| Change alert cadence | `settings.yaml` `scheduler` / `.env` `ALERT_SCAN_INTERVAL_SECONDS` |
| Change UI view/tab | `frontend/src/App.tsx` `TABS` + add component in `components/` |
| Change API client | `frontend/src/api.ts` |
| Change styling/tokens | `frontend/src/styles.css` `:root` and dark-mode block |
| Add golden eval case | `evals/golden/*.json` (run `python -m evals.run`) |

---

## 7. Invariants to preserve (PRD hard requirements)

1. **No Jira write without a recorded approval** (`approve_draft_record` before `create_or_update_issue`).
2. **No unsourced draft saved** (`validate_source_quote` must return line numbers).
3. **No silent fake data on live failure** (connectors raise `ConnectorError`; LLM fails visibly).
4. **Project + role enforced server-side on every route** (never trust the client).
5. **Retries idempotent** (idempotency labels/keys; no duplicate Jira issues).
6. **Private meetings/series never stored** (reject before persistence; purge on mark-private).
7. **Cost + SLA measured from real runs** (not estimated by the UI).
