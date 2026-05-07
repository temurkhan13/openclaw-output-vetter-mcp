# SPEC — openclaw-output-vetter-mcp

**Version:** v1.3.0
**Status:** v1.1 verify_action_outcome (P10 ABSORB) + v1.2 chained-claim parser + **v1.3 grounding scanner rewrite** (stem-Jaccard + stop-word filter + entity-mismatch detection + new `confidence_note` field for honest disclosure of lexical-method limits; adversarial test 1/5 → 4/5 PASS)
**Tests:** 114 passing
**Last updated:** 2026-05-07 (D3 SPEC drift sweep)

Server identifier: `openclaw-output-vetter`. Lives on PyPI as `openclaw-output-vetter-mcp`.

## Architecture (3 layers)

```
SCANNERS         — pure functions: input → typed result
                   • grounding.py (Jaccard overlap)
                   • swallowed_exceptions.py (Python AST)
                   • transcript.py (pattern matching)
                   • action_outcome.py (claim ↔ before/after diff) [v1.1+]

TYPES            — frozen pydantic models, JSON-serializable
                   • GroundingResult / GroundingClaim
                   • SwallowedExceptionReport / SwallowedExceptionFinding
                   • TranscriptReview / TranscriptIssue / Turn
                   • ActionOutcomeMismatch / ActionOutcomeReport [v1.1+]

SERVER           — MCP wire-up
                   • 4 tools, 4 demo resources, 3 prompts (v1.1+)
                   • Stateless: no backend, no persistence, no caching
```

The scanner layer is pure — no I/O, no global state. The server layer is also pure-functional except for the demo resources which call scanners with hardcoded inputs.

## Tools

### `verify_response_grounding`

```
Input:
  question: str       (reserved for v1.1; v1.0 doesn't use)
  context: str        (retrieval / source the answer should be grounded in)
  answer: str         (the agent's response to verify)
  threshold: float    (0.0–1.0, default 0.30)

Output: GroundingResult
  verdict: CLEAN | PARTIALLY_GROUNDED | FABRICATED | UNVERIFIED
  total_claims: int
  grounded_count: int
  ungrounded_count: int
  overall_grounding_score: float (0.0–1.0, average overlap_score)
  claims: list[GroundingClaim]
    claim: str
    grounded: bool
    overlap_score: float
    closest_context_excerpt: str | None (≤200 chars)
  summary: str
```

Algorithm:
1. Split `answer` into sentences via punctuation boundaries (. ! ?)
2. Drop sentences shorter than 3 words (greetings, headers)
3. Tokenize each claim — alpha-only word-boundary regex, lowercased
4. Window `context` into overlapping 50-word chunks (stride 25)
5. For each claim, compute Jaccard overlap with every chunk; take max
6. Mark `grounded = overlap >= threshold`
7. Compose verdict: CLEAN if all grounded, FABRICATED if none grounded, PARTIALLY_GROUNDED if mixed, UNVERIFIED if no claims survived splitting

### `find_swallowed_exceptions`

```
Input:
  code: str           (Python source code)

Output: SwallowedExceptionReport
  verdict: CLEAN | PARTIALLY_GROUNDED | FABRICATED | UNVERIFIED
  finding_count: int
  findings: list[SwallowedExceptionFinding]
    severity: INFO | LOW | MEDIUM | HIGH | CRITICAL
    line_number: int
    pattern: pass-only | mock-substitution | silent-log-and-return | bare-except
    code_excerpt: str (≤250 chars)
    description: str
  summary: str
  parse_error: str | None
```

Algorithm:
1. `ast.parse(code)` → SyntaxError → return UNVERIFIED with parse_error
2. `ast.walk` for every `ast.Try` node; for each `ast.ExceptHandler`:
   - **mock-substitution** (HIGH): handler returns `dict literal`, non-empty `list literal`, non-trivial constant, name from `_SUSPICIOUS_NAMES` (sample/mock/default/placeholder/fake/dummy/fixture/test/example), suspicious-name attribute access (e.g., `self.SAMPLE_DATA`), or constructor call (`dict()`, `list()`). Excluded if `raise` appears anywhere in handler body.
   - **silent-log-and-return** (MEDIUM): handler logs (print / log / logger / logging / info / warn / warning / error / debug call) AND returns AND no re-raise.
   - **pass-only** (MEDIUM): every body statement is `ast.Pass`.
   - **bare-except** (LOW): handler has no exception type.
3. Findings sorted by line number; verdict FABRICATED if any HIGH, PARTIALLY_GROUNDED if any other findings, CLEAN if none.

