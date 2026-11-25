"""SQLite persistence for the AI Project Manager Agent.

The schema is deliberately small but relational: projects own meetings, meetings
own transcript lines and drafts, and approved action items become first-class
records that can be tracked, alerted on and forecast. SQLite keeps the demo
zero-config while every query still enforces project access in the API layer.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import aiosqlite

from backend.config import settings
from backend.logger import get_logger
from backend.models import (
    ActionItemRecord,
    CodeActivityRecord,
    CostByMeeting,
    CostSummary,
    DraftRecord,
    ForecastRecord,
    HistoryRecord,
    MeetingDrafts,
    MeetingRecord,
    MeetingSummaryRecord,
    NotificationRecord,
    ProjectContext,
    ProjectMember,
    QualityMetrics,
    SlaSummary,
    TranscriptLine,
    UserRecord,
)

logger = get_logger("database")


# ---------------------------------------------------------------------------
# Connection, schema and migrations
# ---------------------------------------------------------------------------


def get_db() -> aiosqlite.Connection:
    return aiosqlite.connect(str(settings.DB_PATH))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return date.today().isoformat()


def _json_list(value: Any) -> List[str]:
    """Read modern JSON arrays and plain comma-separated legacy values."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    text = str(value).strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
        if isinstance(parsed, str):
            return [parsed]
    except (ValueError, TypeError):
        pass
    return [part.strip() for part in text.split(",") if part.strip()]


SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    project_key TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    agreed_scope TEXT NOT NULL,
    team_members TEXT NOT NULL,
    jira_project_key TEXT,
    github_repo TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL,
    email TEXT,
    role TEXT NOT NULL DEFAULT 'Team Member',
    password_hash TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_members (
    project_key TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (project_key, user_id),
    FOREIGN KEY (project_key) REFERENCES projects(project_key),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS meetings (
    id TEXT PRIMARY KEY,
    project_key TEXT NOT NULL,
    title TEXT NOT NULL,
    meeting_date TEXT NOT NULL,
    attendees TEXT NOT NULL,
    transcript TEXT NOT NULL,
    summary TEXT,
    is_private INTEGER DEFAULT 0,
    series_id TEXT,
    source TEXT DEFAULT 'upload',
    source_url TEXT,
    status TEXT DEFAULT 'ready',
    error TEXT,
    transcript_available_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transcript_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id TEXT NOT NULL,
    line_no INTEGER NOT NULL,
    speaker TEXT NOT NULL,
    spoke_at TEXT,
    text TEXT NOT NULL,
    UNIQUE (meeting_id, line_no),
    FOREIGN KEY (meeting_id) REFERENCES meetings(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_key TEXT NOT NULL,
    meeting_id TEXT NOT NULL,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    proposed_change TEXT NOT NULL,
    source_quote TEXT NOT NULL,
    source_lines TEXT,
    github_evidence TEXT,
    discrepancy_detected INTEGER DEFAULT 0,
    discrepancy_details TEXT,
    confidence REAL DEFAULT 1.0,
    status TEXT DEFAULT 'waiting',
    jira_issue_key TEXT,
    sync_status TEXT DEFAULT 'pending',
    sync_error TEXT,
    sync_attempts INTEGER DEFAULT 0,
    idempotency_key TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (meeting_id) REFERENCES meetings(id)
);

CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER NOT NULL,
    project_key TEXT NOT NULL,
    action TEXT NOT NULL,
    user_name TEXT,
    user_role TEXT NOT NULL,
    original_content TEXT NOT NULL,
    final_content TEXT NOT NULL,
    note TEXT,
    jira_ticket_id TEXT,
    timestamp TEXT NOT NULL,
    FOREIGN KEY (draft_id) REFERENCES drafts(id)
);

CREATE TABLE IF NOT EXISTS action_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_key TEXT NOT NULL,
    meeting_id TEXT,
    draft_id INTEGER,
    description TEXT NOT NULL,
    owner_username TEXT,
    owner_display TEXT,
    due_date TEXT,
    status TEXT DEFAULT 'open',
    priority TEXT DEFAULT 'Medium',
    jira_issue_key TEXT,
    source_quote TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_key TEXT NOT NULL,
    meeting_id TEXT,
    draft_id INTEGER,
    action_item_id INTEGER,
    type TEXT NOT NULL,
    recipient_username TEXT NOT NULL,
    message TEXT NOT NULL,
    link TEXT,
    read INTEGER DEFAULT 0,
    dedupe_key TEXT UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS forecast (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_key TEXT NOT NULL,
    date TEXT NOT NULL,
    on_track INTEGER DEFAULT 1,
    health TEXT DEFAULT 'on_track',
    total_items INTEGER DEFAULT 0,
    open_items INTEGER DEFAULT 0,
    overdue_items INTEGER DEFAULT 0,
    completed_items INTEGER DEFAULT 0,
    notes TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE (project_key, date)
);

CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id TEXT NOT NULL,
    project_key TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    estimated_cost_usd REAL DEFAULT 0.0,
    latency_ms INTEGER DEFAULT 0,
    success INTEGER DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sla_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id TEXT NOT NULL,
    project_key TEXT NOT NULL,
    started_at REAL,
    completed_at REAL,
    duration_seconds REAL,
    within_sla INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS code_activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_key TEXT NOT NULL,
    repo TEXT NOT NULL,
    kind TEXT NOT NULL,
    external_id TEXT NOT NULL,
    title TEXT NOT NULL,
    author TEXT,
    state TEXT,
    url TEXT,
    linked_jira_key TEXT,
    linked_action_item_id INTEGER,
    mismatch INTEGER DEFAULT 0,
    mismatch_details TEXT,
    activity_at TEXT,
    synced_at TEXT NOT NULL,
    UNIQUE (project_key, repo, kind, external_id)
);

CREATE TABLE IF NOT EXISTS private_series (
    series_id TEXT NOT NULL,
    project_key TEXT NOT NULL,
    reason TEXT,
    created_by TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (series_id, project_key)
);

