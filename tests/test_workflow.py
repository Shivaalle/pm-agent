"""Citation grounding, approval gate, retry-safety and privacy tests."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

from fastapi.testclient import TestClient

from backend.database import (
    get_drafts,
    get_meeting,
    get_transcript_lines,
    save_drafts_from_llm,
    save_meeting,
)
from backend.models import ActionItem, MeetingDrafts, TranscriptLine
from tests.conftest import auth


def _seed_meeting(meeting_id: str, project_key: str = "KAN") -> None:
    async def run() -> None:
        await save_meeting(
            meeting_id=meeting_id,
            project_key=project_key,
            title="Grounding test",
            meeting_date="2026-11-20",
            attendees=["Sarah", "Alex"],
            transcript_lines=[
                TranscriptLine(line=1, speaker="Sarah", text="Let's review the sprint."),
                TranscriptLine(
                    line=2,
                    speaker="Alex",
                    text="I will finish the migrations by Friday.",
                ),
                TranscriptLine(
                    line=3,
                    speaker="Sarah",
                    text="Thanks. No other changes today.",
                ),
            ],
        )

    asyncio.run(run())


def test_invented_quotes_are_discarded(project_key: str):
    meeting_id = "TEST-GROUNDING"
    _seed_meeting(meeting_id, project_key)

    llm_output = MeetingDrafts(
        meeting_summary="A short sync.",
        action_items=[
            ActionItem(
                task="Finish the migrations",
                owner="Alex",
                due_date="by Friday",
                source_quote="Line 2: Alex: I will finish the migrations by Friday.",
            ),
            ActionItem(
                task="Delete the production database",
                owner="Alex",
                due_date="MISSING",
                source_quote="Line 99: Nobody: I will delete the production database.",
            ),
        ],
    )
    saved = asyncio.run(
        save_drafts_from_llm(meeting_id, project_key, "2026-11-20", llm_output)
    )
    assert len(saved) == 1

    draft = asyncio.run(get_drafts(project_keys=[project_key], meeting_id=meeting_id))[0]
    assert draft.type == "action_item"
    assert draft.source_lines == [2]
    # Friday 2026-11-20 met on Friday resolves to the next Friday.
    assert draft.proposed_change["due_date"] == "2026-11-27"
    assert "delete" not in draft.title.lower()


def test_no_jira_write_before_approval(client: TestClient, pm_token: str):
    meeting_id = "TEST-APPROVAL-GATE"
    _seed_meeting(meeting_id)
    asyncio.run(
        save_drafts_from_llm(
            meeting_id,
            "KAN",
            "2026-11-20",
            MeetingDrafts(
                meeting_summary="Approval gate.",
                action_items=[
                    ActionItem(
                        task="Finish the migrations",
                        owner="Alex",
                        due_date="2026-11-27",
                        source_quote="Line 2: Alex: I will finish the migrations by Friday.",
                    )
                ],
            ),
        )
    )
    drafts = asyncio.run(get_drafts(project_keys=["KAN"], meeting_id=meeting_id))
    draft = drafts[0]
    assert draft.status == "waiting"
    assert draft.jira_issue_key is None
    history = client.get("/history?project_key=KAN", headers=auth(pm_token)).json()
    assert all(record["draft_id"] != draft.id for record in history)

    approved = client.post(f"/drafts/{draft.id}/approve", headers=auth(pm_token))
    assert approved.status_code == 200
    result = approved.json()
    assert result["status"] == "approved"
    assert result["sync_status"] == "synced"
    assert result["jira_issue_key"]
    assert result["action_item_id"]

    history = client.get("/history?project_key=KAN", headers=auth(pm_token)).json()
    approval_records = [
        record for record in history if record["draft_id"] == draft.id and record["action"] == "approved"
    ]
    assert approval_records
    assert approval_records[0]["user_name"] == "Sarah Chen"

    # A repeated approval must be a no-op, not a second Jira issue.
    repeated = client.post(f"/drafts/{draft.id}/approve", headers=auth(pm_token))
    assert repeated.status_code == 200
    assert repeated.json()["status"] == "already_approved"
    items = client.get("/action-items?project_key=KAN", headers=auth(pm_token)).json()
    assert len([item for item in items if item["draft_id"] == draft.id]) == 1


def test_failed_sync_is_retryable_without_new_approval(client: TestClient, pm_token: str):
    meeting_id = "TEST-RETRY"
    _seed_meeting(meeting_id)
    asyncio.run(
        save_drafts_from_llm(
            meeting_id,
            "KAN",
            "2026-11-20",
            MeetingDrafts(
                meeting_summary="Retry safety.",
                decisions=[
                    {
                        "decision": "Adopt the new migration runner",
                        "rationale": "Team agreement.",
                        "source_quote": "Line 2: Alex: I will finish the migrations by Friday.",
                    }
                ],
            ),
        )
    )
    draft = asyncio.run(get_drafts(project_keys=["KAN"], meeting_id=meeting_id))[0]
    approved = client.post(f"/drafts/{draft.id}/approve", headers=auth(pm_token)).json()
    assert approved["sync_status"] == "synced"

    # Simulate a later transport failure on an already-approved draft.
    async def force_failure() -> None:
        from backend.database import record_sync_attempt

        await record_sync_attempt(draft.id, "failed", None, "simulated Jira outage")

    asyncio.run(force_failure())
    retried = client.post(f"/drafts/{draft.id}/retry", headers=auth(pm_token))
    assert retried.status_code == 200
    assert retried.json()["sync_status"] == "synced"
    assert retried.json()["jira_issue_key"]


def test_private_meeting_is_never_stored(client: TestClient, pm_token: str):
    response = client.post(
        "/meetings/upload",
        headers=auth(pm_token),
        json={
            "meeting_id": "TEST-PRIVATE-UPLOAD",
            "project_key": "KAN",
            "title": "Sensitive 1:1",
            "meeting_date": "2026-11-20",
            "is_private": True,
            "transcript": [{"line": 1, "speaker": "Sarah", "text": "Confidential."}],
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "skipped"
    assert asyncio.run(get_meeting("TEST-PRIVATE-UPLOAD")) is None


def test_private_series_blocks_future_ingestion(client: TestClient, pm_token: str):
    series = "series-test-private"
    blocked = client.post(
        f"/series/{series}/private",
        headers=auth(pm_token),
        json={"project_key": "KAN", "reason": "Personnel matters"},
    )
    assert blocked.status_code == 200

    response = client.post(
        "/meetings/upload",
        headers=auth(pm_token),
        json={
            "meeting_id": "TEST-PRIVATE-SERIES",
            "project_key": "KAN",
            "title": "Weekly standup",
            "meeting_date": "2026-11-20",
            "series_id": series,
            "transcript": [{"line": 1, "speaker": "Sarah", "text": "Confidential series."}],
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "skipped"
    assert asyncio.run(get_meeting("TEST-PRIVATE-SERIES")) is None


def test_marking_meeting_private_purges_transcript_and_drafts(
    client: TestClient, pm_token: str
):
    meeting_id = "TEST-PURGE"
    _seed_meeting(meeting_id)
    asyncio.run(
        save_drafts_from_llm(
            meeting_id,
            "KAN",
            "2026-11-20",
            MeetingDrafts(
                meeting_summary="Will be purged.",
                action_items=[
                    ActionItem(
                        task="Finish the migrations",
                        owner="Alex",
                        due_date="2026-11-27",
                        source_quote="Line 2: Alex: I will finish the migrations by Friday.",
                    )
                ],
            ),
        )
    )
    assert asyncio.run(get_drafts(project_keys=["KAN"], meeting_id=meeting_id))

    response = client.post(
        f"/meetings/{meeting_id}/private",
        headers=auth(pm_token),
        json={"reason": "Sensitive"},
    )
    assert response.status_code == 200
    assert asyncio.run(get_meeting(meeting_id)) is None
    assert asyncio.run(get_transcript_lines(meeting_id)) == []
    assert asyncio.run(get_drafts(project_keys=["KAN"], meeting_id=meeting_id)) == []


def test_deadline_alerts_are_deduplicated_and_forecast_reacts(
    client: TestClient, pm_token: str
):
    meeting_id = "TEST-DEADLINE"
    _seed_meeting(meeting_id)
    overdue = (date.today() - timedelta(days=2)).isoformat()
    asyncio.run(
        save_drafts_from_llm(
            meeting_id,
            "KAN",
            "2026-11-20",
            MeetingDrafts(
                meeting_summary="Deadline test.",
                action_items=[
                    ActionItem(
                        task="Ship the overdue migration",
                        owner="Alex",
                        due_date=overdue,
                        source_quote="Line 2: Alex: I will finish the migrations by Friday.",
                    )
                ],
            ),
        )
    )
    draft = asyncio.run(get_drafts(project_keys=["KAN"], meeting_id=meeting_id))[0]
    approved = client.post(f"/drafts/{draft.id}/approve", headers=auth(pm_token)).json()
    assert approved["action_item_id"]

    first = client.post("/alerts/check?project_key=KAN", headers=auth(pm_token))
    assert first.status_code == 200
    assert first.json()["alerts_created"] >= 1
    assert first.json()["overdue_items"] >= 1

    second = client.post("/alerts/check?project_key=KAN", headers=auth(pm_token))
    assert second.status_code == 200
    assert second.json()["alerts_created"] == 0

    sarah_notes = client.get("/notifications", headers=auth(pm_token)).json()
    alex_notes = client.get(
        "/notifications", headers=auth(client.post("/auth/login", json={"username": "alex", "password": "demo1234"}).json()["token"])
    ).json()
    assert any(note["type"] == "deadline_alert" for note in sarah_notes)
    assert any(note["type"] == "deadline_alert" for note in alex_notes)

    item_id = approved["action_item_id"]
    done = client.patch(
        f"/action-items/{item_id}", headers=auth(pm_token), json={"status": "done"}
    )
    assert done.status_code == 200
    forecast = client.get("/forecast?project_key=KAN", headers=auth(pm_token)).json()
    assert forecast["completed_items"] >= 1
