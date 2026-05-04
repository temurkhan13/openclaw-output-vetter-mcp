"""Tests for multi-turn transcript review."""
from __future__ import annotations

from openclaw_output_vetter_mcp.scanners.transcript import review_transcript
from openclaw_output_vetter_mcp.types import Severity, Turn, Verdict


def _t(role: str, text: str, tool_calls: list[str] | None = None) -> Turn:
    return Turn(role=role, text=text, tool_calls=tool_calls or [])


def test_empty_transcript_returns_unverified() -> None:
    review = review_transcript([])
    assert review.verdict == Verdict.UNVERIFIED
    assert review.turn_count == 0
    assert review.issue_count == 0


def test_clean_transcript_with_tool_evidence() -> None:
    transcript = [
        _t("user", "Please configure the gateway."),
        _t(
            "assistant",
            "I've configured the gateway with port 18789.",
            tool_calls=["edit_file", "run_command"],
        ),
    ]
    review = review_transcript(transcript)
    assert review.verdict == Verdict.CLEAN
    assert review.issue_count == 0


def test_unverified_completion_claim_flagged() -> None:
    transcript = [
        _t("user", "Please configure the gateway."),
        _t("assistant", "I've configured the gateway and verified everything is working."),
    ]
    review = review_transcript(transcript)
    issues = [i for i in review.issues if i.issue_kind == "unverified-completion-claim"]
    assert len(issues) == 1
    assert issues[0].severity == Severity.HIGH
    assert review.verdict == Verdict.FABRICATED


def test_completion_claim_with_prior_tool_calls_is_clean() -> None:
    transcript = [
        _t("user", "Please configure the gateway."),
        _t(
            "assistant",
            "Let me look into this.",
            tool_calls=["edit_file"],
        ),
        _t("user", "Did you finish?"),
        _t("assistant", "Yes, I've configured everything."),
    ]
    review = review_transcript(transcript)
    issues = [i for i in review.issues if i.issue_kind == "unverified-completion-claim"]
    assert len(issues) == 0


def test_cross_turn_contradiction_detected() -> None:
    transcript = [
        _t("user", "What does the API return?"),
        _t("assistant", "The API returns JSON for every request."),
        _t("user", "Show me an example."),
        _t("assistant", "Here's the format. The API returns XML for legacy endpoints."),
    ]
    review = review_transcript(transcript)
    contradictions = [i for i in review.issues if i.issue_kind == "cross-turn-contradiction"]
    # At least one contradiction should be picked up between "returns JSON" vs "returns XML"
    assert len(contradictions) >= 1


def test_no_contradiction_when_subjects_differ() -> None:
    transcript = [
        _t("user", "Tell me about the system."),
        _t("assistant", "The frontend uses React."),
        _t("assistant", "The backend uses Python."),
    ]
    review = review_transcript(transcript)
    contradictions = [i for i in review.issues if i.issue_kind == "cross-turn-contradiction"]
    assert len(contradictions) == 0


def test_review_summary_mentions_turn_count() -> None:
    transcript = [_t("user", "Hi."), _t("assistant", "Hello.")]
    review = review_transcript(transcript)
    assert str(len(transcript)) in review.summary or "turn" in review.summary.lower()


def test_each_issue_references_valid_turn_indices() -> None:
    transcript = [
        _t("user", "Please configure the gateway."),
        _t("assistant", "I've configured the gateway and database."),
    ]
    review = review_transcript(transcript)
    for issue in review.issues:
        for idx in issue.turn_indices:
            assert 0 <= idx < len(transcript)


def test_pure_user_turns_dont_trigger_completion_check() -> None:
    transcript = [
        _t("user", "I've already configured the gateway myself."),
        _t("assistant", "Got it, I won't touch the gateway."),
    ]
    review = review_transcript(transcript)
    # User turns shouldn't be flagged as unverified completion claims
    issues = [i for i in review.issues if i.issue_kind == "unverified-completion-claim"]
    assert all(transcript[i.turn_indices[0]].role == "assistant" for i in issues)
