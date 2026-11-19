import aiosqlite
import json
from datetime import datetime
from typing import List, Optional, Dict, Any
from backend.config import settings
from backend.models import MeetingDrafts, DraftRecord, HistoryRecord, ProjectContext

def get_db():
    """
    Returns an async context manager for SQLite database connection.
    Usage:
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            ...
    """
    return aiosqlite.connect(str(settings.DB_PATH))

async def init_db():
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        await db.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                project_key TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                agreed_scope TEXT NOT NULL,
                team_members TEXT NOT NULL
            );
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS meetings (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                meeting_date TEXT NOT NULL,
                attendees TEXT NOT NULL,
                transcript TEXT NOT NULL,
                summary TEXT,
                is_private INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            );
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS drafts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                meeting_id TEXT NOT NULL,
                type TEXT NOT NULL,
                title TEXT NOT NULL,
                proposed_change TEXT NOT NULL,
                source_quote TEXT NOT NULL,
                github_evidence TEXT,
                discrepancy_detected INTEGER DEFAULT 0,
                discrepancy_details TEXT,
                confidence REAL DEFAULT 1.0,
                status TEXT DEFAULT 'waiting',
                jira_issue_key TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (meeting_id) REFERENCES meetings(id)
            );
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                draft_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                user_role TEXT NOT NULL,
                original_content TEXT NOT NULL,
                final_content TEXT NOT NULL,
                jira_ticket_id TEXT,
                timestamp TEXT NOT NULL,
                FOREIGN KEY (draft_id) REFERENCES drafts(id)
            );
        """)
        await db.commit()
    
    # Seed default baseline project if none exists
    await seed_default_project()

async def seed_default_project():
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT project_key FROM projects WHERE project_key = ?", (settings.JIRA_PROJECT_KEY,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                agreed_scope = json.dumps(settings.AGREED_SCOPE)
                team_members = json.dumps(settings.TEAM_MEMBERS)
                await db.execute(
                    "INSERT INTO projects (project_key, name, agreed_scope, team_members) VALUES (?, ?, ?, ?)",
                    (settings.JIRA_PROJECT_KEY, settings.PROJECT_NAME, agreed_scope, team_members)
                )
                await db.commit()

async def get_project(project_key: str = "PM") -> Optional[ProjectContext]:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM projects WHERE project_key = ?", (project_key,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return ProjectContext(
                    project_key=row["project_key"],
                    name=row["name"],
                    agreed_scope=json.loads(row["agreed_scope"]),
                    team_members=json.loads(row["team_members"])
                )
    return None

async def save_meeting(
    meeting_id: str, 
    title: str, 
    meeting_date: str, 
    attendees: str, 
    transcript: str, 
    summary: Optional[str] = None,
    is_private: bool = False
) -> None:
    now = datetime.utcnow().isoformat()
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            """
            INSERT OR REPLACE INTO meetings 
            (id, title, meeting_date, attendees, transcript, summary, is_private, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (meeting_id, title, meeting_date, attendees, transcript, summary, 1 if is_private else 0, now)
        )
        await db.commit()

async def get_meeting(meeting_id: str) -> Optional[Dict[str, Any]]:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(row)
    return None

async def save_drafts_from_llm(meeting_id: str, drafts: MeetingDrafts) -> List[int]:
    """
    Saves generated drafts to SQLite with status='waiting'.
    Strict filter: Discards any draft that lacks a valid source_quote.
    """
    now = datetime.utcnow().isoformat()
    inserted_ids = []

    # Update meeting summary if provided
    if drafts.meeting_summary:
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("UPDATE meetings SET summary = ? WHERE id = ?", (drafts.meeting_summary, meeting_id))
            await db.commit()

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        # 1. Decisions
        for d in drafts.decisions:
            if not d.source_quote or not d.source_quote.strip():
                continue
            cursor = await db.execute(
                """
                INSERT INTO drafts (meeting_id, type, title, proposed_change, source_quote, github_evidence, confidence, status, created_at, updated_at)
                VALUES (?, 'decision', ?, ?, ?, NULL, 1.0, 'waiting', ?, ?)
                """,
                (meeting_id, f"Decision: {d.decision[:60]}", json.dumps(d.model_dump()), d.source_quote, now, now)
            )
            inserted_ids.append(cursor.lastrowid)

        # 2. Action Items
        for a in drafts.action_items:
            if not a.source_quote or not a.source_quote.strip():
                continue
            cursor = await db.execute(
                """
                INSERT INTO drafts (meeting_id, type, title, proposed_change, source_quote, github_evidence, confidence, status, created_at, updated_at)
                VALUES (?, 'action_item', ?, ?, ?, NULL, 1.0, 'waiting', ?, ?)
                """,
                (meeting_id, f"Action: {a.task[:60]} (Owner: {a.owner})", json.dumps(a.model_dump()), a.source_quote, now, now)
            )
            inserted_ids.append(cursor.lastrowid)

        # 3. RAID Items
        for r in drafts.raid_items:
            if not r.source_quote or not r.source_quote.strip():
                continue
            cursor = await db.execute(
                """
                INSERT INTO drafts (meeting_id, type, title, proposed_change, source_quote, github_evidence, confidence, status, created_at, updated_at)
                VALUES (?, 'raid_item', ?, ?, ?, NULL, 1.0, 'waiting', ?, ?)
                """,
                (meeting_id, f"{r.category}: {r.title[:50]}", json.dumps(r.model_dump()), r.source_quote, now, now)
            )
            inserted_ids.append(cursor.lastrowid)

        # 4. Scope Changes
        for s in drafts.scope_changes:
            if not s.source_quote or not s.source_quote.strip():
                continue
            cursor = await db.execute(
                """
                INSERT INTO drafts (meeting_id, type, title, proposed_change, source_quote, github_evidence, confidence, status, created_at, updated_at)
                VALUES (?, 'scope_change', ?, ?, ?, NULL, 1.0, 'waiting', ?, ?)
                """,
                (meeting_id, f"Scope Change: {s.title[:50]}", json.dumps(s.model_dump()), s.source_quote, now, now)
            )
            inserted_ids.append(cursor.lastrowid)

        # 5. Status Updates
        for st in drafts.status_updates:
            if not st.source_quote or not st.source_quote.strip():
                continue
            cursor = await db.execute(
                """
                INSERT INTO drafts (meeting_id, type, title, proposed_change, source_quote, github_evidence, discrepancy_detected, discrepancy_details, confidence, status, created_at, updated_at)
                VALUES (?, 'status_update', ?, ?, ?, ?, ?, ?, ?, 'waiting', ?, ?)
                """,
                (
                    meeting_id,
                    f"Status Update: {st.jira_key} -> {st.proposed_jira_status}",
                    json.dumps(st.model_dump()),
                    st.source_quote,
                    st.github_evidence,
                    1 if st.discrepancy_detected else 0,
                    st.discrepancy_details,
                    st.confidence,
                    now,
                    now
                )
            )
            inserted_ids.append(cursor.lastrowid)

        await db.commit()
    return inserted_ids