### `review_transcript`

```
Input:
  transcript: list[Turn]
    role: str               ("user" | "assistant" | "tool" | ...)
    text: str
    tool_calls: list[str]   (optional; tool names invoked)
    timestamp: datetime?    (optional)

Output: TranscriptReview
  verdict: CLEAN | PARTIALLY_GROUNDED | FABRICATED | UNVERIFIED
  turn_count: int
  issue_count: int
  issues: list[TranscriptIssue]
    severity: INFO | LOW | MEDIUM | HIGH | CRITICAL
    issue_kind: unverified-completion-claim | cross-turn-contradiction | tool-call-without-side-effect
    turn_indices: list[int]
    description: str
    evidence_excerpt: str (≤250 chars)
  summary: str
```

Checks:
- **unverified-completion-claim** (HIGH): assistant turn matches `_COMPLETION_PATTERN` (regex over completion verbs: configured / installed / deployed / wired / connected / implemented / integrated / fixed / updated / migrated / enabled / published / registered / provisioned / completed / finished / done / established / hooked up / written / tested) AND no tool calls in this turn or any prior turn.
- **cross-turn-contradiction** (MEDIUM): two assistant turns extract subject-verb-object factual statements via `_FACT_PATTERN`; same subject + same verb + different object (and not substring) → flag.
- **tool-call-without-side-effect**: reserved for v1.3 — requires inferring observable side effects from later turns.

### `verify_action_outcome` *(v1.1+, P10 ABSORB; v1.2+ chained-claim parser)*

```
Input:
  claim: str                              (the agent's stated outcome — verbatim)
  before_snapshot: dict[str, Any]         (caller-captured state BEFORE the action)
  after_snapshot: dict[str, Any]          (caller-captured state AFTER the action)
  expected_changes: list[str] | None      (optional caller-supplied checklist)

Output: ActionOutcomeReport
  verdict: CLEAN | PARTIALLY_GROUNDED | FABRICATED | UNVERIFIED
  matched_count: int
  mismatched_count: int
  mismatches: list[ActionOutcomeMismatch]
    severity: INFO | LOW | MEDIUM | HIGH | CRITICAL
    rule_id: str               (one of 8 ACTION_OUTCOME.* IDs)
    claim_excerpt: str         (≤200 chars)
    expected: str              (plain English)
    actual: str                (plain English)
    description: str
  diff_summary: str            (one-line diff text)
  summary: str
```

**Snapshot shape (schema-loose):** caller passes arbitrary `dict[str, Any]`. Recognized keys when present:
- `files` (list[str]) — set-diff for file additions/removals
- `git_status` (str | dict) — "clean"/"dirty" semantics; dict shape supports `{"clean": bool, "modified": [...], "untracked": [...]}`
- `git_tip` / `git_head` / `git_log_tip` (str SHA)
- `tests_status` / `test_status` (str | dict | bool) — pass/fail or `{"passed": N, "failed": N}`
- `read_only` (bool) — caller-asserted no-write constraint; if True in before AND state changes → `STATE_VIOLATED_CONSTRAINT` (Codex sandbox-escalation case)

Other keys are tracked for general "did anything change?" diff-summary, but no claim-specific matchers run.

**Detection rules** (8 rule_ids under `ACTION_OUTCOME.*`):
- `STATE_UNCHANGED` (HIGH) — vague-completion claim + identical before/after snapshots (the chiefofautism failure mode)
- `UNSUPPORTED_CLAIM` (HIGH) — claim references a specific filename that's not in the diff
- `TESTS_NOT_PASSING` (CRITICAL) — claim says tests pass; tests_status indicates failure
- `NO_COMMIT` (HIGH) — claim says committed/pushed/shipped; git_tip didn't change
- `UNCOMMITTED_CHANGES` (HIGH) — claim says repo is clean; git_status indicates dirty
- `STATE_VIOLATED_CONSTRAINT` (CRITICAL) — read_only=True asserted but state changed (Codex case)
- `MISSING_EXPECTED_CHANGE` (HIGH/MEDIUM) — caller-supplied `expected_changes` entry not satisfied
- `AMBIGUOUS_CLAIM` (MEDIUM) — claim is checkable but snapshots lack the relevant key

**Claim-extraction patterns (v1.2+ supports chained "A and B" / "A, B, and C"):**
- `created_file` / `created_file_terse` — "Created auth.py" / "Wrote helpers.py"
- `deleted_file` — "Removed old.py" / "Deleted legacy.py"
- `tests_pass` — "tests pass" / "all green"
- `committed` — "committed and pushed"
- `clean_state` — "the project is clean now"
- `vague_completion` — "I cleaned up", "All done", "tidied"

