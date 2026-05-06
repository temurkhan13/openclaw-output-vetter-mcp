"""Tests for the lightweight grounding scanner."""
from __future__ import annotations

import pytest

from openclaw_output_vetter_mcp.scanners.grounding import (
    _split_into_claims,
    verify_grounding,
)
from openclaw_output_vetter_mcp.types import Verdict

# ─────────── Claim splitting ───────────


def test_split_into_claims_handles_multiple_sentences() -> None:
    text = "The office is in London. The team is remote-first. We have 12 engineers."
    claims = _split_into_claims(text)
    assert len(claims) == 3


def test_split_into_claims_drops_short_fragments() -> None:
    text = "Hello! This is a longer sentence with several words."
    claims = _split_into_claims(text)
    # "Hello!" has only 1 word — dropped
    assert "Hello!" not in claims
    assert len(claims) == 1


def test_split_into_claims_handles_empty() -> None:
    assert _split_into_claims("") == []
    assert _split_into_claims("   ") == []


# ─────────── verify_grounding ───────────


def test_verify_grounding_clean_when_all_claims_supported() -> None:
    result = verify_grounding(
        question="Where is the office?",
        context=(
            "Pixelette Technologies is headquartered in London. "
            "The team is remote-first with members across Pakistan, the UK, and Canada."
        ),
        answer=(
            "The Pixelette Technologies headquarters is in London. "
            "The team is remote-first."
        ),
    )
    assert result.verdict == Verdict.CLEAN
    assert result.ungrounded_count == 0
    assert result.grounded_count >= 2
    assert result.overall_grounding_score > 0.3


def test_verify_grounding_fabricated_when_no_claims_supported() -> None:
    result = verify_grounding(
        question="What's the funding?",
        context="Pixelette Technologies is a self-funded software studio.",
        answer=(
            "Pixelette Technologies has raised twelve million dollars in Series A funding "
            "led by Sequoia Capital. The company has forty-seven full-time employees and "
            "recently expanded into the APAC region."
        ),
    )
    assert result.verdict == Verdict.FABRICATED
    assert result.grounded_count == 0
    assert result.ungrounded_count >= 2


def test_verify_grounding_partial_when_mixed() -> None:
    result = verify_grounding(
        question="Where is the office?",
        context="Pixelette Technologies is headquartered in London.",
        answer=(
            "The Pixelette Technologies headquarters is in London. "
            "Sequoia Capital led the most recent funding round of twelve million dollars."
        ),
    )
    assert result.verdict == Verdict.PARTIALLY_GROUNDED
    assert result.grounded_count >= 1
    assert result.ungrounded_count >= 1


def test_verify_grounding_unverified_when_answer_empty() -> None:
    result = verify_grounding(
        question="Where is the office?",
        context="Pixelette Technologies is headquartered in London.",
        answer="",
    )
    assert result.verdict == Verdict.UNVERIFIED
    assert result.total_claims == 0


def test_verify_grounding_unverified_when_answer_too_short() -> None:
    result = verify_grounding(
        question="Yes?",
        context="Pixelette Technologies is headquartered in London.",
        answer="Yes",
    )
    assert result.verdict == Verdict.UNVERIFIED
    assert result.total_claims == 0


def test_verify_grounding_each_claim_has_overlap_score() -> None:
    result = verify_grounding(
        question="Q?",
        context="The office is in London. The team uses Python.",
        answer="The office is in London. The team uses Python.",
    )
    for claim in result.claims:
        assert 0.0 <= claim.overlap_score <= 1.0


def test_verify_grounding_threshold_affects_verdict() -> None:
    # High threshold makes everything ungrounded
    result_strict = verify_grounding(
        question="Q?",
        context="The office is in London.",
        answer="The London office is on a quiet street with cobblestone roads.",
        threshold=0.95,
    )
    # Low threshold passes
    result_loose = verify_grounding(
        question="Q?",
        context="The office is in London.",
        answer="The London office is on a quiet street with cobblestone roads.",
        threshold=0.05,
    )
    assert result_strict.ungrounded_count >= result_loose.ungrounded_count


def test_verify_grounding_overall_score_is_within_bounds() -> None:
    result = verify_grounding(
        question="Q?",
        context="Random unrelated text about things and stuff.",
        answer="The team uses Python. The office is in London. We ship software.",
    )
    assert 0.0 <= result.overall_grounding_score <= 1.0


