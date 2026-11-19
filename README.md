# AI Project Manager Agent

> **Production-grade AI Project Manager Agent MVP**  
> Ingests project meeting transcripts, audits spoken status against live GitHub code activity, drafts grounded project updates with verbatim citations, and synchronizes approved changes back to Jira with human-in-the-loop approval.

---

## 🏗️ Architecture & Tech Stack

- **Backend:** FastAPI (asynchronous REST API).
- **Orchestration:** LangGraph state machine (`AgentState` transitions).
- **Model Context Protocol (MCP):** Native MCP 2.x connectors wrapping Jira Cloud REST API and GitHub REST API.
- **LLM Engine:** Gemini Flash (`gemini-3.5-flash-lite` / `gemini-3-flash-preview`) via the official `google-genai` SDK using strict Pydantic `response_schema` (zero formatting hallucinations).
- **Database:** SQLite (`aiosqlite`) zero-config local database with relational schema and full audit history.
- **Frontend:** Streamlit interactive single-page app with multi-persona role toggle (Project Manager, Team Member, Sponsor, Admin).

---

## ⚡ Core Features & PRD Compliance

| Feature | Description | Implementation |
| :--- | :--- | :--- |
| **1. Meeting Ingestion** | Ingests Granola/Wispr JSON transcripts with speakers and line numbers. | `POST /upload_transcript` & `POST /upload_file` |
| **2. Minutes & Decisions** | Captures summary, architectural decisions, and verbatim rationale. | `decisions` schema with mandatory quote grounding |
| **3. Action Items** | Resolves relative dates ("by Friday" -> `YYYY-MM-DD`). Marks unstated owners as `"MISSING"`. | `action_items` schema with strict no-guessing rules |
| **4. RAID Log** | Classifies Risks, Assumptions, Issues, and Dependencies with severity and updates. | `raid_items` schema with update detection |
| **5. Scope Creep Detection** | Compares spoken discussions against agreed project baseline scope. | `scope_changes` schema with impact assessment |
| **6 & 13. GitHub Reality Check** | Detects discrepancies between spoken claims and Git status (e.g. claim "Done" while PR is still open). | `discrepancy_detected` flag & evidence linking |
| **7. PM Review Queue** | Human-in-the-loop approval gate. Nothing goes to Jira without PM consent. | Streamlit cards with Approve, Edit, Reject, and Batch Approve |
| **8. Jira Writeback** | Idempotent ticket creation, status transitions, and audit comments. | FastMCP Jira tool `create_or_update_issue` |
| **9. Multi-Role Views** | Dedicated views for PM, Team Member (My Tasks), Sponsor (Progress), Admin (Integrations). | Streamlit sidebar persona switch |
| **11. Change History** | Comprehensive audit trail of who approved/edited what and when. | SQLite `history` table & `GET /history` |
| **12. Privacy by Default** | Private meetings are discarded and never processed or stored. | `is_private` check in pipeline |

---

## 🚀 Quick Start Guide

### 1. Environment Setup

Ensure Python 3.11+ is installed. Clone the repository and install dependencies:

```bash
cd pm-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure Environment (`.env`)

Secrets and tokens stay in `.env` (git-ignored), while non-secret configurations (models, agreed scope, parameters) live cleanly in `backend/config/settings.yaml`.

Copy `.env.example` to `.env` and fill in your credentials:

```bash
# Gemini API Key (from Google AI Studio)
GEMINI_API_KEY="your-gemini-api-key"

# Jira Cloud Configuration
JIRA_BASE_URL="https://your-domain.atlassian.net"
JIRA_EMAIL="your-email@example.com"
JIRA_API_TOKEN="your-atlassian-api-token"

# GitHub Configuration
GITHUB_TOKEN="your-github-personal-access-token"
GITHUB_REPO="owner/repo-name"
```

> **Dual-Mode Execution:** If Jira or GitHub tokens are omitted, the connectors automatically operate in seamless mock fallback mode, ensuring zero crashes during local testing.

### 3. Run Everything with One Command

You can bootstrap and run the entire application (FastAPI backend + Streamlit frontend) with:

```bash
bash scripts/run.sh
```

Or run them individually in separate terminals:

```bash
# Terminal 1 - Backend API
uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload

# Terminal 2 - Frontend UI
streamlit run frontend/app.py --server.port 8501
```

Open `http://localhost:8501` in your browser.

---

## 🧪 Testing the Workflow

1. Open the Streamlit app (`http://localhost:8501`).
2. In the **Project Manager** persona view, click **"⚡ Run Agent on Sprint 14 Sample Transcript"**.
3. Watch the LangGraph state machine:
   - Fetches live Jira issues (`KAN-1`, `KAN-2`, `KAN-3`) and GitHub PRs.
   - Extracts Decisions, Action Items, RAID Risks, and Scope Changes.
   - Detects the **discrepancy** where Alex claims Task 1 is complete while PR #45 is still open.
4. Review the generated cards, edit if needed, and click **"Approve"**.
5. Observe the live writeback to your Jira Cloud project!

---

## 📋 Assumptions & Design Decisions

1. **Transcript Format:** Transcripts follow modern JSON exports (such as Granola or Wispr Flow), containing speaker tags and ordered line numbers.
2. **Strict Traceability:** In accordance with PRD quality rules, any LLM output lacking an explicit line-number source quote is automatically rejected by the database ingestion layer.
3. **Idempotency:** Approved drafts record the generated Jira ticket key; re-clicking "Approve" returns the existing ticket without creating duplicates.