CREATE TABLE IF NOT EXISTS agent_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id TEXT NOT NULL,
    project_key TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT
);
"""

# Indexes are created only after old MVP columns have been added by migrations.
INDEXES = """
CREATE INDEX IF NOT EXISTS idx_drafts_project_status ON drafts(project_key, status);
CREATE INDEX IF NOT EXISTS idx_drafts_meeting ON drafts(meeting_id);
CREATE INDEX IF NOT EXISTS idx_action_items_status_due ON action_items(project_key, status, due_date);
CREATE INDEX IF NOT EXISTS idx_notifications_recipient ON notifications(recipient_username, read);
CREATE INDEX IF NOT EXISTS idx_llm_calls_project ON llm_calls(project_key);
CREATE INDEX IF NOT EXISTS idx_sla_project ON sla_metrics(project_key);
CREATE INDEX IF NOT EXISTS idx_meetings_project ON meetings(project_key, meeting_date);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
"""


async def _table_columns(db: aiosqlite.Connection, table: str) -> set[str]:
    async with db.execute(f"PRAGMA table_info({table})") as cursor:
        rows = await cursor.fetchall()
    return {row[1] for row in rows}


async def _ensure_column(db: aiosqlite.Connection, table: str, column: str, ddl: str) -> None:
    columns = await _table_columns(db, table)
    if column not in columns:
        await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


async def _backfill_transcript_lines(db: aiosqlite.Connection) -> None:
    """Turn transcript text saved by the old MVP into queryable line records."""
    async with db.execute(
        """
        SELECT m.id, m.transcript FROM meetings m
        WHERE m.transcript IS NOT NULL AND m.transcript != ''
          AND NOT EXISTS (SELECT 1 FROM transcript_lines t WHERE t.meeting_id = m.id)
        """
    ) as cursor:
        rows = await cursor.fetchall()
    for meeting_id, transcript in rows:
        records = []
        for index, raw in enumerate((transcript or "").splitlines(), start=1):
            match = re.match(r"\s*Line\s+(\d+):\s*([^:]+):\s*(.*)", raw)
            if match:
                records.append(
                    (meeting_id, int(match.group(1)), match.group(2).strip(), None, match.group(3).strip())
                )
            elif raw.strip():
                records.append((meeting_id, index, "Speaker", None, raw.strip()))
        if records:
            await db.executemany(
                """
                INSERT OR IGNORE INTO transcript_lines (meeting_id, line_no, speaker, spoke_at, text)
                VALUES (?, ?, ?, ?, ?)
                """,
                records,
            )


def hash_password(password: str, salt: Optional[bytes] = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32
    )
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: Optional[str]) -> bool:
    if not stored:
        return False
    try:
        algorithm, salt_hex, digest_hex = stored.split("$", 2)
        if algorithm != "scrypt":
            return False
        candidate = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=2**14,
            r=8,
            p=1,
            dklen=32,
        )
        return hmac.compare_digest(candidate.hex(), digest_hex)
    except Exception:
        return False


def install_seed_database() -> None:
    """Copy the bundled demo database when a fresh instance starts empty.

    Free hosts give the container an empty filesystem, so without this the
    public demo would open with no meetings or drafts. Only runs when
    ``SEED_DEMO_DB`` is enabled and the target database does not exist yet.
    """
    if not settings.SEED_DEMO_DB or settings.APP_ENV == "test":
        return
    if settings.DB_PATH.exists():
        return
    seed_path = Path(__file__).resolve().parents[1] / "data" / "seed_demo.db"
    if not seed_path.exists() or seed_path == settings.DB_PATH:
        return
    settings.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(seed_path, settings.DB_PATH)
    logger.info("Seeded demo database from %s", seed_path)


async def init_db() -> None:
    install_seed_database()
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")
        await db.executescript(SCHEMA)

        # Migrate databases created by the original MVP.
        await _ensure_column(db, "projects", "jira_project_key", "TEXT")
        await _ensure_column(db, "projects", "github_repo", "TEXT")
        await _ensure_column(db, "projects", "created_at", "TEXT")
        await _ensure_column(db, "meetings", "project_key", "TEXT")
        await _ensure_column(db, "meetings", "series_id", "TEXT")
        await _ensure_column(db, "meetings", "source", "TEXT DEFAULT 'upload'")
        await _ensure_column(db, "meetings", "source_url", "TEXT")
        await _ensure_column(db, "meetings", "status", "TEXT DEFAULT 'ready'")
        await _ensure_column(db, "meetings", "error", "TEXT")
        await _ensure_column(db, "meetings", "transcript_available_at", "TEXT")
        await _ensure_column(db, "drafts", "project_key", "TEXT")
        await _ensure_column(db, "drafts", "source_lines", "TEXT")
        await _ensure_column(db, "drafts", "sync_status", "TEXT DEFAULT 'pending'")
        await _ensure_column(db, "drafts", "sync_error", "TEXT")
        await _ensure_column(db, "drafts", "sync_attempts", "INTEGER DEFAULT 0")
        await _ensure_column(db, "drafts", "idempotency_key", "TEXT")
        await _ensure_column(db, "history", "project_key", "TEXT")
        await _ensure_column(db, "history", "user_name", "TEXT")
        await _ensure_column(db, "history", "note", "TEXT")

        project_key = settings.JIRA_PROJECT_KEY
        await db.execute(
            "UPDATE projects SET project_key = ? WHERE project_key IS NULL", (project_key,)
        )
        await db.execute(
            "UPDATE meetings SET project_key = ? WHERE project_key IS NULL", (project_key,)
        )
        await db.execute(
            "UPDATE meetings SET status = 'ready' WHERE status IS NULL"
        )
        await db.execute(
            "UPDATE meetings SET transcript_available_at = created_at WHERE transcript_available_at IS NULL"
        )
        await db.execute(
            "UPDATE drafts SET project_key = ? WHERE project_key IS NULL", (project_key,)
        )
        await db.execute(
            "UPDATE history SET project_key = ? WHERE project_key IS NULL", (project_key,)
        )

        # Older forecast tables did not have the composite uniqueness required
        # by ON CONFLICT. Deduplicate then enforce it for fresh installs too.
        await db.execute(
            """
            DELETE FROM forecast
            WHERE id NOT IN (
                SELECT MAX(id) FROM forecast GROUP BY project_key, date
            )
            """
        )
        try:
            await db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_forecast_unique ON forecast(project_key, date)"
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Could not create forecast unique index: %s", exc)

        await db.executescript(INDEXES)
        await _backfill_transcript_lines(db)
        await db.commit()

    await seed_default_project()
    await seed_default_users()


async def seed_default_project() -> None:
    project_key = settings.JIRA_PROJECT_KEY
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT project_key FROM projects WHERE project_key = ?", (project_key,)
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            await db.execute(
                """
                INSERT INTO projects
                (project_key, name, agreed_scope, team_members, jira_project_key, github_repo, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_key,
                    settings.PROJECT_NAME,
                    json.dumps(settings.AGREED_SCOPE),
                    json.dumps(settings.TEAM_MEMBERS),
                    project_key,
                    settings.GITHUB_REPO or None,
                    _now(),
                ),
            )
        else:
            await db.execute(
                """
                UPDATE projects SET
                    name = ?,
                    agreed_scope = ?,
                    team_members = ?,
                    jira_project_key = COALESCE(jira_project_key, ?),
                    github_repo = COALESCE(github_repo, ?)
                WHERE project_key = ?
                """,
                (
                    settings.PROJECT_NAME,
                    json.dumps(settings.AGREED_SCOPE),
                    json.dumps(settings.TEAM_MEMBERS),
                    project_key,
                    settings.GITHUB_REPO or None,
                    project_key,
                ),
            )
        await db.commit()


async def seed_default_users() -> None:
    defaults = [
        ("sarah", "Sarah Chen", "sarah@company.com", "Project Manager"),
        ("alex", "Alex Rivera", "alex@company.com", "Team Member"),
        ("david", "David Park", "david@company.com", "Team Member"),
        ("elena", "Elena Rossi", "elena@company.com", "Team Member"),
        ("mark", "Mark Johnson", "mark@company.com", "Sponsor"),
        ("admin", "Admin", "admin@company.com", "Admin"),
    ]
    for username, display_name, email, role in defaults:
        existing = await get_user_by_username(username)
        if not existing:
            await create_user(
                username=username,
                display_name=display_name,
                email=email,
                role=role,
                password=settings.DEMO_PASSWORD,
                project_key=settings.JIRA_PROJECT_KEY,
            )
        else:
            async with get_db() as db:
                await db.execute(
                    "UPDATE users SET display_name = COALESCE(NULLIF(display_name, ''), ?), email = COALESCE(email, ?) WHERE username = ?",
                    (display_name, email, username),
                )
                await db.commit()
            if not existing.get("password_hash"):
                async with get_db() as db:
                    await db.execute(
                        "UPDATE users SET password_hash = ? WHERE username = ?",
                        (hash_password(settings.DEMO_PASSWORD), username),
                    )
                    await db.commit()
            await add_project_member(settings.JIRA_PROJECT_KEY, existing["id"])


# ---------------------------------------------------------------------------
# Users, authentication and project access
# ---------------------------------------------------------------------------


def _user_from_row(row: aiosqlite.Row) -> UserRecord:
    return UserRecord(
        id=row["id"],
        username=row["username"],
        display_name=row["display_name"],
        email=row["email"],
        role=row["role"],
        created_at=row["created_at"],
    )