def test_verify_grounding_summary_mentions_claim_count() -> None:
    result = verify_grounding(
        question="Q?",
        context="The office is in London.",
        answer="The office is in London. The team uses Python.",
    )
    assert "claim" in result.summary.lower()
    assert str(result.total_claims) in result.summary or str(result.ungrounded_count) in result.summary


# ─────────── v1.3 — stem-Jaccard + entity-mismatch ───────────


def test_v13_paraphrase_grounded_via_stemming() -> None:
    """`mutates` ↔ `mutating` ↔ `mutation` should all stem to a common form,
    so a paraphrased grounded claim no longer reads as fabrication."""
    result = verify_grounding(
        question="Q?",
        context="In Python, lists are mutable data structures - you can append or modify them.",
        answer="Python supports list mutation after creation.",
    )
    assert result.verdict == Verdict.CLEAN, (
        f"paraphrase should be grounded; got verdict={result.verdict}, "
        f"score={result.overall_grounding_score:.2f}"
    )


def test_v13_entity_mismatch_flags_misattribution() -> None:
    """Eiffel-Tower-in-Berlin: vocabulary overlaps with a Paris-context (entity
    'Eiffel Tower' appears) but the location entity 'Berlin' is unsupported,
    which must flag the claim despite high stem overlap."""
    result = verify_grounding(
        question="Where is the Eiffel Tower?",
        context="Paris is the capital of France and home to the Eiffel Tower.",
        answer="The Eiffel Tower is in Berlin.",
    )
    assert result.verdict == Verdict.FABRICATED, (
        f"entity mismatch should fabricate-flag; got verdict={result.verdict}"
    )
    # The claim's `unsupported_entities` should include 'berlin'
    assert any("berlin" in c.unsupported_entities for c in result.claims), (
        f"expected 'berlin' in unsupported_entities; got "
        f"{[c.unsupported_entities for c in result.claims]}"
    )


def test_v13_confidence_note_always_populated() -> None:
    """Every response must carry the lexical-scanner-limits note so callers
    can surface the limitation alongside the verdict."""
    result = verify_grounding(
        question="Q?",
        context="Some context here.",
        answer="Some answer that mentions context here.",
    )
    assert result.confidence_note  # non-empty
    assert "lexical" in result.confidence_note.lower()
    assert "does not catch" in result.confidence_note.lower() or "not catch" in result.confidence_note.lower()


def test_v13_unsupported_entities_field_present_on_clean_claims() -> None:
    """Even CLEAN claims expose the entities field (empty list, not missing)."""
    result = verify_grounding(
        question="Q?",
        context="The office is in London.",
        answer="The office is in London.",
    )
    assert all(isinstance(c.unsupported_entities, list) for c in result.claims)


def test_v13_stop_words_dont_dilute_overlap() -> None:
    """Stop-words (the/is/are/in/of) should not carry grounding signal — the
    substantive concepts should drive the verdict, not function words."""
    # Both texts have lots of function-word overlap but only 'cars' as substantive match.
    # Pre-v1.3 the high stop-word overlap could have crossed threshold; v1.3 filters them.
    result = verify_grounding(
        question="Q?",
        context="There are some cars in the garage.",
        answer="The pizza is in the oven.",
    )
    # 'cars'/'pizza' don't share a stem and have no entity overlap → should be ungrounded
    assert result.verdict in (Verdict.FABRICATED, Verdict.PARTIALLY_GROUNDED), (
        f"high stop-word overlap should not paper over disjoint substantive content; "
        f"got verdict={result.verdict}, score={result.overall_grounding_score:.2f}"
    )


@pytest.mark.parametrize(
    "context,answer,expected_verdict",
    [
        # Identical → CLEAN
        ("Python is a programming language.", "Python is a programming language.", Verdict.CLEAN),
        # Empty context → FABRICATED for non-empty answers
        ("", "The team uses Python and ships software.", Verdict.FABRICATED),
    ],
)
def test_verify_grounding_parametrized_cases(context: str, answer: str, expected_verdict: Verdict) -> None:
    result = verify_grounding("Q?", context, answer)
    assert result.verdict == expected_verdict
