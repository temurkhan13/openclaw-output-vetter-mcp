"""Domain types for openclaw-output-vetter-mcp.

All models are frozen pydantic — they round-trip through JSON cleanly and
serve as MCP tool/resource response payloads.
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Verdict(StrEnum):
    """Top-level verdict for a vet operation."""

    CLEAN = "clean"
    """No issues found."""
    PARTIALLY_GROUNDED = "partially-grounded"
    """Some claims grounded, some not."""
    FABRICATED = "fabricated"
    """No grounding evidence — agent stating things not in input."""
    UNVERIFIED = "unverified"
    """Cannot determine — insufficient input or unsupported pattern."""


class Severity(StrEnum):
    """Severity ladder for findings."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# ─────────── Grounding (single-transcript inline check) ───────────


class GroundingClaim(BaseModel):
    """One claim extracted from the agent's answer + whether it's grounded in context."""

    model_config = ConfigDict(frozen=True)

    claim: str
    """The extracted claim sentence."""
    grounded: bool
    overlap_score: float
    """0.0–1.0: how much stem-Jaccard overlap with the closest context chunk."""
    closest_context_excerpt: str | None = None
    """The context chunk that scored highest (truncated to 200 chars). None when no context overlap."""
    unsupported_entities: list[str] = Field(default_factory=list)
    """Proper nouns or numbers in the claim that don't appear anywhere in the context.
    A non-empty list is a strong signal of fabrication even when overlap_score is high
    (e.g. `Eiffel Tower is in Berlin` against a context about Paris — vocabulary overlaps
    but the location entity Berlin is unsupported)."""


class GroundingResult(BaseModel):
    """Response for `verify_response_grounding`."""

    model_config = ConfigDict(frozen=True)

    verdict: Verdict
    grounded_count: int
    ungrounded_count: int
    total_claims: int
    overall_grounding_score: float
    """0.0–1.0: average overlap_score across all claims."""
    claims: list[GroundingClaim]
    summary: str
    confidence_note: str = ""
    """Honest disclosure of what this scanner does and doesn't catch.
    Always populated — clients should surface it in any UI that shows the verdict."""


# ─────────── Swallowed-exception scanner ───────────


class SwallowedExceptionFinding(BaseModel):
    """One detected try/except pattern that swallows an exception or substitutes mock data."""

    model_config = ConfigDict(frozen=True)

    severity: Severity
    line_number: int
    pattern: str
    """`pass-only`, `mock-substitution`, `silent-log-and-return`, `bare-except`."""
    code_excerpt: str
    """Up to 250 chars of the offending block."""
    description: str


class SwallowedExceptionReport(BaseModel):
    """Response for `find_swallowed_exceptions`."""

    model_config = ConfigDict(frozen=True)

    verdict: Verdict
    finding_count: int
    findings: list[SwallowedExceptionFinding]
    summary: str
    parse_error: str | None = None
    """Set if the input wasn't parseable as Python (verdict will be UNVERIFIED)."""


# ─────────── Transcript review ───────────


class Turn(BaseModel):
    """One turn in an agent transcript."""

    model_config = ConfigDict(frozen=True)

    role: str
    """`user`, `assistant`, `tool`, etc."""
    text: str
    tool_calls: list[str] = Field(default_factory=list)
    """Names of tools the assistant invoked in this turn (only relevant when role==assistant)."""
    timestamp: datetime | None = None


class TranscriptIssue(BaseModel):
    """One issue surfaced by `review_transcript`."""

    model_config = ConfigDict(frozen=True)

    severity: Severity
    issue_kind: str
    """`unverified-completion-claim` | `cross-turn-contradiction` | `tool-call-without-side-effect`."""
    turn_indices: list[int]
    """Which turn(s) the issue references (0-based)."""
    description: str
    evidence_excerpt: str
    """Quote from the relevant turn(s), truncated to 250 chars."""


class TranscriptReview(BaseModel):
    """Response for `review_transcript`."""

    model_config = ConfigDict(frozen=True)

    verdict: Verdict
    turn_count: int
    issue_count: int
    issues: list[TranscriptIssue]
    summary: str


# ─────────── Action-outcome verifier (v1.1+, P10 ABSORB) ───────────


class ActionOutcomeMismatch(BaseModel):
    """One mismatch between an agent's claim and the actual before/after state diff."""

    model_config = ConfigDict(frozen=True)

    severity: Severity
    rule_id: str
    """Stable identifier — `ACTION_OUTCOME.UNSUPPORTED_CLAIM`,
    `ACTION_OUTCOME.STATE_UNCHANGED`, `ACTION_OUTCOME.TESTS_NOT_PASSING`,
    `ACTION_OUTCOME.NO_COMMIT`, `ACTION_OUTCOME.UNCOMMITTED_CHANGES`,
    `ACTION_OUTCOME.STATE_VIOLATED_CONSTRAINT`, `ACTION_OUTCOME.MISSING_EXPECTED_CHANGE`,
    `ACTION_OUTCOME.AMBIGUOUS_CLAIM`."""
    claim_excerpt: str
    """Quoted fragment of the claim that this mismatch is about (truncated to 200 chars)."""
    expected: str
    """What the claim implied should have happened — in plain English."""
    actual: str
    """What the diff actually shows — in plain English."""
    description: str


class ActionOutcomeReport(BaseModel):
    """Response for `verify_action_outcome` — compares an agent claim against before/after state diff.

    The scanner is the next layer below `review_transcript`'s
    `unverified-completion-claim` check: that one fires when a claim has *no
    supporting tool calls visible in the transcript*. This one fires when a
    claim *has* supporting tool calls, but the side effects don't match.
    """

    model_config = ConfigDict(frozen=True)

    verdict: Verdict
    """CLEAN if all extracted claims match the diff; PARTIALLY_GROUNDED if some
    match and some don't; FABRICATED if the diff actively contradicts the
    claim (state unchanged or violated stated constraint); UNVERIFIED if the
    claim couldn't be parsed into checkable assertions."""
    matched_count: int
    """Claim assertions that matched the diff."""
    mismatched_count: int
    """Claim assertions that did not match the diff."""
    mismatches: list[ActionOutcomeMismatch]
    """All mismatches, sorted CRITICAL → INFO."""
    diff_summary: str
    """One-line text summary of what changed between the snapshots."""
    summary: str
