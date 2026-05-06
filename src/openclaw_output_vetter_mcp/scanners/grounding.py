"""Lightweight grounding check — claim-by-claim overlap against retrieval context.

This is a **lexical** scanner: it computes stem-level token overlap (Jaccard) plus
an entity-coverage check (do the proper nouns / numbers in the claim appear in the
context?). Two signals, combined for the final verdict.

What this scanner DOES catch:
- Direct fabrication (claim has zero meaningful overlap with context)
- Paraphrased grounded claims that share stems with context (`mutates` ≈ `mutating`)
- Misattribution where the claim names an entity not in the context
  (e.g. claim asserts `Eiffel Tower is in Berlin`; context only mentions Paris —
  the entity `Berlin` is flagged as unsupported even though `Eiffel Tower` overlaps)

What this scanner does NOT catch (state in `confidence_note`, surface to caller):
- Inferred claims requiring world knowledge ("Python is older than JavaScript"
  given dates in context — the inference is correct but lexically distant)
- Vocabulary-overlap fabrications where the wrong subject is associated with
  the right object ("Honeybees produce silk" against a context that mentions
  honeybees AND silk separately) — needs relation-level parsing

For those failure modes the operator should pair this tool with an
LLM-as-judge or NLI-based semantic verifier. We keep this scanner pure-Python
+ sub-second so it can run inline on every agent response.

v1.0: token-Jaccard. v1.3 (this version): stem-Jaccard + entity-coverage.
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
_PROPER_NOUN = re.compile(r"\b([A-Z][a-zA-Z]{2,}(?:\s+[A-Z][a-zA-Z]{2,})*)\b")
"""Sequences of capitalised words (≥3 chars). Catches multi-word entities
like `Eiffel Tower`, `New York City` — single-word names too (`Berlin`, `Python`).
We require ≥3 chars to avoid noise from single-letter abbreviations.

Note: a Capitalised word at the start of a sentence may be a normal verb
(`The`, `In`, `When`). That's handled by stop-word filtering after extraction.
"""
_NUMBER = re.compile(r"\b\d+(?:[.,]\d+)?\b")
_MIN_CLAIM_WORDS = 3
"""Sentences shorter than this are dropped (greetings, fillers, headers)."""

_GROUNDED_THRESHOLD = 0.20
"""Stem-Jaccard overlap >= this counts as grounded.

