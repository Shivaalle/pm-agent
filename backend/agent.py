"""LangGraph pipeline: fetch context → extract grounded drafts → persist → link code."""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from backend.config import settings
from backend.database import (
    get_drafts,
    get_project,
    record_llm_calls,
    record_sla,
    save_drafts_from_llm,
    set_meeting_status,
)
from backend.llm.client import get_llm_client
from backend.llm.providers.gemini import GeminiProvider
from backend.logger import get_logger
from backend.mcp_servers import (
    get_commits,
    get_github_issues,
    get_open_issues,
    get_recent_prs,
)
from backend.models import MeetingDrafts
from backend.services import link_code_activity

logger = get_logger("agent")


SYSTEM_PROMPT = """You are a meticulous project manager's assistant. You read one meeting
transcript and turn only what was explicitly said into grounded draft updates.

CRITICAL RULES (do not deviate):
1. STRICT TRACEABILITY: Every decision, action item, RAID item, scope change and status
   update MUST include a source_quote with the exact line number(s) and verbatim words
   from the transcript, e.g. "Line 14: Alex: I will finish the migrations by Friday".
   Never invent a line or paraphrase inside the quote. A draft without a real quote is
   discarded automatically.
2. NEVER GUESS: If an owner, due date, fact or ticket key was not said, write "MISSING"
   (or "UNMATCHED" for a Jira key). Do not use names from the attendee list unless the
   transcript assigns the work.
3. RELATIVE DATES: Resolve phrases against the supplied meeting date. "by Friday" becomes
   the next Friday's YYYY-MM-DD; "tomorrow" becomes the following day. If the phrase is
   ambiguous ("soon", "next week"), output "MISSING".
4. SCOPE: Compare requests with the agreed scope. Flag a scope change only when the request
   is genuinely outside it, and quote the request and the response confirming it.
5. CODE TRUTH: Map spoken progress to the supplied Jira issues and GitHub activity. A
   "done" claim with an open PR or no merged evidence is a discrepancy: set
   discrepancy_detected=true, explain it, lower confidence. If the Jira match is uncertain,
   use jira_key="UNMATCHED" and confidence below 0.7. Cite the PR/commit in github_evidence.
6. RAID: Classify Risk (might happen), Assumption, Issue (already happening), or Dependency
   (relies on another team/system). If the item updates an existing RAID entry supplied in
   the prompt, set is_update_to_existing=true and give its reference.
7. DECISIONS: Only decisions the group actually agreed, not proposals that were rejected.
8. Do not create drafts for greetings, status chatter, or anything not actionable.
9. Be concise. Prefer a small, high-precision set of drafts over speculation.
"""


class AgentState(TypedDict, total=False):
    meeting_id: str
    project_key: str
    title: str
    meeting_date: str
    numbered_transcript: str
    agreed_scope: List[str]
    jira_context: List[Dict[str, Any]]
    github_prs: List[Dict[str, Any]]
    github_commits: List[Dict[str, Any]]
    github_issues: List[Dict[str, Any]]
    existing_raid: List[Dict[str, Any]]
    context_warnings: List[str]
    generated_drafts: Optional[MeetingDrafts]
    inserted_draft_ids: List[int]
    llm_attempts: List[Dict[str, Any]]
    llm_error: Optional[str]
    llm_provider: Optional[str]
    llm_model: Optional[str]
    _llm_latency_ms: int
    sla_started_at: float
    sla_completed_at: Optional[float]
    code_activity: Dict[str, Any]


async def fetch_context_node(state: AgentState) -> Dict[str, Any]:
    project_key = state["project_key"]
    project = await get_project(project_key)
    agreed_scope = project.agreed_scope if project else settings.AGREED_SCOPE
    warnings: List[str] = []

    async def safe(label: str, coro):
        try:
            return await coro
        except Exception as exc:
            logger.warning("Could not fetch %s: %s", label, exc)
            warnings.append(f"{label}: {exc}")
            return []

    jira_issues = await safe("Jira issues", get_open_issues(project_key))
    github_prs = await safe("GitHub pull requests", get_recent_prs())
    github_commits = await safe("GitHub commits", get_commits())
    github_issues = await safe("GitHub issues", get_github_issues())

    raid_drafts = await get_drafts(project_keys=[project_key], draft_type="raid_item", limit=100)
    existing_raid = [
        {
            "draft_id": draft.id,
            "title": draft.title,
            "status": draft.status,
            "category": draft.proposed_change.get("category"),
            "owner": draft.proposed_change.get("owner"),
        }
        for draft in raid_drafts
    ]

    return {
        "agreed_scope": agreed_scope,
        "jira_context": jira_issues,
        "github_prs": github_prs,
        "github_commits": github_commits,
        "github_issues": github_issues,
        "existing_raid": existing_raid,
        "context_warnings": warnings,
        "sla_started_at": state.get("sla_started_at") or time.time(),
    }