async def create_user(
    username: str,
    display_name: Optional[str] = None,
    email: Optional[str] = None,
    role: str = "Team Member",
    password: Optional[str] = None,
    project_key: Optional[str] = None,
) -> int:
    username = username.strip().lower()
    display_name = (display_name or username.title()).strip()
    password = password or settings.DEMO_PASSWORD
    async with get_db() as db:
        cursor = await db.execute(
            """
            INSERT INTO users (username, display_name, email, role, password_hash, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (username, display_name, email, role, hash_password(password), _now()),
        )
        user_id = cursor.lastrowid
        await db.commit()
    if project_key:
        await add_project_member(project_key, user_id)
    return user_id


async def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def get_user_by_username(username: str) -> Optional[Dict[str, Any]]:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM users WHERE username = ?", (username.strip().lower(),)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def get_users(project_key: Optional[str] = None) -> List[UserRecord]:
    query = "SELECT DISTINCT u.* FROM users u"
    params: List[Any] = []
    if project_key:
        query += " JOIN project_members pm ON pm.user_id = u.id AND pm.project_key = ?"
        params.append(project_key)
    query += " ORDER BY u.id"
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, params) as cursor:
            return [_user_from_row(row) for row in await cursor.fetchall()]


async def authenticate(username: str, password: str) -> Optional[UserRecord]:
    user = await get_user_by_username(username)
    if not user or not verify_password(password, user.get("password_hash")):
        return None
    return _user_from_row_dict(user)


def _user_from_row_dict(row: Dict[str, Any]) -> UserRecord:
    return UserRecord(
        id=row["id"],
        username=row["username"],
        display_name=row["display_name"],
        email=row.get("email"),
        role=row["role"],
        created_at=row["created_at"],
    )


async def create_session(user_id: int) -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=settings.SESSION_TTL_HOURS)).isoformat()
    async with get_db() as db:
        await db.execute(
            "INSERT INTO sessions (token_hash, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
            (token_hash, user_id, expires_at, _now()),
        )
        await db.commit()
    return token, expires_at


async def get_user_by_session(token: str) -> Optional[UserRecord]:
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT u.*, s.expires_at FROM sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token_hash = ?
            """,
            (token_hash,),
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            return None
        try:
            if datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
                await db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
                await db.commit()
                return None
        except ValueError:
            return None
        return _user_from_row(row)


async def delete_session(token: str) -> None:
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    async with get_db() as db:
        await db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
        await db.commit()


async def get_user_projects(user_id: int, is_admin: bool = False) -> List[str]:
    if is_admin:
        async with get_db() as db:
            async with db.execute("SELECT project_key FROM projects ORDER BY project_key") as cursor:
                return [row[0] for row in await cursor.fetchall()]
    async with get_db() as db:
        async with db.execute(
            "SELECT project_key FROM project_members WHERE user_id = ? ORDER BY project_key",
            (user_id,),
        ) as cursor:
            return [row[0] for row in await cursor.fetchall()]


async def user_has_project_access(user_id: int, is_admin: bool, project_key: str) -> bool:
    if is_admin:
        return True
    async with get_db() as db:
        async with db.execute(
            "SELECT 1 FROM project_members WHERE user_id = ? AND project_key = ?",
            (user_id, project_key),
        ) as cursor:
            return await cursor.fetchone() is not None


async def add_project_member(project_key: str, user_id: int) -> None:
    async with get_db() as db:
        await db.execute(
            "INSERT OR IGNORE INTO project_members (project_key, user_id, created_at) VALUES (?, ?, ?)",
            (project_key, user_id, _now()),
        )
        await db.commit()


async def remove_project_member(project_key: str, user_id: int) -> bool:
    async with get_db() as db:
        cursor = await db.execute(
            "DELETE FROM project_members WHERE project_key = ? AND user_id = ?",
            (project_key, user_id),
        )
        await db.commit()
        return cursor.rowcount > 0


async def get_project_members(project_key: str) -> List[ProjectMember]:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT u.id, u.username, u.display_name, u.email, u.role
            FROM project_members pm JOIN users u ON u.id = pm.user_id
            WHERE pm.project_key = ? ORDER BY
                CASE u.role
                    WHEN 'Project Manager' THEN 0
                    WHEN 'Team Member' THEN 1
                    WHEN 'Sponsor' THEN 2
                    ELSE 3
                END, u.display_name
            """,
            (project_key,),
        ) as cursor:
            return [
                ProjectMember(
                    user_id=row["id"],
                    username=row["username"],
                    display_name=row["display_name"],
                    email=row["email"],
                    role=row["role"],
                )
                for row in await cursor.fetchall()
            ]


async def get_project_pm_usernames(project_key: str) -> List[str]:
    async with get_db() as db:
        async with db.execute(
            """
            SELECT u.username FROM project_members pm
            JOIN users u ON u.id = pm.user_id
            WHERE pm.project_key = ? AND u.role = 'Project Manager'
            """,
            (project_key,),
        ) as cursor:
            rows = await cursor.fetchall()
    return [row[0] for row in rows] or ["sarah"]


async def resolve_owner_username(owner: str) -> tuple[Optional[str], Optional[str]]:
    """Map a spoken owner name to a seeded user without guessing ambiguously."""
    if not owner or owner.strip().upper() == "MISSING":
        return None, None
    cleaned = re.sub(r"\s*\(.*?\)\s*", "", owner).strip()
    if not cleaned:
        return None, None
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT username, display_name FROM users
            WHERE lower(username) = lower(?)
               OR lower(display_name) = lower(?)
               OR lower(display_name) LIKE lower(?)
            ORDER BY CASE WHEN lower(username) = lower(?) THEN 0 ELSE 1 END
            LIMIT 1
            """,
            (cleaned, cleaned, f"{cleaned}%", cleaned),
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return row["username"], row["display_name"]
    return None, cleaned


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


async def get_project(project_key: Optional[str] = None) -> Optional[ProjectContext]:
    project_key = project_key or settings.JIRA_PROJECT_KEY
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM projects WHERE project_key = ?", (project_key,)
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            return None
        async with db.execute(
            "SELECT COUNT(*) FROM project_members WHERE project_key = ?", (project_key,)
        ) as cursor:
            member_count = (await cursor.fetchone())[0]
    return ProjectContext(
        project_key=row["project_key"],
        name=row["name"],
        agreed_scope=_json_list(row["agreed_scope"]),
        team_members=_json_list(row["team_members"]),
        jira_project_key=row["jira_project_key"] or project_key,
        github_repo=row["github_repo"] or settings.GITHUB_REPO or None,
        pm_usernames=await get_project_pm_usernames(project_key),
        member_count=member_count,
    )


async def get_projects_for_user(user_id: int, is_admin: bool = False) -> List[ProjectContext]:
    keys = await get_user_projects(user_id, is_admin)
    projects: List[ProjectContext] = []
    for key in keys:
        project = await get_project(key)
        if project:
            projects.append(project)
    return projects


async def create_project(
    project_key: str,
    name: str,
    agreed_scope: Optional[List[str]] = None,
    team_members: Optional[List[str]] = None,
    jira_project_key: Optional[str] = None,
    github_repo: Optional[str] = None,
) -> ProjectContext:
    """Create a new project with its own Jira/GitHub targets."""
    async with get_db() as db:
        async with db.execute(
            "SELECT 1 FROM projects WHERE project_key = ?", (project_key,)
        ) as cursor:
            if await cursor.fetchone():
                raise ValueError(f"Project '{project_key}' already exists")
        await db.execute(
            """
            INSERT INTO projects
            (project_key, name, agreed_scope, team_members, jira_project_key, github_repo, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_key,
                name,
                json.dumps(agreed_scope or []),
                json.dumps(team_members or []),
                jira_project_key or None,
                github_repo or None,
                _now(),
            ),
        )
        await db.commit()
    project = await get_project(project_key)
    assert project is not None
    return project


async def update_project_integrations(
    project_key: str,
    jira_project_key: Optional[str],
    github_repo: Optional[str],
) -> bool:
    """Set the per-project Jira key and GitHub repo used by all connectors.

    Credentials remain global (.env); only the targets vary per project.
    Empty values fall back to the global defaults in ``get_project``.
    """
    async with get_db() as db:
        cursor = await db.execute(
            """
            UPDATE projects
            SET jira_project_key = ?, github_repo = ?
            WHERE project_key = ?
            """,
            (jira_project_key or None, github_repo or None, project_key),
        )
        await db.commit()
        return cursor.rowcount > 0


# ---------------------------------------------------------------------------
# Meetings and transcripts
# ---------------------------------------------------------------------------


def _number_transcript(lines: Iterable[TranscriptLine]) -> str:
    return "\n".join(
        f"Line {line.line}: {line.speaker}: {line.text}" for line in sorted(lines, key=lambda x: x.line)
    )


async def is_series_private(series_id: Optional[str], project_key: str) -> bool:
    if not series_id:
        return False
    async with get_db() as db:
        async with db.execute(
            "SELECT 1 FROM private_series WHERE series_id = ? AND project_key = ?",
            (series_id, project_key),
        ) as cursor:
            return await cursor.fetchone() is not None


async def save_meeting(
    meeting_id: str,
    project_key: str,
    title: str,
    meeting_date: str,
    attendees: List[str],
    transcript_lines: List[TranscriptLine],
    summary: Optional[str] = None,
    is_private: bool = False,
    series_id: Optional[str] = None,
    source: str = "upload",
    source_url: Optional[str] = None,
    transcript_available_at: Optional[str] = None,
    status: str = "ready",
    error: Optional[str] = None,
) -> None:
    if is_private:
        # Defence in depth: a private meeting never reaches persistence.
        raise ValueError("Private meetings must not be stored")
    transcript_text = _number_transcript(transcript_lines)
    available_at = transcript_available_at or _now()
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            """
            INSERT INTO meetings
            (id, project_key, title, meeting_date, attendees, transcript, summary, is_private,
             series_id, source, source_url, status, error, transcript_available_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title = excluded.title,
                meeting_date = excluded.meeting_date,
                attendees = excluded.attendees,
                transcript = excluded.transcript,
                summary = COALESCE(excluded.summary, meetings.summary),
                series_id = excluded.series_id,
                source = excluded.source,
                source_url = excluded.source_url,
                status = excluded.status,
                error = excluded.error,
                transcript_available_at = excluded.transcript_available_at
            """,
            (
                meeting_id,
                project_key,
                title,
                meeting_date,
                json.dumps(attendees),
                transcript_text,
                summary,
                series_id,
                source,
                source_url,
                status,
                error,
                available_at,
                _now(),
            ),
        )
        await db.execute("DELETE FROM transcript_lines WHERE meeting_id = ?", (meeting_id,))
        await db.executemany(
            """
            INSERT INTO transcript_lines (meeting_id, line_no, speaker, spoke_at, text)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (meeting_id, line.line, line.speaker, line.spoke_at, line.text)
                for line in sorted(transcript_lines, key=lambda x: x.line)
            ],
        )
        await db.commit()


async def get_transcript_lines(meeting_id: str) -> List[TranscriptLine]:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT line_no, speaker, spoke_at, text FROM transcript_lines WHERE meeting_id = ? ORDER BY line_no",
            (meeting_id,),
        ) as cursor:
            return [
                TranscriptLine(
                    line=row["line_no"],
                    speaker=row["speaker"],
                    text=row["text"],
                    spoke_at=row["spoke_at"],
                )
                for row in await cursor.fetchall()
            ]


