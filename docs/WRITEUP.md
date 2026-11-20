# AI Project Manager Agent — System Write-Up

### 1. The Core Problem
On enterprise engineering teams, real project status is spoken informally in meetings, but it rarely flows accurately into the plan. Getting an LLM to generate generic meeting summaries is trivial; the hard problem is building an agent that enterprise project managers can actually trust. That requires reconciling messy human conversation ("it's basically done", "by Friday") against deterministic code truth without hallucinating unstated owners, inventing fake dates, or silently corrupting Jira with unverified updates.

---

### 2. Our Approach
```
[ Meeting Transcript ] ──► [ Node 1: Fetch Live Context ] ──► [ Node 2: Extract & Audit ] ──► [ Node 3: Grounded Staging ]
(Teams / WebVTT / JSON)    (Jira issues + GitHub PRs via MCP) (Gemini + Pydantic Schema)       (Source-quote verified)
                                                                                                        │
                                                                                                        ▼
[ Live Jira Writeback ] ◄── [ Human Approval Gate ] ◄── [ React Review Queue ] ◄── [ SQLite agent.db ]
(Issue / Comment / Status)   (PM: Approve / Edit / Reject)   (Discrepancy warnings)             (Audit history & telemetry)
```

The obvious alternative was a single prompt that reads the transcript and writes directly to the Jira API. We rejected this because monolithic prompts cannot cross-audit verbal claims against live code activity, and unsupervised writes destroy human trust immediately. 

Instead, we built a four-node **LangGraph state machine** (three functional stages, plus persistence) decoupled through **Model Context Protocol (MCP)** connectors:
1. **Live Context Pull:** Before parsing speech, the agent queries live Jira tickets and GitHub pull requests.
2. **Audited Extraction:** The agent processes the conversation against baseline project scope and code activity using structured Pydantic schemas. Relative dates ("by Friday") resolve to exact calendar dates, and unstated fields default strictly to `"MISSING"`.
3. **Approval First:** Everything is staged locally in SQLite (`agent.db`) with status `waiting`. Every item requires an exact verbatim line citation. Nothing touches Jira until the PM clicks Approve.

---

### 3. The Hardest Decision
The most difficult architectural decision was **how to handle contradictions between spoken claims and Git reality**—for example, when an engineer claims a task is "completely done", but the linked GitHub PR is still open and unmerged.

We weighed three options:
* **Option A: Auto-block the update.** (Risk: The work might be complete on a separate fork or demoed locally; an automated block frustrates the team).
* **Option B: Trust the spoken claim and mark Jira done.** (Risk: Status goes stale and unmerged code is marked complete, defeating the entire purpose of the agent).
* **Option C: Draft the update, but flag the discrepancy.** 

We chose **Option C**. The agent drafts the status update, marks `discrepancy_detected: true`, lowers its confidence score, and displays an unambiguous warning banner citing the open PR. The PM gets full context to make the decision in one click.

---

### 4. What We Left Out & What's Next
* **What we left out:** We deliberately skipped live in-call audio recording and calendar integrations. Teams transcription permissions vary widely in enterprises, so we prioritized reliable ingestion (Granola/Wispr JSON, Teams WebVTT, and Microsoft Graph imports) with immediate privacy discards. We also kept rejection one-way rather than auto-commenting rejections back to Jira.
* **What we would do next:** Implement learning from PM edits (using logged history diffs to tune prompt heuristics per team) and active Slack/Teams notifications when overdue deadline alerts flip the project forecast to "At Risk".