async def draft_and_cross_reference_node(state: AgentState) -> Dict[str, Any]:
    client = get_llm_client()
    if (
        settings.BACKUP_MODEL
        and settings.GEMINI_API_KEY
        and client.provider.name == "gemini"
        and settings.BACKUP_MODEL != settings.PRIMARY_MODEL
    ):
        client.set_backup(GeminiProvider(api_key=settings.GEMINI_API_KEY, model=settings.BACKUP_MODEL))

    prompt = f"""
MEETING METADATA
- Meeting ID: {state['meeting_id']}
- Title: {state['title']}
- Meeting Date: {state['meeting_date']}

AGREED PROJECT SCOPE
{json.dumps(state.get('agreed_scope', []), indent=2)}

ACTIVE JIRA ISSUES
{json.dumps(state.get('jira_context', []), indent=2)}

RECENT GITHUB PULL REQUESTS
{json.dumps(state.get('github_prs', []), indent=2)}

RECENT GITHUB COMMITS
{json.dumps(state.get('github_commits', []), indent=2)}

RECENT GITHUB ISSUES
{json.dumps(state.get('github_issues', []), indent=2)}

EXISTING RAID LOG ENTRIES
{json.dumps(state.get('existing_raid', []), indent=2)}

NUMBERED TRANSCRIPT
{state['numbered_transcript']}

Extract the summary, decisions, action items, RAID proposals, scope changes and status
updates. Use only this transcript and supplied context. Return the schema exactly.
"""
    started = time.perf_counter()
    result = await client.generate_structured(
        system_instruction=SYSTEM_PROMPT,
        user_prompt=prompt,
        response_schema=MeetingDrafts,
        temperature=settings.TEMPERATURE,
    )
    latency_ms = int((time.perf_counter() - started) * 1000)
    attempts = [attempt.model_dump() for attempt in result.attempts]
    if result.ok:
        logger.info(
            "Drafted meeting %s via %s/%s in %d ms",
            state["meeting_id"],
            result.usage.provider if result.usage else "?",
            result.usage.model if result.usage else "?",
            latency_ms,
        )
    else:
        logger.error("AI extraction failed for %s: %s", state["meeting_id"], result.error)

    return {
        "generated_drafts": result.parsed if result.ok else None,
        "llm_attempts": attempts,
        "llm_error": result.error,
        "llm_provider": result.usage.provider if result.usage else client.primary_name,
        "llm_model": result.usage.model if result.usage else client.primary_model,
        "sla_completed_at": time.time(),
        "_llm_latency_ms": latency_ms,
    }


async def save_drafts_node(state: AgentState) -> Dict[str, Any]:
    drafts = state.get("generated_drafts")
    meeting_id = state["meeting_id"]
    project_key = state["project_key"]
    attempts = state.get("llm_attempts", [])
    latency_ms = int(state.get("_llm_latency_ms", 0))  # type: ignore[arg-type]

    if not drafts:
        await set_meeting_status(meeting_id, "failed", state.get("llm_error") or "No AI output")
        await record_llm_calls(meeting_id, project_key, attempts, latency_ms, success=False)
        return {"inserted_draft_ids": []}

    inserted = await save_drafts_from_llm(meeting_id, project_key, state["meeting_date"], drafts)
    await record_llm_calls(meeting_id, project_key, attempts, latency_ms, success=True)

    completed = state.get("sla_completed_at") or time.time()
    started = state.get("sla_started_at") or completed
    duration = completed - started
    if settings.AI_MEASURE_SLA:
        await record_sla(
            meeting_id,
            project_key,
            started,
            completed,
            within_sla=duration <= settings.SLA_SECONDS,
        )
    if not inserted:
        await set_meeting_status(
            meeting_id,
            "failed",
            "AI output contained no drafts with a verified transcript source. Nothing was saved.",
        )
    else:
        await set_meeting_status(meeting_id, "ready")
    return {"inserted_draft_ids": inserted}


async def code_link_node(state: AgentState) -> Dict[str, Any]:
    try:
        result = await link_code_activity(
            project_key=state["project_key"],
            prs=state.get("github_prs") or [],
            commits=state.get("github_commits") or [],
            issues=state.get("github_issues") or [],
            jira_context=state.get("jira_context") or [],
        )
        return {"code_activity": result}
    except Exception as exc:
        logger.warning("Code activity linking failed: %s", exc)
        return {"code_activity": {"error": str(exc)}}


def build_pm_agent_graph():
    workflow = StateGraph(AgentState)
    workflow.add_node("fetch_context", fetch_context_node)
    workflow.add_node("draft", draft_and_cross_reference_node)
    workflow.add_node("save_drafts", save_drafts_node)
    workflow.add_node("link_code", code_link_node)
    workflow.add_edge(START, "fetch_context")
    workflow.add_edge("fetch_context", "draft")
    workflow.add_edge("draft", "save_drafts")
    workflow.add_edge("save_drafts", "link_code")
    workflow.add_edge("link_code", END)
    return workflow.compile()


pm_agent_app = build_pm_agent_graph()