def _meeting_from_row(row: aiosqlite.Row, draft_count: int = 0) -> MeetingSummaryRecord:
    return MeetingSummaryRecord(
        id=row["id"],
        project_key=row["project_key"],
        title=row["title"],
        meeting_date=row["meeting_date"],
        attendees=_json_list(row["attendees"]),
        summary=row["summary"],
        is_private=bool(row["is_private"]),
        series_id=row["series_id"],
        source=row["source"] or "upload",
        status=row["status"] or "ready",
        draft_count=draft_count,
        created_at=row["created_at"],
    )


async def get_meeting(meeting_id: str, include_transcript: bool = False) -> Optional[MeetingRecord]:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)) as cursor:
            row = await cursor.fetchone()
        if not row:
            return None
        async with db.execute(
            "SELECT COUNT(*) FROM drafts WHERE meeting_id = ?", (meeting_id,)
        ) as cursor:
            draft_count = (await cursor.fetchone())[0]
    summary = _meeting_from_row(row, draft_count)
    lines = await get_transcript_lines(meeting_id) if include_transcript else []
    return MeetingRecord(
        **summary.model_dump(),
        error=row["error"],
        transcript_available_at=row["transcript_available_at"],
        transcript_lines=lines,
    )


async def get_meetings(project_keys: List[str], limit: int = 100) -> List[MeetingSummaryRecord]:
    if not project_keys:
        return []
    placeholders = ",".join("?" for _ in project_keys)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"""
            SELECT m.*, (SELECT COUNT(*) FROM drafts d WHERE d.meeting_id = m.id) AS draft_count
            FROM meetings m WHERE m.project_key IN ({placeholders})
            ORDER BY m.meeting_date DESC, m.created_at DESC LIMIT ?
            """,
            (*project_keys, limit),
        ) as cursor:
            return [_meeting_from_row(row, row["draft_count"]) for row in await cursor.fetchall()]


async def set_meeting_status(meeting_id: str, status: str, error: Optional[str] = None) -> None:
    async with get_db() as db:
        await db.execute(
            "UPDATE meetings SET status = ?, error = ? WHERE id = ?", (status, error, meeting_id)
        )
        await db.commit()


async def set_meeting_summary(meeting_id: str, summary: str) -> None:
    async with get_db() as db:
        await db.execute("UPDATE meetings SET summary = ? WHERE id = ?", (summary, meeting_id))
        await db.commit()


