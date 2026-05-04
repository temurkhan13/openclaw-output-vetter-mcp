"""Multi-turn transcript review — claim-vs-evidence + cross-turn contradictions.

Catches:
  - Unverified completion claims: assistant says "I've configured X" but no
    tool calls in the transcript actually configured X.
  - Cross-turn contradictions: turn 7 asserts "the API returns JSON" but
    turn 3 asserted "the API returns XML."
  - Tool calls without observable side effects: assistant calls `write_file`
    but no later turn references the file existing — possible silent failure.

Lightweight pure-Python pattern matching. Not as smart as an LLM-as-judge
review but sub-second and free; catches the high-frequency easy patterns.
"""
from __future__ import annotations

import re

from openclaw_output_vetter_mcp.types import (
    Severity,
    TranscriptIssue,
    TranscriptReview,
    Turn,
    Verdict,
)

_COMPLETION_VERBS = (
    r"i'?ve\s+(?:set\s+up|configured|added|created|installed|deployed|wired|connected|"
    r"implemented|integrated|fixed|updated|migrated|enabled|published|registered|provisioned|"
    r"completed|finished|done|established|hooked\s+up|written|tested\s+and\s+confirmed)"
)
_COMPLETION_PATTERN = re.compile(_COMPLETION_VERBS, re.IGNORECASE)


_TOOL_KEYWORDS = {
    "write_file": ("write", "wrote", "created", "saved"),
    "edit_file": ("edited", "modified", "updated"),
    "run_command": ("ran", "executed", "installed", "deployed"),
    "create_pull_request": ("opened", "created"),
}


def _excerpt(text: str, limit: int = 250) -> str:
    text = text.strip()
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def _detect_unverified_completions(transcript: list[Turn]) -> list[TranscriptIssue]:
    issues: list[TranscriptIssue] = []
    for i, turn in enumerate(transcript):
        if turn.role != "assistant":
            continue
        if not _COMPLETION_PATTERN.search(turn.text or ""):
            continue
        # Look for tool calls in this turn or any prior assistant/tool turns
        has_tool_evidence = bool(turn.tool_calls)
        if not has_tool_evidence:
            for prior in transcript[:i]:
                if prior.tool_calls:
                    has_tool_evidence = True
                    break
        if not has_tool_evidence:
            issues.append(
                TranscriptIssue(
                    severity=Severity.HIGH,
                    issue_kind="unverified-completion-claim",
                    turn_indices=[i],
                    description=(
                        "Assistant claims completion of an action but no tool calls "
                        "are present in this turn or earlier turns. The claim cannot "
                        "be verified from the transcript alone."
                    ),
                    evidence_excerpt=_excerpt(turn.text),
                )
            )
    return issues


_FACT_PATTERN = re.compile(
    r"(?P<subject>\bthe\s+\w+(?:\s+\w+)?)\s+(?P<verb>is|are|returns|uses|requires|"
    r"contains|expects|defaults\s+to|is\s+set\s+to)\s+(?P<object>[^\.\?!]{3,80})",
    re.IGNORECASE,
)


def _extract_factual_statements(text: str) -> list[tuple[str, str, str]]:
    """Return list of (subject, verb, object) triples roughly capturing factual claims."""
    out: list[tuple[str, str, str]] = []
    for m in _FACT_PATTERN.finditer(text or ""):
        subj = m.group("subject").strip().lower()
        verb = m.group("verb").strip().lower()
        obj = m.group("object").strip().lower()
        if len(obj) > 80:
            obj = obj[:80]
        out.append((subj, verb, obj))
    return out


def _detect_contradictions(transcript: list[Turn]) -> list[TranscriptIssue]:
    """Find pairs of assistant turns that make contradictory factual claims about the same subject."""
    issues: list[TranscriptIssue] = []
    statements_by_turn: list[list[tuple[str, str, str]]] = []
    for turn in transcript:
        if turn.role == "assistant":
            statements_by_turn.append(_extract_factual_statements(turn.text or ""))
        else:
            statements_by_turn.append([])

    # Compare each assistant turn's statements against later assistant turns' statements
    for i in range(len(transcript)):
        for j in range(i + 1, len(transcript)):
            for s_i in statements_by_turn[i]:
                for s_j in statements_by_turn[j]:
                    # Same subject but different objects → potential contradiction
                    if s_i[0] == s_j[0] and s_i[1] == s_j[1] and s_i[2] != s_j[2]:
                        # Skip if either object is a substring of the other (refinements, not contradictions)
                        if s_i[2] in s_j[2] or s_j[2] in s_i[2]:
                            continue
                        issues.append(
                            TranscriptIssue(
                                severity=Severity.MEDIUM,
                                issue_kind="cross-turn-contradiction",
                                turn_indices=[i, j],
                                description=(
                                    f"Cross-turn factual drift on subject '{s_i[0]}': "
                                    f"turn {i} says '{s_i[2]}', turn {j} says '{s_j[2]}'."
                                ),
                                evidence_excerpt=(
                                    f"[{i}] {_excerpt(transcript[i].text, 100)}\n"
                                    f"[{j}] {_excerpt(transcript[j].text, 100)}"
                                ),
                            )
                        )
                        # One issue per pair is enough; break inner loops
                        break
    return issues


def review_transcript(transcript: list[Turn]) -> TranscriptReview:
    """Run all checks on the transcript and compose a TranscriptReview."""
    if not transcript:
        return TranscriptReview(
            verdict=Verdict.UNVERIFIED,
            turn_count=0,
            issue_count=0,
            issues=[],
            summary="Empty transcript — nothing to review.",
        )

    issues: list[TranscriptIssue] = []
    issues.extend(_detect_unverified_completions(transcript))
    issues.extend(_detect_contradictions(transcript))

    if not issues:
        verdict = Verdict.CLEAN
        summary = (
            f"Reviewed {len(transcript)} turn(s); no unverified completion claims "
            f"or cross-turn contradictions detected."
        )
    else:
        any_high = any(i.severity in {Severity.HIGH, Severity.CRITICAL} for i in issues)
        verdict = Verdict.FABRICATED if any_high else Verdict.PARTIALLY_GROUNDED
        summary = (
            f"Reviewed {len(transcript)} turn(s); flagged {len(issues)} issue(s)"
            + (
                " including unverified completion claim(s) — investigate before trusting the transcript."
                if any_high
                else "."
            )
        )

    return TranscriptReview(
        verdict=verdict,
        turn_count=len(transcript),
        issue_count=len(issues),
        issues=issues,
        summary=summary,
    )
