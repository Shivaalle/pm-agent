"""Measured accuracy harness for the meeting-to-draft pipeline.

The harness sends each golden transcript through the *real* FastAPI pipeline
(auth → ingest → agent → grounding filter → SQLite) against a throwaway
database, then compares the saved drafts with human-labelled expectations.

Default mode is deterministic: the explicit mock LLM and offline connectors.
Use ``--provider gemini`` (or openai_compatible) for a live, measured run:

    python -m evals.run --provider gemini
    python -m evals.run --provider mock --out evals/results/mock.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
DEFAULT_OUT = Path(__file__).resolve().parent / "results" / "latest.json"


def _tokens(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower())


def draft_text(draft: Dict[str, Any]) -> str:
    payload = json.dumps(draft.get("proposed_change") or {}, ensure_ascii=False)
    return _tokens(f"{draft.get('title', '')} {payload}")


def _score(expected: Dict[str, Any], draft: Dict[str, Any]) -> int:
    target_type = expected.get("type")
    if target_type and target_type != draft["type"]:
        return 0
    haystack = draft_text(draft)
    required = [_tokens(term) for term in expected.get("match_all", [])]
    if any(term not in haystack for term in required):
        return 0
    return 1 + sum(1 for term in expected.get("match_any", []) if _tokens(term) in haystack)


def match_expected(
    expected_items: Iterable[Dict[str, Any]], drafts: List[Dict[str, Any]]
) -> Tuple[List[Tuple[Dict[str, Any], Optional[Dict[str, Any]]]], List[Dict[str, Any]]]:
    remaining = list(drafts)
    matched: List[Tuple[Dict[str, Any], Optional[Dict[str, Any]]]] = []
    for expected in expected_items:
        best: Optional[Dict[str, Any]] = None
        best_score = 0
        for draft in remaining:
            score = _score(expected, draft)
            if score > best_score:
                best_score = score
                best = draft
        if best is not None:
            remaining.remove(best)
        matched.append((expected, best))
    return matched, remaining


def owner_matches(expected: str, actual: Optional[str]) -> bool:
    if (expected or "").upper() == "MISSING":
        return (actual or "").upper() == "MISSING"
    expected_tokens = set(_tokens(expected).split())
    actual_tokens = set(_tokens(actual or "").split())
    return bool(expected_tokens) and expected_tokens <= actual_tokens


def date_matches(expected: str, actual: Optional[str]) -> bool:
    if (expected or "").upper() == "MISSING":
        return (actual or "").upper() == "MISSING"
    return (actual or "")[:10] == expected[:10]


def _safe_div(numerator: float, denominator: float) -> Optional[float]:
    return round(numerator / denominator, 4) if denominator else None


def compute_metrics(
    fixtures: List[Dict[str, Any]],
    drafts_by_fixture: Dict[str, List[Dict[str, Any]]],
    latencies: Dict[str, float],
) -> Dict[str, Any]:
    totals = {
        "expected_actions": 0,
        "actions_matched": 0,
        "action_drafts": 0,
        "actions_matched_precision": 0,
        "owners_expected": 0,
        "owners_correct": 0,
        "dates_expected": 0,
        "dates_correct": 0,
        "expected_decisions": 0,
        "decisions_matched": 0,
        "expected_raids": 0,
        "raids_matched": 0,
        "expected_scopes": 0,
        "scopes_matched": 0,
        "scope_drafts": 0,
        "scope_matched_precision": 0,
        "expected_statuses": 0,
        "statuses_matched": 0,
        "drafts": 0,
        "cited_drafts": 0,
    }
    per_meeting: List[Dict[str, Any]] = []

    for fixture in fixtures:
        drafts = drafts_by_fixture[fixture["id"]]
        expected = fixture.get("expected", {})
        action_drafts = [draft for draft in drafts if draft["type"] == "action_item"]
        matched, unmatched_actions = match_expected(expected.get("action_items", []), action_drafts)

        owner_ok = 0
        owner_checked = 0
        date_ok = 0
        date_checked = 0
        for expectation, draft in matched:
            if draft is None:
                continue
            payload = draft.get("proposed_change") or {}
            if expectation.get("owner"):
                owner_checked += 1
                if owner_matches(expectation["owner"], payload.get("owner")):
                    owner_ok += 1
            if expectation.get("due_date"):
                date_checked += 1
                if date_matches(expectation["due_date"], payload.get("due_date")):
                    date_ok += 1

        decision_matches, _ = match_expected(
            expected.get("decisions", []),
            [draft for draft in drafts if draft["type"] == "decision"],
        )
        raid_matches, _ = match_expected(
            expected.get("raid_items", []),
            [draft for draft in drafts if draft["type"] == "raid_item"],
        )
        scope_drafts = [draft for draft in drafts if draft["type"] == "scope_change"]
        scope_matches, unmatched_scopes = match_expected(
            expected.get("scope_changes", []), scope_drafts
        )
        status_matches, _ = match_expected(
            expected.get("status_updates", []),
            [draft for draft in drafts if draft["type"] == "status_update"],
        )

        matched_actions = sum(1 for _, draft in matched if draft is not None)
        matched_scopes = sum(1 for _, draft in scope_matches if draft is not None)
        cited = [draft for draft in drafts if draft.get("source_lines")]
        totals["expected_actions"] += len(expected.get("action_items", []))
        totals["actions_matched"] += matched_actions
        totals["action_drafts"] += len(action_drafts)
        totals["actions_matched_precision"] += len(action_drafts) - len(unmatched_actions)
        totals["owners_expected"] += owner_checked
        totals["owners_correct"] += owner_ok
        totals["dates_expected"] += date_checked
        totals["dates_correct"] += date_ok
        totals["expected_decisions"] += len(expected.get("decisions", []))
        totals["decisions_matched"] += sum(1 for _, draft in decision_matches if draft)
        totals["expected_raids"] += len(expected.get("raid_items", []))
        totals["raids_matched"] += sum(1 for _, draft in raid_matches if draft)
        totals["expected_scopes"] += len(expected.get("scope_changes", []))
        totals["scopes_matched"] += matched_scopes
        totals["scope_drafts"] += len(scope_drafts)
        totals["scope_matched_precision"] += len(scope_drafts) - len(unmatched_scopes)
        totals["expected_statuses"] += len(expected.get("status_updates", []))
        totals["statuses_matched"] += sum(1 for _, draft in status_matches if draft)
        totals["drafts"] += len(drafts)
        totals["cited_drafts"] += len(cited)

        per_meeting.append(
            {
                "id": fixture["id"],
                "title": fixture["title"],
                "latency_seconds": round(latencies.get(fixture["id"], 0.0), 2),
                "drafts": len(drafts),
                "draft_types": {
                    kind: len([draft for draft in drafts if draft["type"] == kind])
                    for kind in ("decision", "action_item", "raid_item", "scope_change", "status_update")
                },
                "action_recall": _safe_div(matched_actions, len(expected.get("action_items", []))),
                "owner_accuracy": _safe_div(owner_ok, owner_checked),
                "due_date_accuracy": _safe_div(date_ok, date_checked),
                "scope_recall": _safe_div(matched_scopes, len(expected.get("scope_changes", []))),
                "missing_actions": [
                    expectation["label"]
                    for expectation, draft in matched
                    if draft is None and expectation.get("label")
                ],
                "unexpected_actions": [
                    draft["title"] for draft in unmatched_actions
                ],
                "unexpected_scope_changes": [
                    draft["title"] for draft in unmatched_scopes
                ],
            }
        )

    return {
        "action_item_recall": _safe_div(totals["actions_matched"], totals["expected_actions"]),
        "action_item_precision": _safe_div(
            totals["actions_matched_precision"], totals["action_drafts"]
        ),
        "owner_accuracy": _safe_div(totals["owners_correct"], totals["owners_expected"]),
        "due_date_accuracy": _safe_div(totals["dates_correct"], totals["dates_expected"]),
        "decision_recall": _safe_div(totals["decisions_matched"], totals["expected_decisions"]),
        "raid_recall": _safe_div(totals["raids_matched"], totals["expected_raids"]),
        "scope_change_recall": _safe_div(totals["scopes_matched"], totals["expected_scopes"]),
        "scope_change_precision": _safe_div(
            totals["scope_matched_precision"], totals["scope_drafts"]
        ),
        "status_update_recall": _safe_div(
            totals["statuses_matched"], totals["expected_statuses"]
        ),
        "citation_coverage": _safe_div(totals["cited_drafts"], totals["drafts"]),
        "totals": totals,
        "meetings": per_meeting,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--provider",
        default="mock",
        choices=["mock", "gemini", "openai_compatible", "configured"],
        help="LLM provider for the run (default: explicit mock)",
    )
    parser.add_argument(
        "--live-connectors",
        action="store_true",
        help="Use live Jira/GitHub context instead of deterministic offline fixtures",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    if args.provider != "configured":
        os.environ["AI_PROVIDER"] = args.provider
    if not args.live_connectors:
        os.environ.update(
            {
                "JIRA_BASE_URL": "",
                "JIRA_EMAIL": "",
                "JIRA_API_TOKEN": "",
                "GITHUB_TOKEN": "",
                "GITHUB_REPO": "",
                "TEAMS_ACCESS_TOKEN": "",
                "CONNECTORS_ALLOW_MOCK": "true",
            }
        )
    tmpdir = tempfile.mkdtemp(prefix="pm-agent-eval-")
    os.environ["PM_DB_PATH"] = str(Path(tmpdir) / "eval.db")
    os.environ["SCHEDULER_ENABLED"] = "false"
    os.environ["APP_ENV"] = "test"

    # Imports must happen after environment configuration.
    from fastapi.testclient import TestClient

    from backend.config import settings
    from backend.main import app

    if not args.live_connectors:
        settings.JIRA_BASE_URL = ""
        settings.JIRA_EMAIL = ""
        settings.JIRA_API_TOKEN = ""
        settings.GITHUB_TOKEN = ""
        settings.GITHUB_REPO = ""
        settings.TEAMS_ACCESS_TOKEN = ""
        settings.TEAMS_TENANT_ID = ""
        settings.TEAMS_CLIENT_ID = ""
        settings.TEAMS_CLIENT_SECRET = ""

    fixtures = []
    for path in sorted(GOLDEN_DIR.glob("*.json")):
        fixtures.append(json.loads(path.read_text(encoding="utf-8")))

    drafts_by_fixture: Dict[str, List[Dict[str, Any]]] = {}
    latencies: Dict[str, float] = {}
    run_id = uuid.uuid4().hex[:8]
    started = time.time()

    with TestClient(app) as client:
        login = client.post(
            "/auth/login", json={"username": "sarah", "password": "demo1234"}
        )
        login.raise_for_status()
        headers = {"Authorization": f"Bearer {login.json()['token']}"}

        for fixture in fixtures:
            meeting_id = f"EVAL-{fixture['id'].upper()}-{run_id}"
            payload = {
                "meeting_id": meeting_id,
                "project_key": fixture.get("project_key", settings.JIRA_PROJECT_KEY),
                "title": fixture["title"],
                "meeting_date": fixture["meeting_date"],
                "attendees": fixture.get("attendees", []),
                "transcript": fixture["transcript"],
            }
            upload_started = time.perf_counter()
            response = client.post("/meetings/upload", headers=headers, json=payload)
            latencies[fixture["id"]] = time.perf_counter() - upload_started
            if response.status_code != 200 or response.json().get("status") != "success":
                print(
                    f"FAILED {fixture['id']}: {response.status_code} {response.text[:400]}",
                    file=sys.stderr,
                )
                return 1
            drafts = client.get(
                f"/drafts?project_key={payload['project_key']}&meeting_id={meeting_id}",
                headers=headers,
            ).json()
            drafts_by_fixture[fixture["id"]] = drafts

    metrics = compute_metrics(fixtures, drafts_by_fixture, latencies)
    result = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "provider": settings.AI_PROVIDER,
        "model": settings.PRIMARY_MODEL if settings.AI_PROVIDER == "gemini" else settings.AI_PROVIDER,
        "live_connectors": args.live_connectors,
        "meetings": len(fixtures),
        "duration_seconds": round(time.time() - started, 2),
        "metrics": metrics,
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2), encoding="utf-8")

    print(json.dumps({k: result[k] for k in ("provider", "model", "meetings", "duration_seconds")}, indent=2))
    print(json.dumps({k: metrics[k] for k in metrics if k not in {"totals", "meetings"}}, indent=2))
    print(f"Result written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