async def purge_meeting(meeting_id: str) -> None:
    """Remove everything derived from a meeting when it is marked private."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id FROM action_items WHERE meeting_id = ?", (meeting_id,)
        ) as cursor:
            action_item_ids = [row[0] for row in await cursor.fetchall()]
        for action_item_id in action_item_ids:
            await db.execute(
                "UPDATE code_activity SET linked_action_item_id = NULL WHERE linked_action_item_id = ?",
                (action_item_id,),
            )
        await db.execute("DELETE FROM transcript_lines WHERE meeting_id = ?", (meeting_id,))
        await db.execute("DELETE FROM action_items WHERE meeting_id = ?", (meeting_id,))
        async with db.execute("SELECT id FROM drafts WHERE meeting_id = ?", (meeting_id,)) as cursor:
            draft_ids = [row["id"] for row in await cursor.fetchall()]
        for draft_id in draft_ids:
            await db.execute("DELETE FROM history WHERE draft_id = ?", (draft_id,))
        await db.execute("DELETE FROM notifications WHERE meeting_id = ?", (meeting_id,))
        await db.execute("DELETE FROM drafts WHERE meeting_id = ?", (meeting_id,))
        await db.execute("DELETE FROM meetings WHERE id = ?", (meeting_id,))
        await db.commit()


async def record_agent_run(
    meeting_id: str, project_key: str, status: str, error: Optional[str] = None
) -> None:
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO agent_runs (meeting_id, project_key, status, error, started_at, completed_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (meeting_id, project_key, status, error, _now(), _now() if status != "running" else None),
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Source traceability and date normalisation
# ---------------------------------------------------------------------------


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def _tokens(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", text.lower()) if len(token) > 2}


def validate_source_quote(quote: str, lines: Dict[int, TranscriptLine]) -> List[int]:
    """Return cited line numbers only when the quote really comes from them.

    LLMs occasionally invent a plausible line number. We require at least one
    cited line whose text substantially appears in the quote (or vice versa),
    otherwise the draft is discarded (PRD: traceable / no broken output).
    """
    if not quote or not quote.strip():
        return []
    normalised_quote = _normalise(quote)
    cited = [int(value) for value in re.findall(r"line\s+(\d+)", quote, re.I)]
    candidates = cited or list(lines.keys())
    matched: List[int] = []
    for line_no in candidates:
        line = lines.get(line_no)
        if not line:
            continue
        line_text = _normalise(line.text)
        if line_text and (line_text in normalised_quote or normalised_quote in line_text):
            matched.append(line_no)
            continue
        quote_tokens = _tokens(normalised_quote)
        line_tokens = _tokens(line_text)
        if line_tokens:
            overlap = len(quote_tokens & line_tokens) / len(line_tokens)
            if overlap >= 0.65:
                matched.append(line_no)
    return sorted(set(matched))


_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def normalise_due_date(value: str, meeting_date: str) -> str:
    """Resolve common relative phrases against the meeting date, never guess."""
    raw = (value or "").strip()
    if not raw or raw.upper() == "MISSING":
        return "MISSING"
    try:
        date.fromisoformat(raw)
        return raw
    except ValueError:
        pass
    try:
        base = date.fromisoformat(meeting_date)
    except ValueError:
        return "MISSING"
    text = raw.lower()
    for name, weekday in _WEEKDAYS.items():
        if name in text:
            delta = (weekday - base.weekday()) % 7
            if delta == 0 and not text.startswith("next"):
                delta = 7
            return (base + timedelta(days=delta)).isoformat()
    if "tomorrow" in text:
        return (base + timedelta(days=1)).isoformat()
    if "today" in text:
        return base.isoformat()
    if "next week" in text or "end of week" in text or "end of month" in text:
        return "MISSING"
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if match:
        return match.group(0)
    match = re.search(r"\b(\d{1,2})[/-](\d{1,2})\b", raw)
    if match:
        try:
            return date(base.year, int(match.group(1)), int(match.group(2))).isoformat()
        except ValueError:
            return "MISSING"
    return "MISSING"


# ---------------------------------------------------------------------------
# Drafts
# ---------------------------------------------------------------------------


async def save_drafts_from_llm(
    meeting_id: str, project_key: str, meeting_date: str, drafts: MeetingDrafts
) -> List[int]:
    """Save only schema-valid, transcript-grounded drafts in `waiting` state."""
    lines = {line.line: line for line in await get_transcript_lines(meeting_id)}
    if not lines:
        logger.warning("Meeting %s has no transcript lines; refusing to save drafts", meeting_id)
        return []
    now = _now()
    inserted: List[int] = []
    await set_meeting_summary(meeting_id, drafts.meeting_summary)

    rows: List[tuple[Any, ...]] = []

    def add(
        draft_type: str,
        title: str,
        payload: Dict[str, Any],
        quote: str,
        evidence: Optional[str] = None,
        discrepancy: bool = False,
        details: Optional[str] = None,
        confidence: float = 1.0,
    ) -> None:
        source_lines = validate_source_quote(quote, lines)
        if not source_lines:
            logger.warning("Discarding %s draft without a verified source quote", draft_type)
            return
        key = hashlib.sha256(
            f"{meeting_id}|{draft_type}|{title}|{quote}".encode("utf-8")
        ).hexdigest()[:24]
        rows.append(
            (
                project_key,
                meeting_id,
                draft_type,
                title,
                json.dumps(payload, ensure_ascii=False),
                quote,
                json.dumps(source_lines),
                evidence,
                1 if discrepancy else 0,
                details,
                confidence,
                "waiting",
                "pending",
                f"ai-pm-{key}",
                now,
                now,
            )
        )

    for item in drafts.decisions:
        add(
            "decision",
            f"Decision: {item.decision[:70]}",
            item.model_dump(),
            item.source_quote,
        )
    for item in drafts.action_items:
        due = normalise_due_date(item.due_date, meeting_date)
        if due == "MISSING":
            # Models often quote "by Friday" but omit the resolved date field.
            # Pull the date phrase out of the verified quote rather than guessing.
            match = re.search(
                r"\b(?:by|before|on|due)\s+([A-Za-z0-9 ,/-]{3,40})",
                item.source_quote,
                re.I,
            )
            if match:
                due = normalise_due_date(match.group(1).strip(" .,;"), meeting_date)
        payload = item.model_dump()
        payload["due_date"] = due
        owner = item.owner if item.owner and item.owner.upper() != "MISSING" else "MISSING"
        add(
            "action_item",
            f"Action: {item.task[:60]} (Owner: {owner})",
            payload,
            item.source_quote,
        )
    for item in drafts.raid_items:
        add(
            "raid_item",
            f"{item.category}: {item.title[:55]}",
            item.model_dump(),
            item.source_quote,
        )
    for item in drafts.scope_changes:
        add(
            "scope_change",
            f"Scope change: {item.title[:55]}",
            item.model_dump(),
            item.source_quote,
        )
    for item in drafts.status_updates:
        add(
            "status_update",
            f"Status update: {item.jira_key} -> {item.proposed_jira_status}",
            item.model_dump(),
            item.source_quote,
            evidence=item.github_evidence,
            discrepancy=item.discrepancy_detected,
            details=item.discrepancy_details,
            confidence=item.confidence,
        )

    if not rows:
        return []
    async with get_db() as db:
        # Re-processing the same meeting replaces its pending drafts, while
        # approved/rejected records and their audit trail are preserved.
        async with db.execute(
            "SELECT id FROM drafts WHERE meeting_id = ? AND status = 'waiting'", (meeting_id,)
        ) as cursor:
            stale_ids = [row[0] for row in await cursor.fetchall()]
        for stale_id in stale_ids:
            await db.execute("DELETE FROM history WHERE draft_id = ?", (stale_id,))
        await db.execute(
            "DELETE FROM drafts WHERE meeting_id = ? AND status = 'waiting'", (meeting_id,)
        )
        await db.executemany(
            """
            INSERT INTO drafts
            (project_key, meeting_id, type, title, proposed_change, source_quote, source_lines,
             github_evidence, discrepancy_detected, discrepancy_details, confidence, status,
             sync_status, idempotency_key, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        await db.commit()
        # aiosqlite does not expose lastrowid for executemany; query by meeting.
        async with db.execute(
            "SELECT id FROM drafts WHERE meeting_id = ? ORDER BY id", (meeting_id,)
        ) as read_cursor:
            inserted = [row[0] for row in await read_cursor.fetchall()]
    logger.info("Saved %d grounded drafts for meeting %s", len(inserted), meeting_id)
    return inserted


def _draft_from_row(row: aiosqlite.Row) -> DraftRecord:
    try:
        payload = json.loads(row["proposed_change"])
        if not isinstance(payload, dict):
            payload = {"value": payload}
    except Exception:
        payload = {"value": row["proposed_change"]}
    try:
        source_lines = json.loads(row["source_lines"] or "[]")
    except Exception:
        source_lines = []
    return DraftRecord(
        id=row["id"],
        project_key=row["project_key"] or settings.JIRA_PROJECT_KEY,
        meeting_id=row["meeting_id"],
        meeting_title=row["meeting_title"] if "meeting_title" in row.keys() else None,
        type=row["type"],
        title=row["title"],
        proposed_change=payload,
        source_quote=row["source_quote"],
        source_lines=source_lines,
        github_evidence=row["github_evidence"],
        discrepancy_detected=bool(row["discrepancy_detected"]),
        discrepancy_details=row["discrepancy_details"],
        confidence=row["confidence"],
        status=row["status"],
        jira_issue_key=row["jira_issue_key"],
        sync_status=row["sync_status"] or "pending",
        sync_error=row["sync_error"],
        sync_attempts=row["sync_attempts"] or 0,
        idempotency_key=row["idempotency_key"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def get_drafts(
    project_keys: Optional[List[str]] = None,
    meeting_id: Optional[str] = None,
    status: Optional[str] = None,
    draft_type: Optional[str] = None,
    limit: int = 500,
) -> List[DraftRecord]:
    query = """
        SELECT d.*, m.title AS meeting_title
        FROM drafts d LEFT JOIN meetings m ON m.id = d.meeting_id
        WHERE 1=1
    """
    params: List[Any] = []
    if project_keys is not None:
        if not project_keys:
            return []
        query += f" AND d.project_key IN ({','.join('?' for _ in project_keys)})"
        params.extend(project_keys)
    if meeting_id:
        query += " AND d.meeting_id = ?"
        params.append(meeting_id)
    if status:
        query += " AND d.status = ?"
        params.append(status)
    if draft_type:
        query += " AND d.type = ?"
        params.append(draft_type)
    query += " ORDER BY d.id ASC LIMIT ?"
    params.append(limit)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, params) as cursor:
            return [_draft_from_row(row) for row in await cursor.fetchall()]


async def get_draft_by_id(draft_id: int) -> Optional[DraftRecord]:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT d.*, m.title AS meeting_title FROM drafts d
            LEFT JOIN meetings m ON m.id = d.meeting_id WHERE d.id = ?
            """,
            (draft_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return _draft_from_row(row) if row else None


async def _record_history(
    db: aiosqlite.Connection,
    draft_id: int,
    project_key: str,
    action: str,
    user_name: Optional[str],
    user_role: str,
    original_content: str,
    final_content: str,
    note: Optional[str] = None,
    jira_ticket_id: Optional[str] = None,
) -> None:
    await db.execute(
        """
        INSERT INTO history
        (draft_id, project_key, action, user_name, user_role, original_content, final_content,
         note, jira_ticket_id, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            draft_id,
            project_key,
            action,
            user_name,
            user_role,
            original_content,
            final_content,
            note,
            jira_ticket_id,
            _now(),
        ),
    )


async def edit_draft(
    draft_id: int,
    proposed_change: Dict[str, Any] | str,
    user_name: str,
    user_role: str,
    note: Optional[str] = None,
) -> bool:
    content = (
        proposed_change
        if isinstance(proposed_change, str)
        else json.dumps(proposed_change, ensure_ascii=False)
    )
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)) as cursor:
            draft = await cursor.fetchone()
        if not draft or draft["status"] not in {"waiting", "edited"}:
            return False
        await db.execute(
            "UPDATE drafts SET proposed_change = ?, status = 'edited', updated_at = ? WHERE id = ?",
            (content, _now(), draft_id),
        )
        await _record_history(
            db,
            draft_id,
            draft["project_key"],
            "edited",
            user_name,
            user_role,
            draft["proposed_change"],
            content,
            note,
        )
        await db.commit()
        return True