Multi-target expansion bounded by sentence boundaries. Period only counts as boundary when followed by whitespace or end-of-string — periods inside filenames (`.py`, `.tsx`) don't terminate scan.

**`expected_changes` formats:**
- `file:foo.py:added` — file in `files_added`
- `file:bar.py:removed` — file in `files_removed`
- `git:committed` — git tip changed
- `git:clean` — git_status_after == "clean"
- `tests:pass` — tests_status semantically passing

## Resources

- `vetter://demo/grounded` — calls `verify_response_grounding` with a CLEAN sample
- `vetter://demo/fabricated` — calls `verify_response_grounding` with a FABRICATED sample
- `vetter://demo/swallowed-exceptions` — calls `find_swallowed_exceptions` with a sample containing all three high-severity patterns
- `vetter://demo/action-divergence` *(v1.1+)* — calls `verify_action_outcome` with the chiefofautism case (claim says "I cleaned up the project structure"; before == after)

## Prompts

- `verify-this-answer(threshold?)` — diagnostic walkthrough of `verify_response_grounding`
- `audit-this-code` — diagnostic walkthrough of `find_swallowed_exceptions`
- `verify-this-action` *(v1.1+)* — diagnostic walkthrough of `verify_action_outcome` with snapshot-capture guidance

## Severity ladder

For `find_swallowed_exceptions`:
- HIGH: `mock-substitution` (the silent-fake-success pattern)
- MEDIUM: `pass-only`, `silent-log-and-return`
- LOW: `bare-except`

For `review_transcript`:
- HIGH: `unverified-completion-claim`
- MEDIUM: `cross-turn-contradiction`

For `verify_response_grounding`: no per-claim severity; overall verdict is FABRICATED / PARTIALLY_GROUNDED / CLEAN / UNVERIFIED.

For `verify_action_outcome` *(v1.1+)*:
- CRITICAL: `TESTS_NOT_PASSING`, `STATE_VIOLATED_CONSTRAINT`
- HIGH: `STATE_UNCHANGED`, `UNSUPPORTED_CLAIM`, `NO_COMMIT`, `UNCOMMITTED_CHANGES`, `MISSING_EXPECTED_CHANGE` (file:added/removed, git:committed)
- MEDIUM: `AMBIGUOUS_CLAIM`, `MISSING_EXPECTED_CHANGE` (git:clean)

## Verdict semantics

Across all four tools:
- **CLEAN**: no issues found.
- **PARTIALLY_GROUNDED**: some support / some lacking. For grounding: some claims grounded, some not. For swallowed-exceptions: only LOW/MEDIUM findings. For transcript: only MEDIUM issues. For action-outcome: some assertions match diff, others don't.
- **FABRICATED**: high-severity issues. For grounding: zero claims grounded. For swallowed-exceptions: at least one HIGH (mock-substitution). For transcript: at least one HIGH (unverified-completion-claim). For action-outcome: at least one CRITICAL OR (HIGH AND zero matched).
- **UNVERIFIED**: scanner could not determine. For grounding: answer too short or empty. For swallowed-exceptions: code unparseable. For transcript: empty list. For action-outcome: claim has no extractable assertions AND no `expected_changes` AND no constraint-violation detected.

## Future work (v1.4+)

(v1.3 grounding-rewrite shipped 2026-05-06: stem-Jaccard + stop-word filter + entity-mismatch detection + `confidence_note` field. Adversarial test 1/5 → 4/5 PASS. Items below are post-v1.3 candidates.)

- LLM-as-judge backend (wraps DeepEval `FaithfulnessMetric`) gated behind `pip install openclaw-output-vetter-mcp[llm-judge]` extra
- Embedding-based similarity (sentence-transformers) for higher semantic-equivalence detection beyond what the v1.3 stem-Jaccard scanner achieves
- Tool-call-without-side-effect check (requires inferring observable evidence in later turns)
- Multi-language swallowed-exception scanners (TypeScript via tree-sitter, Go via go/parser, Rust via syn)
- Issue persistence + cross-session aggregation
- Webhook emit on FABRICATED verdict
- **Action-outcome claim parser:** richer NL handling — non-English claims, mixed sentence structures with implicit chaining ("the auth, login, and logout files"), claim parsers using small parsing models for higher recall
- **Snapshot-capture helpers:** optional companion utilities for callers to easily snapshot common state shapes (filesystem, git, pytest results) — keeping the core scanner stateless
