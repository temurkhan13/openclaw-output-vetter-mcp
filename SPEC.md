# SPEC — openclaw-output-vetter-mcp

Server identifier: `openclaw-output-vetter`. Lives on PyPI as `openclaw-output-vetter-mcp`.

## Architecture (3 layers)

```
SCANNERS         — pure functions: input → typed result
                   • grounding.py (Jaccard overlap)
                   • swallowed_exceptions.py (Python AST)
                   • transcript.py (pattern matching)

TYPES            — frozen pydantic models, JSON-serializable
                   • GroundingResult / GroundingClaim
                   • SwallowedExceptionReport / SwallowedExceptionFinding
                   • TranscriptReview / TranscriptIssue / Turn

SERVER           — MCP wire-up
                   • 3 tools, 3 demo resources, 2 prompts
                   • Stateless: no backend, no persistence, no caching (v1.0)
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
- **tool-call-without-side-effect**: reserved for v1.2 — requires inferring observable side effects from later turns.

## Resources

- `vetter://demo/grounded` — calls `verify_response_grounding` with a CLEAN sample
- `vetter://demo/fabricated` — calls `verify_response_grounding` with a FABRICATED sample
- `vetter://demo/swallowed-exceptions` — calls `find_swallowed_exceptions` with a sample containing all three high-severity patterns

## Prompts

- `verify-this-answer(threshold?)` — diagnostic walkthrough of `verify_response_grounding`
- `audit-this-code` — diagnostic walkthrough of `find_swallowed_exceptions`

## Severity ladder

For `find_swallowed_exceptions`:
- HIGH: `mock-substitution` (the silent-fake-success pattern)
- MEDIUM: `pass-only`, `silent-log-and-return`
- LOW: `bare-except`

For `review_transcript`:
- HIGH: `unverified-completion-claim`
- MEDIUM: `cross-turn-contradiction`

For `verify_response_grounding`: no per-claim severity; overall verdict is FABRICATED / PARTIALLY_GROUNDED / CLEAN / UNVERIFIED.

## Verdict semantics

Across all three tools:
- **CLEAN**: no issues found.
- **PARTIALLY_GROUNDED**: some support / some lacking. For grounding: some claims grounded, some not. For swallowed-exceptions: only LOW/MEDIUM findings. For transcript: only MEDIUM issues.
- **FABRICATED**: high-severity issues. For grounding: zero claims grounded. For swallowed-exceptions: at least one HIGH (mock-substitution). For transcript: at least one HIGH (unverified-completion-claim).
- **UNVERIFIED**: scanner could not determine. For grounding: answer too short or empty. For swallowed-exceptions: code unparseable. For transcript: empty list.

## Future work (v1.1+)

- LLM-as-judge backend (wraps DeepEval `FaithfulnessMetric`) gated behind `pip install openclaw-output-vetter-mcp[llm-judge]` extra
- Embedding-based similarity (sentence-transformers) for higher semantic-equivalence detection
- Tool-call-without-side-effect check (requires inferring observable evidence in later turns)
- Multi-language swallowed-exception scanners (TypeScript via tree-sitter, Go via go/parser, Rust via syn)
- Issue persistence + cross-session aggregation
- Webhook emit on FABRICATED verdict