Lower threshold (0.20) chosen for inline-fast use: cost of false-negative-grounded
(saying 'this is fine' when slightly off) is low; cost of false-positive-fabricated
(saying 'this is fabricated' when answer just rephrases context with different word
order) is higher because it makes the agent re-fetch context unnecessarily.
"""

# Common sentence-initial capitalised tokens that aren't real entities.
# Filtering these reduces false-positive entity-mismatch flags.
_NON_ENTITY_CAPITALISED = frozenset({
    "the", "a", "an", "in", "on", "at", "by", "for", "of", "to", "with",
    "this", "that", "these", "those", "what", "when", "where", "why", "how",
    "is", "are", "was", "were", "be", "been", "being",
    "if", "then", "else", "and", "or", "but", "so", "as", "than",
    "you", "your", "we", "our", "they", "their", "i", "my", "me",
    "all", "any", "some", "many", "few", "every", "each",
    "do", "does", "did", "have", "has", "had", "can", "could",
    "would", "should", "may", "might", "must", "shall", "will",
    "let", "make", "made", "use", "used", "like", "say", "says", "said",
    "first", "next", "last", "after", "before", "during",
})

# General-purpose English stop-words. Dropped before stem-Jaccard so that
# function words (the, is, a, and...) don't dilute the overlap signal.
# Without this filter, claim "Python supports list mutation" against context
# "lists are mutable data structures" gets diluted by 'in', 'are', 'you can'
# etc., even though the substantive concepts (python / list / mutate) match.
_STOPWORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "am", "do", "does", "did", "have", "has", "had", "having",
    "in", "on", "at", "to", "for", "of", "with", "by", "from", "into",
    "out", "up", "down", "over", "under", "between", "through", "across",
    "this", "that", "these", "those", "such",
    "i", "me", "my", "we", "us", "our",
    "you", "your", "he", "him", "his", "she", "her", "it", "its",
    "they", "them", "their",
    "and", "or", "but", "so", "if", "then", "else", "while", "as", "than",
    "because", "since", "though", "although",
    "can", "could", "would", "should", "may", "might", "must", "will",
    "shall", "ought",
    "not", "no", "nor",
    "what", "when", "where", "why", "how", "which", "who", "whom", "whose",
    "all", "any", "some", "every", "each", "few", "many", "much", "more",
    "most", "other", "another", "same",
    "after", "before", "during", "until",
    "also", "just", "only", "even", "still", "yet", "ever", "never",
    "very", "really", "quite", "rather", "too",
    "let", "make", "made", "get", "got", "use", "used", "like",
    "say", "says", "said", "well",
})


def _meaningful_tokens(text: str) -> list[str]:
    """Return lowercased word tokens with stop-words filtered out (no stemming yet)."""
    return [w for w in _WORD.findall(text.lower()) if w not in _STOPWORDS]

# Common English suffixes for aggressive stemming. Order matters — longer
# suffixes are checked first so we don't strip "ing" when "ling" is the real stem.
# This is intentionally simpler than Porter — we trade precision for being
# pure-Python + dependency-free.
_SUFFIXES = (
    "ational", "ization", "ousness", "iveness", "fulness",
    "tional", "sement", "lessly",
    "ation", "ition", "ement", "ities", "ments", "ables",
    "able", "ible", "ical", "ical", "ical",
    "tion", "sion", "ness", "ment", "ance", "ence", "ship",
    "ies", "ing", "ize", "ise",
    "ed", "ly", "er", "or", "es", "al",
    "s",
)


def _stem(token: str) -> str:
    """Aggressive suffix-stripping stemmer. Pure-Python, no deps.

    Rules: skip stripping if the resulting stem would be too short (<3 chars).
    This catches the cases the Jaccard scanner most often fails on:
        mutating ↔ mutates ↔ mutate → 'mut'
        modifies ↔ modify ↔ modification → 'modif' / 'mod'
        supports ↔ supporting ↔ supported → 'support'
    """
    t = token.lower()
    for suffix in _SUFFIXES:
        if t.endswith(suffix) and len(t) - len(suffix) >= 3:
            return t[: -len(suffix)]
    return t


def _stems(text: str) -> set[str]:
    """Stem-set with stop-words filtered out. Function words don't carry
    grounding signal — they're noise that dilutes the Jaccard overlap.
    """
    return {_stem(w) for w in _meaningful_tokens(text)}


def _entities(text: str) -> set[str]:
    """Extract proper-noun-like entities and numbers.

    Returns lowercase tokens for matching purposes. Multi-word entities are
    split into their component words so `Eiffel Tower` produces `{eiffel, tower}`.
    """
    out: set[str] = set()
    for match in _PROPER_NOUN.finditer(text):
        for w in match.group(1).split():
            wl = w.lower()
            if wl not in _NON_ENTITY_CAPITALISED and len(wl) >= 3:
                out.add(wl)
    out.update(_NUMBER.findall(text))
    return out


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


def _chunk_context(context: str, words_per_chunk: int = 50) -> list[str]:
    """Window the context into overlapping word chunks for finer-grained matching."""
    text = context.strip()
    if not text:
        return []
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
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _best_overlap(claim: str, context_chunks: list[str]) -> tuple[float, str | None]:
    """Find the highest stem-Jaccard context chunk for a claim. Returns (score, excerpt)."""
    if not context_chunks:
        return 0.0, None
    claim_stems = _stems(claim)
    if not claim_stems:
        return 0.0, None
    best_score = 0.0
    best_chunk: str | None = None
    for chunk in context_chunks:
        score = _jaccard(claim_stems, _stems(chunk))
        if score > best_score:
            best_score = score
            best_chunk = chunk
    return best_score, best_chunk


def _unsupported_entities(claim: str, full_context: str) -> list[str]:
    """Return proper nouns / numbers in claim that don't appear in the full context.

    We compare against the FULL context (not chunks), because an entity might
    appear elsewhere in the document even if not in the highest-overlap chunk.
    """
    claim_ents = _entities(claim)
    if not claim_ents:
        return []
    context_ents = _entities(full_context)
    missing = sorted(e for e in claim_ents if e not in context_ents)
    return missing


_CONFIDENCE_NOTE = (
    "Lexical scanner: stem-Jaccard token overlap + entity-mismatch check. "
    "Catches direct fabrication, paraphrased grounded claims, and entity "
    "misattribution. Does NOT catch: world-knowledge inference (e.g. dates → "
    "ordering), or vocabulary-overlap fabrications where the wrong subject is "
    "associated with the right object. Pair with an LLM-as-judge or NLI verifier "
    "for those cases. See SPEC.md § Grounding scanner limitations."
)


def _summarize(verdict: Verdict, total: int, ungrounded: int, score: float) -> str:
    if total == 0:
        return "No claims to check — the answer was empty or contained only short fragments."
    if verdict == Verdict.CLEAN:
        return (
            f"All {total} claim(s) grounded in input context "
            f"(avg stem-overlap {score:.2f}; no unsupported entities)."
        )
    if verdict == Verdict.FABRICATED:
        return (
            f"All {total} claim(s) lack grounding in the input context "
            f"(avg stem-overlap {score:.2f}) — likely hallucinated."
        )
    return (
        f"{ungrounded}/{total} claim(s) lack grounding (low stem-overlap "
        f"or unsupported entities) — review the ungrounded claims listed."
    )


def verify_grounding(
    question: str,
    context: str,
    answer: str,
    threshold: float = _GROUNDED_THRESHOLD,
) -> GroundingResult:
    """Check that every claim in `answer` has lexical + entity support in `context`.

    `question` is currently unused but accepted in the signature for future
    relevance-vs-grounding split (v1.4).

    Returns a GroundingResult with per-claim pass/fail, an aggregate verdict,
    and a `confidence_note` documenting the scanner's limits — clients should
    surface that note alongside the verdict.
    """
    _ = question  # reserved for v1.4 relevance check
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
            confidence_note=_CONFIDENCE_NOTE,
        )

    chunks = _chunk_context(context)
    claim_results: list[GroundingClaim] = []
    grounded = 0
    ungrounded = 0
    total_score = 0.0

    for c in claims_text:
        score, excerpt = _best_overlap(c, chunks)
        unsupported = _unsupported_entities(c, context)
        # A claim is grounded iff:
        #   - stem-overlap meets the threshold, AND
        #   - no proper-noun / number entities in the claim are missing from context
        # The entity check catches misattribution that pure overlap misses.
        is_grounded = (score >= threshold) and not unsupported
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
                unsupported_entities=unsupported,
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
        confidence_note=_CONFIDENCE_NOTE,
    )
