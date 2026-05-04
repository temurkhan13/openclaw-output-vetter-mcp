"""Lightweight grounding check — claim-by-claim overlap against retrieval context.

v1.0 uses bag-of-words Jaccard similarity for a fully-local, sub-second, free
check. v1.1 will optionally wrap DeepEval's `FaithfulnessMetric` for higher-
quality LLM-as-judge mode (gated behind an opt-in dependency + API key).

The differentiator vs DeepEval/Phoenix/LangSmith is positioning: this is a
**single-transcript inline tool** an agent calls during a conversation,
not an eval-set framework run via dashboard. Sub-second matters more than
LLM-as-judge accuracy at this surface.
"""
from __future__ import annotations

import re

from openclaw_output_vetter_mcp.types import (
    GroundingClaim,
    GroundingResult,
    Verdict,
)

_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")
_WORD = re.compile(r"\b[a-z][a-z0-9'-]*\b")
_MIN_CLAIM_WORDS = 3
"""Sentences shorter than this are dropped (greetings, fillers, headers)."""

_GROUNDED_THRESHOLD = 0.20
"""Jaccard overlap >= this counts as grounded.

Lower threshold (0.20) chosen for inline-fast use: cost of false-negative-grounded
(saying 'this is fine' when slightly off) is low; cost of false-positive-fabricated
(saying 'this is fabricated' when answer just rephrases context with different word
order) is higher because it makes the agent re-fetch context unnecessarily.
"""


def _split_into_claims(text: str) -> list[str]:
    """Split agent answer into atomic claims (sentences) for grounding check."""
    text = text.strip()
    if not text:
        return []
    candidates = _SENTENCE_BOUNDARY.split(text)
    out: list[str] = []
    for c in candidates:
        c = c.strip()
        if not c:
            continue
        words = _WORD.findall(c.lower())
        if len(words) < _MIN_CLAIM_WORDS:
            continue
        out.append(c)
    return out


def _tokens(text: str) -> set[str]:
    """Bag-of-words token set, lowercased, alpha-only."""
    return set(_WORD.findall(text.lower()))


def _chunk_context(context: str, words_per_chunk: int = 50) -> list[str]:
    """Window the context into overlapping word chunks for finer-grained matching."""
    text = context.strip()
    if not text:
        return []
    words = _WORD.findall(text.lower())
    if len(words) <= words_per_chunk:
        return [text]
    # Original (non-lowercased) tokens for excerpt readability
    raw_words = re.findall(r"\S+", text)
    if len(raw_words) <= words_per_chunk:
        return [text]
    stride = max(1, words_per_chunk // 2)
    chunks: list[str] = []
    for i in range(0, len(raw_words) - words_per_chunk + 1, stride):
        chunks.append(" ".join(raw_words[i : i + words_per_chunk]))
    if not chunks:
        chunks = [text]
    return chunks


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a:
        return 0.0
    if not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _best_overlap(claim: str, context_chunks: list[str]) -> tuple[float, str | None]:
    """Find the highest-overlap context chunk for a claim. Returns (score, excerpt)."""
    if not context_chunks:
        return 0.0, None
    claim_tokens = _tokens(claim)
    if not claim_tokens:
        return 0.0, None
    best_score = 0.0
    best_chunk: str | None = None
    for chunk in context_chunks:
        score = _jaccard(claim_tokens, _tokens(chunk))
        if score > best_score:
            best_score = score
            best_chunk = chunk
    return best_score, best_chunk


def _summarize(verdict: Verdict, total: int, ungrounded: int, score: float) -> str:
    if total == 0:
        return "No claims to check — the answer was empty or contained only short fragments."
    if verdict == Verdict.CLEAN:
        return (
            f"All {total} claim(s) grounded in input context "
            f"(avg overlap {score:.2f})."
        )
    if verdict == Verdict.FABRICATED:
        return (
            f"All {total} claim(s) lack grounding in the input context "
            f"(avg overlap {score:.2f}) — likely hallucinated."
        )
    return (
        f"{ungrounded}/{total} claim(s) lack grounding in input context "
        f"(avg overlap {score:.2f}) — review the ungrounded claims listed."
    )


def verify_grounding(
    question: str,
    context: str,
    answer: str,
    threshold: float = _GROUNDED_THRESHOLD,
) -> GroundingResult:
    """Check that every claim in `answer` has support in `context`.

    `question` is currently unused but accepted in the signature for future
    relevance-vs-grounding split (v1.1).

    Returns a GroundingResult with per-claim pass/fail + an aggregate verdict.
    """
    _ = question  # reserved for v1.1 relevance check
    claims_text = _split_into_claims(answer)
    if not claims_text:
        return GroundingResult(
            verdict=Verdict.UNVERIFIED,
            grounded_count=0,
            ungrounded_count=0,
            total_claims=0,
            overall_grounding_score=0.0,
            claims=[],
            summary=_summarize(Verdict.UNVERIFIED, 0, 0, 0.0),
        )

    chunks = _chunk_context(context)
    claim_results: list[GroundingClaim] = []
    grounded = 0
    ungrounded = 0
    total_score = 0.0

    for c in claims_text:
        score, excerpt = _best_overlap(c, chunks)
        is_grounded = score >= threshold
        if is_grounded:
            grounded += 1
        else:
            ungrounded += 1
        total_score += score
        claim_results.append(
            GroundingClaim(
                claim=c,
                grounded=is_grounded,
                overlap_score=score,
                closest_context_excerpt=(excerpt[:200] if excerpt else None),
            )
        )

    overall = total_score / len(claim_results) if claim_results else 0.0
    if ungrounded == 0:
        verdict = Verdict.CLEAN
    elif grounded == 0:
        verdict = Verdict.FABRICATED
    else:
        verdict = Verdict.PARTIALLY_GROUNDED

    return GroundingResult(
        verdict=verdict,
        grounded_count=grounded,
        ungrounded_count=ungrounded,
        total_claims=len(claim_results),
        overall_grounding_score=overall,
        claims=claim_results,
        summary=_summarize(verdict, len(claim_results), ungrounded, overall),
    )
