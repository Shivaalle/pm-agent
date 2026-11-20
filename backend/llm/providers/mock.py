"""Deterministic offline provider used for tests and explicit offline demos.

It is intentionally simple and only asserts facts stated in the transcript.
It is never used as a hidden fallback when a real provider fails.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple, Type

from pydantic import BaseModel

from backend.llm.providers.base import LLMProvider, LLMResponse, LLMUsage


class MockProvider(LLMProvider):
    def __init__(self) -> None:
        self._model = "mock-deterministic"

    @property
    def name(self) -> str:
        return "mock"

    @property
    def model(self) -> str:
        return self._model

    async def generate_structured(
        self,
        system_instruction: str,
        user_prompt: str,
        response_schema: Type[BaseModel],
        temperature: float = 0.1,
    ) -> LLMResponse:
        usage = LLMUsage(provider="mock", model=self._model)
        transcript = self._section(user_prompt, "NUMBERED TRANSCRIPT")
        lines = self._parse_lines(transcript)
        meeting_date = self._field(user_prompt, "Meeting Date") or "2026-01-01"
        jira_keys = sorted(set(re.findall(r"\b[A-Z][A-Z0-9]{1,9}-\d+\b", user_prompt)))
        open_prs = [
            line for line in user_prompt.splitlines()
            if '"state": "open"' in line or '"merged": false' in line.lower()
        ]

        decisions, actions, raids, scopes, statuses = [], [], [], [], []
        for line_no, speaker, text in lines:
            lower = text.lower()
            quote = f"Line {line_no}: {speaker}: {text}"

            if any(w in lower for w in ("decide", "decided", "agreed", "agreement")):
                decisions.append(
                    {
                        "decision": self._decision_text(text),
                        "rationale": "Agreed by the meeting participants.",
                        "source_quote": quote,
                    }
                )

            action = self._action_text(text)
            if action:
                owner = speaker if re.search(r"\bi (will|can|'ll)\b", lower) else "MISSING"
                if "someone" in lower and "i will" not in lower and "i can" not in lower:
                    owner = "MISSING"
                due = self._resolve_date(text, meeting_date)
                actions.append(
                    {
                        "task": action,
                        "owner": owner,
                        "due_date": due,
                        "priority": "High" if any(w in lower for w in ("urgent", "critical", "blocker")) else "Medium",
                        "source_quote": quote,
                    }
                )

            category = self._raid_category(lower)
            if category:
                raids.append(
                    {
                        "category": category,
                        "title": text[:80],
                        "description": text,
                        "severity": "High" if any(w in lower for w in ("high", "major", "critical", "expires")) else "Medium",
                        "owner": speaker if "please" in lower or "monitor" in lower else "MISSING",
                        "is_update_to_existing": False,
                        "existing_reference": None,
                        "source_quote": quote,
                    }
                )

            if any(
                phrase in lower
                for phrase in (
                    "not in our agreed scope",
                    "out of our baseline scope",
                    "scope change",
                    "not in scope",
                    "out of scope",
                    "customer requested",
                )
            ):
                scopes.append(
                    {
                        "title": text[:80],
                        "description": text,
                        "requested_by": speaker or "MISSING",
                        "impact_assessment": "Requires formal scope review and sign-off before work starts.",
                        "source_quote": quote,
                    }
                )

            if self._is_status_claim(lower):
                key = next((key for key in jira_keys if key in text), "UNMATCHED")
                completed = any(w in lower for w in ("done", "finished", "completed", "merged", "live"))
                proposed = "Done" if completed else "In Progress"
                discrepancy = bool(completed and open_prs and "pr" not in lower and "github" not in lower)
                details = (
                    "Spoken completion claim has an open pull request in GitHub."
                    if discrepancy
                    else None
                )
                statuses.append(
                    {
                        "jira_key": key,
                        "spoken_status": text,
                        "proposed_jira_status": proposed,
                        "github_evidence": "GitHub pull request list supplied to the agent.",
                        "discrepancy_detected": discrepancy,
                        "discrepancy_details": details,
                        "confidence": 0.9 if key != "UNMATCHED" else 0.45,
                        "source_quote": quote,
                    }
                )

        summary = (
            "The meeting reviewed project progress, agreed decisions, assigned action items "
            "and discussed risks, scope and code-status evidence."
        )
        data = {
            "meeting_summary": summary,
            "decisions": self._dedupe(decisions, "decision"),
            "action_items": self._dedupe(actions, "task"),
            "raid_items": self._dedupe(raids, "title"),
            "scope_changes": self._dedupe(scopes, "title"),
            "status_updates": self._dedupe(statuses, "spoken_status"),
        }
        try:
            parsed = response_schema.model_validate(data)
        except Exception as exc:  # pragma: no cover - defensive
            return LLMResponse(
                parsed=None,
                usage=usage,
                raw=str(data),
                error=f"mock schema validation failed: {exc}",
            )
        return LLMResponse(parsed=parsed, usage=usage, raw=str(data))

    # --- helpers ---------------------------------------------------------

    @staticmethod
    def _section(prompt: str, name: str) -> str:
        marker = f"{name}:\n"
        idx = prompt.find(marker)
        if idx == -1:
            return prompt
        return prompt[idx + len(marker):]

    @staticmethod
    def _field(prompt: str, name: str) -> Optional[str]:
        match = re.search(rf"-?\s*{re.escape(name)}:\s*([^\n]+)", prompt)
        return match.group(1).strip() if match else None

    @staticmethod
    def _parse_lines(transcript: str) -> List[Tuple[int, str, str]]:
        parsed = []
        for raw in transcript.splitlines():
            match = re.match(r"Line (\d+):\s*([^:]+):\s*(.*)", raw)
            if match:
                parsed.append((int(match.group(1)), match.group(2).strip(), match.group(3).strip()))
        return parsed

    @staticmethod
    def _decision_text(text: str) -> str:
        cleaned = re.sub(
            r"^(agreed[.,]?\s*|okay[.,]?\s*|ok[.,]?\s*|sure[.,]?\s*)",
            "",
            text,
            flags=re.I,
        )
        return cleaned.strip()

    @staticmethod
    def _action_text(text: str) -> Optional[str]:
        patterns = [
            r"\bI(?:'ll| will| can)\s+(.+)",
            r"\b(?:needs? to|need to|has to|have to)\s+(.+)",
            r"\bsomeone needs to\s+(.+)",
            r"\bplease\s+(.+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.I)
            if match:
                value = match.group(1).strip().rstrip(".")
                if len(value) < 8:
                    continue
                return value[0].upper() + value[1:]
        return None

    @staticmethod
    def _raid_category(lower: str) -> Optional[str]:
        if any(w in lower for w in ("dependency", "depends on", "blocked by", "expires", "third party")):
            return "Dependency"
        if any(w in lower for w in ("risk", "might", "could", "rate limit", "failover", "maintenance", "vulnerability")):
            return "Risk"
        if any(w in lower for w in ("assume", "assumption")):
            return "Assumption"
        if any(w in lower for w in ("issue", "bug", "broken", "failing", "blocker")):
            return "Issue"
        return None

    @staticmethod
    def _is_status_claim(lower: str) -> bool:
        return any(
            w in lower
            for w in (
                " is done",
                "are done",
                "fully finished",
                "completed the",
                "pr is merged",
                "pr was merged",
                "is live",
                "ready for production",
                "in progress",
                "not done",
                "still open",
            )
        )

    @staticmethod
    def _resolve_date(text: str, meeting_date: str) -> str:
        from backend.database import normalise_due_date

        match = re.search(
            r"\b(?:by|before|on|due)\s+(.+?)(?:[.,;]|$)", text, re.I
        )
        if not match:
            return "MISSING"
        return normalise_due_date(match.group(1).strip(), meeting_date)

    @staticmethod
    def _dedupe(items: List[dict], key: str) -> List[dict]:
        seen = set()
        result = []
        for item in items:
            value = str(item.get(key, "")).strip().lower()
            if value and value not in seen:
                seen.add(value)
                result.append(item)
        return result