async def approve_draft_record(
    draft_id: int,
    user_name: str,
    user_role: str,
    jira_issue_key: Optional[str],
    sync_status: str,
    sync_error: Optional[str],
) -> bool:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)) as cursor:
            draft = await cursor.fetchone()
        if not draft:
            return False
        await db.execute(
            """
            UPDATE drafts SET status = 'approved', jira_issue_key = COALESCE(?, jira_issue_key),
                sync_status = ?, sync_error = ?, updated_at = ?
            WHERE id = ?
            """,
            (jira_issue_key, sync_status, sync_error, _now(), draft_id),
        )
        await _record_history(
            db,
            draft_id,
            draft["project_key"],
            "approved",
            user_name,
            user_role,
            draft["proposed_change"],
            draft["proposed_change"],
            jira_ticket_id=jira_issue_key,
        )
        await db.commit()
        return True


async def record_sync_attempt(
    draft_id: int, sync_status: str, jira_issue_key: Optional[str], error: Optional[str]
) -> None:
    async with get_db() as db:
        await db.execute(
            """
            UPDATE drafts SET sync_status = ?, jira_issue_key = COALESCE(?, jira_issue_key),
                sync_error = ?, sync_attempts = sync_attempts + 1, updated_at = ?
            WHERE id = ?
            """,
            (sync_status, jira_issue_key, error, _now(), draft_id),
        )
        await db.commit()


async def record_sync_history(
    draft_id: int,
    user_name: str,
    user_role: str,
    jira_issue_key: Optional[str],
    note: str,
) -> None:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)) as cursor:
            draft = await cursor.fetchone()
        if not draft:
            return
        await _record_history(
            db,
            draft_id,
            draft["project_key"],
            "retried",
            user_name,
            user_role,
            draft["proposed_change"],
            draft["proposed_change"],
            note,
            jira_issue_key,
        )
        await db.commit()


async def reject_draft(
    draft_id: int, user_name: str, user_role: str, reason: Optional[str] = None
) -> bool:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)) as cursor:
            draft = await cursor.fetchone()
        if not draft or draft["status"] == "approved":
            return False
        await db.execute(
            "UPDATE drafts SET status = 'rejected', updated_at = ? WHERE id = ?",
            (_now(), draft_id),
        )
        await _record_history(
            db,
            draft_id,
            draft["project_key"],
            "rejected",
            user_name,
            user_role,
            draft["proposed_change"],
            draft["proposed_change"],
            reason,
        )
        await db.commit()
        return True


async def get_history(project_keys: Optional[List[str]] = None, limit: int = 300) -> List[HistoryRecord]:
    query = "SELECT * FROM history WHERE 1=1"
    params: List[Any] = []
    if project_keys is not None:
        if not project_keys:
            return []
        query += f" AND project_key IN ({','.join('?' for _ in project_keys)})"
        params.extend(project_keys)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, params) as cursor:
            return [
                HistoryRecord(
                    id=row["id"],
                    draft_id=row["draft_id"],
                    project_key=row["project_key"] or settings.JIRA_PROJECT_KEY,
                    action=row["action"],
                    user_name=row["user_name"],
                    user_role=row["user_role"],
                    original_content=row["original_content"],
                    final_content=row["final_content"],
                    note=row["note"],
                    jira_ticket_id=row["jira_ticket_id"],
                    timestamp=row["timestamp"],
                )
                for row in await cursor.fetchall()
            ]


# ---------------------------------------------------------------------------
# Action items
# ---------------------------------------------------------------------------


def _action_item_from_row(row: aiosqlite.Row) -> ActionItemRecord:
    return ActionItemRecord(
        id=row["id"],
        project_key=row["project_key"],
        meeting_id=row["meeting_id"],
        draft_id=row["draft_id"],
        description=row["description"],
        owner_username=row["owner_username"],
        owner_display=row["owner_display"],
        due_date=row["due_date"],
        status=row["status"],
        priority=row["priority"],
        jira_issue_key=row["jira_issue_key"],
        source_quote=row["source_quote"] or "",
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def create_action_item(
    project_key: str,
    meeting_id: Optional[str],
    draft_id: Optional[int],
    description: str,
    owner_display: Optional[str],
    due_date: Optional[str],
    priority: str,
    source_quote: str,
    jira_issue_key: Optional[str] = None,
) -> int:
    owner_username, resolved_display = await resolve_owner_username(owner_display or "MISSING")
    now = _now()
    async with get_db() as db:
        async with db.execute(
            "SELECT id FROM action_items WHERE draft_id = ?", (draft_id,)
        ) as cursor:
            existing = await cursor.fetchone()
        if existing:
            await db.execute(
                """
                UPDATE action_items SET description = ?, owner_username = ?, owner_display = ?,
                    due_date = ?, priority = ?, jira_issue_key = COALESCE(?, jira_issue_key), updated_at = ?
                WHERE id = ?
                """,
                (
                    description,
                    owner_username,
                    resolved_display or owner_display,
                    due_date if due_date and due_date != "MISSING" else None,
                    priority,
                    jira_issue_key,
                    now,
                    existing[0],
                ),
            )
            await db.commit()
            return existing[0]
        cursor = await db.execute(
            """
            INSERT INTO action_items
            (project_key, meeting_id, draft_id, description, owner_username, owner_display,
             due_date, status, priority, jira_issue_key, source_quote, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?)
            """,
            (
                project_key,
                meeting_id,
                draft_id,
                description,
                owner_username,
                resolved_display or (owner_display if owner_display != "MISSING" else None),
                due_date if due_date and due_date != "MISSING" else None,
                priority,
                jira_issue_key,
                source_quote,
                now,
                now,
            ),
        )
        await db.commit()
        return cursor.lastrowid


async def get_action_items(
    project_keys: List[str],
    owner_username: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 500,
) -> List[ActionItemRecord]:
    if not project_keys:
        return []
    query = f"SELECT * FROM action_items WHERE project_key IN ({','.join('?' for _ in project_keys)})"
    params: List[Any] = list(project_keys)
    if owner_username:
        query += " AND owner_username = ?"
        params.append(owner_username)
    if status:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY CASE WHEN due_date IS NULL THEN 1 ELSE 0 END, due_date, id"
    params.append(limit)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query + " LIMIT ?", params) as cursor:
            return [_action_item_from_row(row) for row in await cursor.fetchall()]


async def get_action_item(action_item_id: int) -> Optional[ActionItemRecord]:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM action_items WHERE id = ?", (action_item_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return _action_item_from_row(row) if row else None


async def update_action_item_status(action_item_id: int, status: str) -> bool:
    async with get_db() as db:
        cursor = await db.execute(
            "UPDATE action_items SET status = ?, updated_at = ? WHERE id = ?",
            (status, _now(), action_item_id),
        )
        await db.commit()
        return cursor.rowcount > 0


