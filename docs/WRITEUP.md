# AI Project Manager Agent — Write-Up

### 1. The Core Problem
On enterprise projects, the real status is spoken in meetings and rarely reaches the plan. Producing a plausible summary is easy; building something a project manager will trust is not. The hard part is reconciling informal speech ("basically done", "by Friday") with deterministic Jira and GitHub reality, without inventing owners, guessing dates, or writing unverified changes into the system of record.

### 2. Our Approach
The obvious alternative is one prompt that reads a transcript and calls the Jira API directly. We rejected it: a monolithic prompt cannot cross-check spoken claims against live code, and an unsupervised write destroys trust on the first mistake.

```
[ Meeting Transcript ] ──► [ Fetch Live Context ] ──► [ Extract & Audit ] ──► [ Persist & Link ]
(Teams / WebVTT / JSON)     (Jira + GitHub via MCP)    (Gemini + Pydantic)      (SQLite + line citations)
                                                                                        │
                                                                                        ▼
[ Jira Writeback ] ◄── [ Human Approval Gate ] ◄── [ React Review Queue ] ◄── [ Code-Activity Links ]
(Issue / Status / Comment) (PM: Approve / Edit / Reject)  (Discrepancy warnings)   (PRs / commits / issues)
```

We built a four-node LangGraph state machine behind Model Context Protocol connectors. The first node fetches live Jira issues and GitHub pull requests and commits for the selected project. The second extracts structured objects with Gemini: relative dates become calendar dates, and unstated fields stay `"MISSING"`. The third stages every draft in SQLite with a verbatim transcript citation and links GitHub activity to Jira keys and action items. The fourth is the human gate — nothing reaches Jira until the PM approves in the review queue.

### 3. The Hardest Decision
The hardest decision was what to do when a spoken claim contradicts Git reality: an engineer says a task is "completely done" while its pull request is still open. We weighed three responses. Auto-block the update risks frustrating a team whose work may exist on another branch. Trust the claim and mark Jira done risks stale, false status — defeating the purpose of the product. Draft the change but flag it preserves both evidence and human control. We chose the third: the agent lowers confidence, sets `discrepancy_detected`, and shows a warning citing the open PR, leaving the PM to decide in one click. The agent stays assistive without hiding the contradiction.

### 4. What We Left Out & What's Next
We deliberately did not build live in-call audio recording, calendar sync, or outbound Teams alerts — deadline alerts are in-product only. Ambiguous owner and date phrases are handled conservatively, which can miss valid work. Next we would learn from the PM's edits to improve those judgements and push deadline alerts directly into Teams.