async def get_drafts(
    meeting_id: Optional[str] = None,
    status: Optional[str] = None,
    draft_type: Optional[str] = None
) -> List[DraftRecord]:
    query = "SELECT * FROM drafts WHERE 1=1"
    params = []
    if meeting_id:
        query += " AND meeting_id = ?"
        params.append(meeting_id)
    if status:
        query += " AND status = ?"
        params.append(status)
    if draft_type:
        query += " AND type = ?"
        params.append(draft_type)
    
    query += " ORDER BY id ASC"

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [
                DraftRecord(
                    id=row["id"],
                    meeting_id=row["meeting_id"],
                    type=row["type"],
                    title=row["title"],
                    proposed_change=row["proposed_change"],
                    source_quote=row["source_quote"],
                    github_evidence=row["github_evidence"],
                    discrepancy_detected=bool(row["discrepancy_detected"]),
                    discrepancy_details=row["discrepancy_details"],
                    confidence=row["confidence"],
                    status=row["status"],
                    jira_issue_key=row["jira_issue_key"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"]
                )
                for row in rows
            ]

async def get_draft_by_id(draft_id: int) -> Optional[DraftRecord]:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return DraftRecord(
                    id=row["id"],
                    meeting_id=row["meeting_id"],
                    type=row["type"],
                    title=row["title"],
                    proposed_change=row["proposed_change"],
                    source_quote=row["source_quote"],
                    github_evidence=row["github_evidence"],
                    discrepancy_detected=bool(row["discrepancy_detected"]),
                    discrepancy_details=row["discrepancy_details"],
                    confidence=row["confidence"],
                    status=row["status"],
                    jira_issue_key=row["jira_issue_key"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"]
                )
    return None

async def update_draft_status(
    draft_id: int,
    new_status: str,
    user_role: str,
    final_content: Optional[str] = None,
    jira_ticket_id: Optional[str] = None
) -> bool:
    now = datetime.utcnow().isoformat()
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        # Retrieve original draft
        async with db.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)) as cursor:
            draft = await cursor.fetchone()
            if not draft:
                return False

        original_content = draft["proposed_change"]
        content_to_save = final_content if final_content is not None else original_content
        jira_key = jira_ticket_id or draft["jira_issue_key"]

        # Update draft
        await db.execute(
            """
            UPDATE drafts 
            SET status = ?, proposed_change = ?, jira_issue_key = ?, updated_at = ?
            WHERE id = ?
            """,
            (new_status, content_to_save, jira_key, now, draft_id)
        )

        # Record audit log in history (PRD Feature 11)
        await db.execute(
            """
            INSERT INTO history (draft_id, action, user_role, original_content, final_content, jira_ticket_id, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (draft_id, new_status, user_role, original_content, content_to_save, jira_key, now)
        )
        await db.commit()
        return True

async def get_history() -> List[HistoryRecord]:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM history ORDER BY id DESC") as cursor:
            rows = await cursor.fetchall()
            return [
                HistoryRecord(
                    id=row["id"],
                    draft_id=row["draft_id"],
                    action=row["action"],
                    user_role=row["user_role"],
                    original_content=row["original_content"],
                    final_content=row["final_content"],
                    jira_ticket_id=row["jira_ticket_id"],
                    timestamp=row["timestamp"]
                )
                for row in rows
            ]