async def get_overdue_action_items(project_key: str) -> List[ActionItemRecord]:
    today = _today()
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT * FROM action_items
            WHERE project_key = ? AND status IN ('open', 'in_progress')
              AND due_date IS NOT NULL AND due_date < ?
            ORDER BY due_date
            """,
            (project_key, today),
        ) as cursor:
            return [_action_item_from_row(row) for row in await cursor.fetchall()]


async def set_action_item_jira_key(action_item_id: int, jira_issue_key: str) -> None:
    async with get_db() as db:
        await db.execute(
            "UPDATE action_items SET jira_issue_key = ?, updated_at = ? WHERE id = ?",
            (jira_issue_key, _now(), action_item_id),
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


async def create_notification(
    project_key: str,
    ntype: str,
    recipient_username: str,
    message: str,
    meeting_id: Optional[str] = None,
    draft_id: Optional[int] = None,
    action_item_id: Optional[int] = None,
    link: Optional[str] = None,
    dedupe_key: Optional[str] = None,
) -> int:
    async with get_db() as db:
        cursor = await db.execute(
            """
            INSERT INTO notifications
            (project_key, meeting_id, draft_id, action_item_id, type, recipient_username,
             message, link, read, dedupe_key, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            ON CONFLICT(dedupe_key) DO NOTHING
            """,
            (
                project_key,
                meeting_id,
                draft_id,
                action_item_id,
                ntype,
                recipient_username,
                message,
                link,
                dedupe_key,
                _now(),
            ),
        )
        await db.commit()
        return cursor.lastrowid or 0


async def get_notifications(
    recipient_username: str, unread_only: bool = False, limit: int = 100
) -> List[NotificationRecord]:
    query = "SELECT * FROM notifications WHERE recipient_username = ?"
    params: List[Any] = [recipient_username]
    if unread_only:
        query += " AND read = 0"
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, params) as cursor:
            return [
                NotificationRecord(
                    id=row["id"],
                    project_key=row["project_key"],
                    meeting_id=row["meeting_id"],
                    draft_id=row["draft_id"],
                    action_item_id=row["action_item_id"],
                    type=row["type"],
                    recipient_username=row["recipient_username"],
                    message=row["message"],
                    link=row["link"],
                    read=bool(row["read"]),
                    created_at=row["created_at"],
                )
                for row in await cursor.fetchall()
            ]


async def mark_notification_read(notification_id: int, recipient_username: str) -> bool:
    async with get_db() as db:
        cursor = await db.execute(
            "UPDATE notifications SET read = 1 WHERE id = ? AND recipient_username = ?",
            (notification_id, recipient_username),
        )
        await db.commit()
        return cursor.rowcount > 0


async def mark_all_notifications_read(recipient_username: str) -> int:
    async with get_db() as db:
        cursor = await db.execute(
            "UPDATE notifications SET read = 1 WHERE recipient_username = ? AND read = 0",
            (recipient_username,),
        )
        await db.commit()
        return cursor.rowcount


# ---------------------------------------------------------------------------
# Forecast
# ---------------------------------------------------------------------------


def _forecast_from_row(row: aiosqlite.Row) -> ForecastRecord:
    return ForecastRecord(
        project_key=row["project_key"],
        date=row["date"],
        on_track=bool(row["on_track"]),
        health=row["health"],
        total_items=row["total_items"],
        open_items=row["open_items"],
        overdue_items=row["overdue_items"],
        completed_items=row["completed_items"],
        notes=row["notes"],
        updated_at=row["updated_at"],
    )


async def recompute_forecast(project_key: str) -> ForecastRecord:
    today = _today()
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN status IN ('open','in_progress') THEN 1 ELSE 0 END) AS open,
                   SUM(CASE WHEN status = 'done' THEN 1 ELSE 0 END) AS done,
                   SUM(CASE WHEN status IN ('open','in_progress') AND due_date IS NOT NULL
                            AND due_date < ? THEN 1 ELSE 0 END) AS overdue
            FROM action_items WHERE project_key = ?
            """,
            (today, project_key),
        ) as cursor:
            row = await cursor.fetchone()
        total = row["total"] or 0
        open_items = row["open"] or 0
        done = row["done"] or 0
        overdue = row["overdue"] or 0
        if overdue:
            health, on_track = "off_track", False
        elif open_items and done == 0:
            health, on_track = "at_risk", True
        else:
            health, on_track = "on_track", True
        notes = (
            f"{overdue} overdue item(s), {open_items} open of {total}."
            if total
            else "No approved action items yet."
        )
        await db.execute(
            """
            INSERT INTO forecast
            (project_key, date, on_track, health, total_items, open_items, overdue_items,
             completed_items, notes, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_key, date) DO UPDATE SET
                on_track = excluded.on_track,
                health = excluded.health,
                total_items = excluded.total_items,
                open_items = excluded.open_items,
                overdue_items = excluded.overdue_items,
                completed_items = excluded.completed_items,
                notes = excluded.notes,
                updated_at = excluded.updated_at
            """,
            (
                project_key,
                today,
                1 if on_track else 0,
                health,
                total,
                open_items,
                overdue,
                done,
                notes,
                _now(),
            ),
        )
        await db.commit()
        async with db.execute(
            "SELECT * FROM forecast WHERE project_key = ? AND date = ?", (project_key, today)
        ) as cursor:
            saved = await cursor.fetchone()
    return _forecast_from_row(saved)


async def get_forecast(project_key: str, date_value: Optional[str] = None) -> ForecastRecord:
    date_value = date_value or _today()
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM forecast WHERE project_key = ? AND date = ?",
            (project_key, date_value),
        ) as cursor:
            row = await cursor.fetchone()
    if row:
        return _forecast_from_row(row)
    return await recompute_forecast(project_key)


async def get_forecast_history(project_key: str, limit: int = 30) -> List[ForecastRecord]:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM forecast WHERE project_key = ? ORDER BY date DESC LIMIT ?",
            (project_key, limit),
        ) as cursor:
            return [_forecast_from_row(row) for row in await cursor.fetchall()]


# ---------------------------------------------------------------------------
# LLM cost and SLA
# ---------------------------------------------------------------------------


async def record_llm_calls(
    meeting_id: str,
    project_key: str,
    attempts: List[Dict[str, Any]],
    latency_ms: int,
    success: bool,
) -> None:
    if not attempts:
        return
    async with get_db() as db:
        await db.executemany(
            """
            INSERT INTO llm_calls
            (meeting_id, project_key, provider, model, prompt_tokens, completion_tokens,
             total_tokens, estimated_cost_usd, latency_ms, success, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    meeting_id,
                    project_key,
                    attempt.get("provider", "unknown"),
                    attempt.get("model", "unknown"),
                    int(attempt.get("prompt_tokens", 0) or 0),
                    int(attempt.get("completion_tokens", 0) or 0),
                    int(attempt.get("total_tokens", 0) or 0),
                    float(attempt.get("estimated_cost_usd", 0.0) or 0.0),
                    latency_ms,
                    1 if success else 0,
                    _now(),
                )
                for attempt in attempts
            ],
        )
        await db.commit()


async def get_llm_cost_summary(project_keys: List[str]) -> CostSummary:
    if not project_keys:
        return CostSummary()
    placeholders = ",".join("?" for _ in project_keys)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"""
            SELECT SUM(estimated_cost_usd) AS total_cost, COUNT(*) AS calls,
                   SUM(total_tokens) AS total_tokens, AVG(estimated_cost_usd) AS avg_cost
            FROM llm_calls WHERE project_key IN ({placeholders})
            """,
            project_keys,
        ) as cursor:
            totals = await cursor.fetchone()
        async with db.execute(
            f"""
            SELECT l.meeting_id, l.project_key,
                   COALESCE(m.title, l.meeting_id) AS meeting_title,
                   SUM(l.estimated_cost_usd) AS cost,
                   COUNT(*) AS calls, SUM(l.total_tokens) AS total_tokens
            FROM llm_calls l LEFT JOIN meetings m ON m.id = l.meeting_id
            WHERE l.project_key IN ({placeholders})
            GROUP BY l.meeting_id, l.project_key ORDER BY cost DESC LIMIT 20
            """,
            project_keys,
        ) as cursor:
            by_meeting = [
                CostByMeeting(
                    meeting_id=row["meeting_id"],
                    meeting_title=row["meeting_title"],
                    project_key=row["project_key"],
                    cost_usd=row["cost"] or 0.0,
                    calls=row["calls"] or 0,
                    total_tokens=row["total_tokens"] or 0,
                )
                for row in await cursor.fetchall()
            ]
        async with db.execute(
            f"""
            SELECT project_key, SUM(estimated_cost_usd) AS cost, COUNT(*) AS calls
            FROM llm_calls WHERE project_key IN ({placeholders}) GROUP BY project_key
            """,
            project_keys,
        ) as cursor:
            by_project = [
                {"project_key": row["project_key"], "cost_usd": row["cost"] or 0.0, "calls": row["calls"]}
                for row in await cursor.fetchall()
            ]
        async with db.execute(
            f"""
            SELECT provider, model, SUM(estimated_cost_usd) AS cost, COUNT(*) AS calls,
                   SUM(total_tokens) AS total_tokens
            FROM llm_calls WHERE project_key IN ({placeholders})
            GROUP BY provider, model ORDER BY cost DESC
            """,
            project_keys,
        ) as cursor:
            by_model = [
                {
                    "provider": row["provider"],
                    "model": row["model"],
                    "cost_usd": row["cost"] or 0.0,
                    "calls": row["calls"],
                    "total_tokens": row["total_tokens"] or 0,
                }
                for row in await cursor.fetchall()
            ]
        async with db.execute(
            f"SELECT COUNT(DISTINCT meeting_id) FROM llm_calls WHERE project_key IN ({placeholders})",
            project_keys,
        ) as cursor:
            meeting_count = (await cursor.fetchone())[0] or 0
    total_cost = totals["total_cost"] or 0.0
    return CostSummary(
        total_cost_usd=total_cost,
        calls=totals["calls"] or 0,
        total_tokens=totals["total_tokens"] or 0,
        avg_cost_usd=totals["avg_cost"] or 0.0,
        avg_cost_per_meeting_usd=(total_cost / meeting_count) if meeting_count else 0.0,
        by_meeting=by_meeting,
        by_project=by_project,
        by_model=by_model,
    )


async def record_sla(
    meeting_id: str,
    project_key: str,
    started_at: float,
    completed_at: float,
    within_sla: bool,
) -> None:
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO sla_metrics
            (meeting_id, project_key, started_at, completed_at, duration_seconds, within_sla, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                meeting_id,
                project_key,
                started_at,
                completed_at,
                completed_at - started_at,
                1 if within_sla else 0,
                _now(),
            ),
        )
        await db.commit()


async def get_sla_summary(project_keys: List[str]) -> SlaSummary:
    if not project_keys:
        return SlaSummary()
    placeholders = ",".join("?" for _ in project_keys)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"""
            SELECT duration_seconds, within_sla FROM sla_metrics
            WHERE project_key IN ({placeholders}) ORDER BY id DESC
            """,
            project_keys,
        ) as cursor:
            rows = await cursor.fetchall()
    if not rows:
        return SlaSummary()
    durations = sorted(row["duration_seconds"] or 0.0 for row in rows)
    within = sum(1 for row in rows if row["within_sla"])
    index = min(len(durations) - 1, int(round(0.95 * (len(durations) - 1))))
    return SlaSummary(
        total_meetings=len(rows),
        within_sla=within,
        sla_compliance_pct=(within / len(rows)) * 100,
        avg_duration_seconds=sum(durations) / len(durations),
        p95_duration_seconds=durations[index],
        last_duration_seconds=rows[0]["duration_seconds"] or 0.0,
    )


async def get_quality_metrics(project_keys: List[str]) -> QualityMetrics:
    if not project_keys:
        return QualityMetrics()
    placeholders = ",".join("?" for _ in project_keys)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"""
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN status = 'waiting' THEN 1 ELSE 0 END) AS waiting,
                   SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) AS approved,
                   SUM(CASE WHEN status = 'edited' THEN 1 ELSE 0 END) AS edited,
                   SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) AS rejected,
                   SUM(CASE WHEN discrepancy_detected = 1 THEN 1 ELSE 0 END) AS discrepancies,
                   SUM(CASE WHEN type = 'scope_change' THEN 1 ELSE 0 END) AS scope_changes
            FROM drafts WHERE project_key IN ({placeholders})
            """,
            project_keys,
        ) as cursor:
            d = await cursor.fetchone()
        async with db.execute(
            "SELECT COUNT(*) FROM meetings WHERE project_key IN (" + placeholders + ")",
            project_keys,
        ) as cursor:
            meetings = (await cursor.fetchone())[0]
        async with db.execute(
            f"SELECT COUNT(*) AS total, SUM(CASE WHEN status IN ('open','in_progress') AND due_date < ? THEN 1 ELSE 0 END) AS overdue FROM action_items WHERE project_key IN ({placeholders})",
            [_today(), *project_keys],
        ) as cursor:
            a = await cursor.fetchone()
        async with db.execute(
            f"SELECT COUNT(*) FROM history WHERE project_key IN ({placeholders}) AND action = 'approved'",
            project_keys,
        ) as cursor:
            approvals = (await cursor.fetchone())[0]
        async with db.execute(
            f"SELECT COUNT(*) FROM history WHERE project_key IN ({placeholders}) AND action = 'edited'",
            project_keys,
        ) as cursor:
            edits = (await cursor.fetchone())[0]
    approved = d["approved"] or 0
    # Approval-without-edit uses recorded edit history, which is the honest
    # measure when a draft can be edited before approval.
    approved_without_edit = max(approved - edits, 0)
    return QualityMetrics(
        drafts_total=d["total"] or 0,
        drafts_waiting=d["waiting"] or 0,
        drafts_approved=approved,
        drafts_edited=d["edited"] or 0,
        drafts_rejected=d["rejected"] or 0,
        approved_without_edit_pct=(approved_without_edit / approved * 100) if approved else 1.0,
        action_items_total=a["total"] or 0,
        action_items_overdue=a["overdue"] or 0,
        scope_changes=d["scope_changes"] or 0,
        discrepancies=d["discrepancies"] or 0,
        meetings=meetings or 0,
    )


# ---------------------------------------------------------------------------
# Code activity
# ---------------------------------------------------------------------------


def _code_activity_from_row(row: aiosqlite.Row) -> CodeActivityRecord:
    return CodeActivityRecord(
        id=row["id"],
        project_key=row["project_key"],
        repo=row["repo"],
        kind=row["kind"],
        external_id=row["external_id"],
        title=row["title"],
        author=row["author"],
        state=row["state"],
        url=row["url"],
        linked_jira_key=row["linked_jira_key"],
        linked_action_item_id=row["linked_action_item_id"],
        mismatch=bool(row["mismatch"]),
        mismatch_details=row["mismatch_details"],
        activity_at=row["activity_at"],
        synced_at=row["synced_at"],
    )


async def upsert_code_activity(
    project_key: str,
    repo: str,
    kind: str,
    external_id: str,
    title: str,
    author: Optional[str],
    state: Optional[str],
    url: Optional[str],
    linked_jira_key: Optional[str],
    linked_action_item_id: Optional[int],
    mismatch: bool,
    mismatch_details: Optional[str],
    activity_at: Optional[str],
) -> None:
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO code_activity
            (project_key, repo, kind, external_id, title, author, state, url, linked_jira_key,
             linked_action_item_id, mismatch, mismatch_details, activity_at, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_key, repo, kind, external_id) DO UPDATE SET
                title = excluded.title,
                author = excluded.author,
                state = excluded.state,
                url = excluded.url,
                linked_jira_key = excluded.linked_jira_key,
                linked_action_item_id = excluded.linked_action_item_id,
                mismatch = excluded.mismatch,
                mismatch_details = excluded.mismatch_details,
                activity_at = excluded.activity_at,
                synced_at = excluded.synced_at
            """,
            (
                project_key,
                repo,
                kind,
                external_id,
                title,
                author,
                state,
                url,
                linked_jira_key,
                linked_action_item_id,
                1 if mismatch else 0,
                mismatch_details,
                activity_at,
                _now(),
            ),
        )
        await db.commit()


async def get_code_activity(project_keys: List[str], limit: int = 100) -> List[CodeActivityRecord]:
    if not project_keys:
        return []
    placeholders = ",".join("?" for _ in project_keys)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"SELECT * FROM code_activity WHERE project_key IN ({placeholders}) ORDER BY activity_at DESC LIMIT ?",
            (*project_keys, limit),
        ) as cursor:
            return [_code_activity_from_row(row) for row in await cursor.fetchall()]


# ---------------------------------------------------------------------------
# Privacy series
# ---------------------------------------------------------------------------


async def add_private_series(
    series_id: str, project_key: str, reason: Optional[str], created_by: str
) -> None:
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO private_series (series_id, project_key, reason, created_by, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(series_id, project_key) DO UPDATE SET reason = excluded.reason
            """,
            (series_id, project_key, reason, created_by, _now()),
        )
        await db.commit()


async def remove_private_series(series_id: str, project_key: str) -> bool:
    async with get_db() as db:
        cursor = await db.execute(
            "DELETE FROM private_series WHERE series_id = ? AND project_key = ?",
            (series_id, project_key),
        )
        await db.commit()
        return cursor.rowcount > 0


async def get_private_series(project_keys: List[str]) -> List[Dict[str, Any]]:
    if not project_keys:
        return []
    placeholders = ",".join("?" for _ in project_keys)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"SELECT * FROM private_series WHERE project_key IN ({placeholders}) ORDER BY created_at DESC",
            project_keys,
        ) as cursor:
            return [dict(row) for row in await cursor.fetchall()]


async def get_meeting_series(meeting_id: str) -> tuple[Optional[str], Optional[str]]:
    async with get_db() as db:
        async with db.execute(
            "SELECT series_id, project_key FROM meetings WHERE id = ?", (meeting_id,)
        ) as cursor:
            row = await cursor.fetchone()
    return (row[0], row[1]) if row else (None, None)
