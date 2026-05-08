# Changelog

All notable changes to `openclaw-output-vetter-mcp` are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.3.3] — 2026-05-08

### Added — `openclaw-output-vetter-mcp-report` console script (V3 of cross-product UX retrofit)

A new console script that runs all three scanners (grounding, swallowed-exceptions, action-outcome) against the same representative cases as the demo and prints a one-page GitHub-flavored markdown report on stdout.

Output: per-scanner section with verdict + key findings/mismatches in markdown bullets, including the canonical chiefofautism May-2026 HN failure-mode case (claim "cleaned up project" against unchanged state → STATE_UNCHANGED FABRICATED).

UTF-8 stdout enforced. New `render.py` exports `render_output_vet_report(...)` plus per-scanner sub-renderers (`render_grounding`, `render_swallowed_exceptions`, `render_action_outcome`).

V3 of cross-product UX retrofit. No protocol behavior changed.

## [1.3.2] — 2026-05-08

### Added — `openclaw-output-vetter-mcp-demo` console script (V1 of cross-product UX retrofit)

A new console script that runs all three scanners against representative inputs in ~30 seconds:

1. **verify_grounding** — paraphrased-grounded answer + entity-mismatch fabrication
2. **find_swallowed_exceptions** — Python source with `try/except: pass` pattern
3. **verify_action_outcome** — agent claim "cleaned up project" against unchanged before/after snapshot (the canonical May-2026 HN story / chiefofautism failure mode)

Output for each: verdict (CLEAN/FABRICATED/PARTIALLY-GROUNDED/UNVERIFIED) + count of findings/mismatches + top finding's severity + rule_id + description.

**Usage:**

```
$ pip install openclaw-output-vetter-mcp
$ openclaw-output-vetter-mcp-demo

  4. verify_action_outcome · 'I cleaned up the project' / state unchanged
    verdict:    FABRICATED
    [HIGH] ACTION_OUTCOME.STATE_UNCHANGED: Claim asserts an action was taken,
       but the before and after snapshots are identical.
       expected: some change visible between before/after snapshot
       actual:   before and after snapshots are identical (no change detected)
```

**No external I/O.** All inputs are hand-curated string literals. Safe to run anywhere.

Adds a second console-script entry (`openclaw-output-vetter-mcp-demo`) alongside the existing MCP server entry.

## [1.3.1] — 2026-05-08

### Added — first-run startup banner (visibility-after-install fix, V2 of cross-product UX retrofit)

When the server starts via `python -m openclaw_output_vetter_mcp` or the console script, the first stderr line is now a one-line value-prove receipt:

```
openclaw-output-vetter-mcp v1.3.1 ready · post-action verify (response-grounding + claim-vs-action divergence + entity-mismatch) · backend=default
```

Before v1.3.1 the server started silently — operators who'd just `pip install`ed had no immediate signal of what the server actually does. The banner is the first-30-seconds value moment that was previously missing.

**Suppressible:** set `OPENCLAW_VETTER_QUIET=1` (or `true` / `yes`) to skip the banner.

**No protocol behavior changed.** Banner is stderr-only; stdout (the MCP JSON-RPC channel) is untouched. Pure observability addition.

## [1.3.0] — 2026-05-06

### Improved — `verify_response_grounding` is no longer pure bag-of-words Jaccard

Real-input adversarial validation against the v1.2.1 build found the grounding scanner failed 4 of 5 cases — the bag-of-words Jaccard approach missed paraphrased grounded claims and missed entity-misattribution fabrications. v1.3.0 closes 3 of those 4 failures with pure-Python improvements (no new dependencies, "sub-second / no API key / local" pitch preserved). The remaining 1 failure is genuinely outside the reach of lexical methods — it's now explicitly disclosed in every response via a new `confidence_note` field.

**Algorithm changes:**

- **Stem-Jaccard instead of token-Jaccard.** Aggressive suffix-stripping stemmer (`-ation`, `-able`, `-tion`, `-ing`, `-ed`, `-s` etc.) collapses morphological variants to a common root. `mutates` / `mutating` / `mutation` / `mutable` all share the stem `mut`. The paraphrase test (*"Python supports list mutation"* against *"lists are mutable"*) now correctly classifies as grounded.
- **Stop-word filtering before overlap computation.** Function words (`the`, `is`, `are`, `in`, `of`, `you`, `can`, etc.) don't carry grounding signal — they were diluting the Jaccard score. v1.3 filters them so substantive concepts drive the verdict.
- **Entity-mismatch detection.** New: extract proper nouns + numbers from each claim. Any claim entity that doesn't appear anywhere in the context is added to a `unsupported_entities` field on the claim, and the claim is marked ungrounded regardless of stem-overlap. This catches misattribution like *"The Eiffel Tower is in Berlin"* against a Paris-context — vocabulary overlaps (Eiffel Tower is in both) but the location entity `Berlin` is unsupported.

**Honest disclosure of remaining limits:**

The response now includes a `confidence_note` field, populated on every call, that documents:
- What the lexical scanner DOES catch (direct fabrication, paraphrase via stems, entity misattribution)
- What it DOES NOT catch:
  - **World-knowledge inference** — *"Python is older than JavaScript"* given dates in context. The inference is correct but lexically distant.
  - **Vocabulary-overlap fabrications** where the wrong subject is associated with the right object — *"Honeybees produce silk"* against a context that mentions both honeybees and silk separately. Bag-of-stems sees the overlap and concludes grounded; only relation-level parsing or NLI can catch this.

For those failure modes, the recommendation is layered: this scanner inline on every response (sub-second), plus periodic LLM-as-judge or NLI-based deep verification.

**New types:**
- `GroundingClaim.unsupported_entities: list[str]` — proper nouns / numbers in the claim that don't appear in the context.
- `GroundingResult.confidence_note: str` — always populated; clients should surface alongside the verdict.

**Validation results (against `C:/Users/hp/_mcp-validation-2026-05-06/test_output_vetter_v2.py` adversarial cases):**

| Test case | v1.2.1 | v1.3.0 |
|----------|:------:|:------:|
| Literal-overlap grounded | PASS | PASS |
| Paraphrase grounded (`mutates` ≈ `mutable`) | FAIL (false-fabricate) | **PASS** (stem-Jaccard + stop-word filter) |
| Inferred grounded (Python older than JS by dates) | FAIL (false-fabricate) | **PASS** (entity-overlap on `Python`/`JavaScript` is enough) |
| Eiffel-Tower-in-Berlin (entity misattribution) | FAIL (false-clean) | **PASS** (entity-mismatch flag on `Berlin`) |
| Honeybees-produce-silk (wrong subject, overlap vocabulary) | FAIL (false-clean) | FAIL (genuinely needs semantic; documented in confidence_note) |

**Net: 1/5 → 4/5.** ruff + mypy strict clean. 109 → 114 tests passing (+5 for v1.3 behavior).

**Why minor version bump:** types added new fields (`unsupported_entities`, `confidence_note`). Existing field shapes preserved — backward compatible reads. New fields default-populated so existing clients don't break.

## [1.2.1] — 2026-05-06

### Changed — overnight Phase 1A + 2A docs/test refresh

Bundles three commits already on main as a single PyPI republish:

- [`7574ba3`](https://github.com/temurkhan13/openclaw-output-vetter-mcp/commit/7574ba3) **test:** server-protocol coverage gap-fillers (Phase 1A). Tests-only — exercises handler registration + tool / resource / prompt routing paths that previously had only end-user-API coverage.
- [`1cb9912`](https://github.com/temurkhan13/openclaw-output-vetter-mcp/commit/1cb9912) **docs:** SPEC.md refresh covering the v1.1 (`verify_action_outcome`) and v1.2 (chained-claim parser) feature additions. Pre-existing SPEC was at v1.0-era surface; now matches what's shipped.
- [`e011cb9`](https://github.com/temurkhan13/openclaw-output-vetter-mcp/commit/e011cb9) **docs:** Related-section bundle reference now reads "7-pack" / "6 others" (was "5 others / 6-pack" mid-week as the suite grew).

No code or detection-rule changes. Patch bump republishes to PyPI so the SPEC + Related text matches the rendered "Project description" page.

## [1.2.0] — 2026-05-05

### Improved — claim parser handles chained / multi-target phrasings

`verify_action_outcome` now correctly splits chained file-creation/deletion claims into per-file assertions. Previously, "Created auth.py and helpers.py" only checked `auth.py` against the diff; "Removed old.py, legacy.py, and util.py" only checked `old.py`.

**v1.2 supports** (post-pass expansion after the primary verb-anchored regex match):
- "Created A and B" — splits into 2 assertions
- "Created A, B" — splits into 2
- "Created A, B, and C" — splits into 3
- "Removed A and B" / "Deleted A, B" — same shape for deletions
- "Wrote x.py and y.py" — terse phrasing without "file" keyword
- Sentence boundaries are respected: "Created auth.py. Then refactored helpers.py." does NOT chain `helpers.py` as a created assertion (the period before " Then" terminates the multi-target scan)

The expansion uses a separator regex `(?:\s*,\s*(?:and\s+)?|\s+and\s+)` followed by a filename-like token. Sentence boundaries are now period-or-question-or-exclamation **followed by whitespace or end-of-string** — periods inside filenames (`.py`, `.tsx`) no longer terminate the scan.

7 new tests added covering chain expansion + sentence-boundary protection. **92 total tests passing**, ruff + mypy strict clean.

### Architecture note
The improvement was identified in v1.1's release-day testing (one v1.1 test had to be rephrased to use 2 sentences instead of "and" because the parser couldn't chain). v1.2 fixes the parser instead of working around it. Listed in [[Pipeline/opportunities]] as the v1.2-backlog candidate; shipped same-day.

## [1.1.0] — 2026-05-05

### Added — action-outcome verifier (P10 ABSORB)

New 4th tool `verify_action_outcome(claim, before_snapshot, after_snapshot, expected_changes?)` that compares an agent's stated outcome against actual before/after state. This is the next layer below `review_transcript`'s `unverified-completion-claim` check:
- `review_transcript` flags claims with NO supporting tool calls (transcript-only).
- `verify_action_outcome` flags claims WITH tool calls whose **side effects don't match** what the agent said happened.

The targeted failure mode is the [@chiefofautism quote (158↑ / 11.5K views)](https://x.com/chiefofautism/status/2023151450503753972): *"...and it will do it confidently while telling you that he cleaned up the project structure"*. Plus the [Codex sandbox-escalation case](https://x.com/heynavtoor/status/2049202562373751162) — agent's chain of thought acknowledged the read-only constraint, then wrote to disk anyway.

**Snapshot shape (schema-loose):** caller passes arbitrary `dict[str, Any]`. Recognized keys when present:

  - `files: list[str]` — file paths in working dir; set-diff
  - `git_status: str | dict` — "clean" / "dirty" semantics
  - `git_tip` / `git_head` / `git_log_tip: str` — HEAD commit SHA
  - `tests_status` / `test_status: str | dict` — "pass" / "fail" or `{"passed": N, "failed": N}`
  - `read_only: bool` — caller-asserted no-write constraint; if True in before AND state changes → `STATE_VIOLATED_CONSTRAINT` (Codex case)

Other keys are tracked for general "did anything change?" diff-summary, but no claim-specific matchers run.

**Detection rules** (8 rule_ids under `ACTION_OUTCOME.*`):
- `STATE_UNCHANGED` (HIGH) — vague-completion claim + identical before/after snapshots (the chiefofautism case)
- `UNSUPPORTED_CLAIM` (HIGH) — claim references a specific filename that's not in the diff
- `TESTS_NOT_PASSING` (CRITICAL) — claim says tests pass; tests_status indicates failure
- `NO_COMMIT` (HIGH) — claim says committed/pushed/shipped; git_tip didn't change
- `UNCOMMITTED_CHANGES` (HIGH) — claim says repo is clean; git_status indicates dirty
- `STATE_VIOLATED_CONSTRAINT` (CRITICAL) — read_only=True asserted but state changed (Codex case)
- `MISSING_EXPECTED_CHANGE` (HIGH/MEDIUM) — caller-supplied `expected_changes` entry not satisfied
- `AMBIGUOUS_CLAIM` (MEDIUM) — claim is checkable but snapshots lack the relevant key

**Verdict ladder** (reuses existing `Verdict` enum):
- `CLEAN` — all extracted claim assertions match the diff
- `PARTIALLY_GROUNDED` — some match, some don't
- `FABRICATED` — diff actively contradicts the claim (state unchanged or constraint violated)
- `UNVERIFIED` — claim couldn't be parsed into testable assertions

### Added (other)
- New demo resource `vetter://demo/action-divergence` showing the chiefofautism case (claim says "I cleaned up the project structure"; before == after).
- New prompt `verify-this-action` walking through the new tool with snapshot-capture guidance.
- 32 new tests (`test_action_outcome.py`) covering claim parsing, diff matching, constraint checks, expected_changes, and verdict ladders. **85 total tests passing**, ruff + mypy strict clean.

### Notes on architecture
P10 was absorbed into output-vetter rather than promoted to a standalone product per the absorption-gate analysis in [[Pipeline/opportunities]]: 3-of-4 gate criteria match (same buyer, same channel, same marketing motion); 1-of-4 differs (paired-call shape vs single-call) but is acceptable since the new tool is the natural next-layer of v1.0's existing `review_transcript`. Reversible — can split into a standalone child package in v1.2 if Pass 8+ shows a buyer who wants reconciliation without grounding/swallowed-exceptions.

## [1.0.2] — 2026-05-05

### Changed — README refresh from Pass 7 sweep

- Added a research-grade citation: the Centre for Long-Term Resilience analyzed 183,420 conversations and found 698 real-world scheming incidents in 6 months (Oct 2025 – Mar 2026), with monthly incident rate growing 4.9× ([Nav Toor's thread, 10K views](https://x.com/heynavtoor/status/2049202503653425446)). Adds peer-grade weight to the failure modes the README enumerates.
- Added a verbatim quote from a working engineer ([@chiefofautism, 158↑ / 11.5K views](https://x.com/chiefofautism/status/2023151450503753972)) describing the most common pain shape in operator language: the *"confidently while telling you that he cleaned up the project structure"* half of the failure pair this server attacks.
- Added a fourth bullet under "What it does" describing the **stated-vs-actual divergence** pattern — agent's chain-of-thought acknowledges a constraint, then the action violates it (Codex sandbox-escalation example from the same CLTR data). Flagged as P10 candidate; v1.1 may extend with action-outcome reconciliation.
- Cross-linked to `bash-vet-mcp` for the destructive-command-vetting half of the failure pair.
- No code or scanner changes. Patch bump only.

## [1.0.1] — 2026-05-04

### Fixed
- pyproject.toml `description` field exceeded PyPI's 512-character limit on the `summary` metadata, causing the v1.0.0 upload to fail with `400 Bad Request: 'summary' field must be 512 characters or less`. Trimmed to under 512 chars while preserving the differentiator framing (sub-second / inline / MCP-native vs DeepEval/Phoenix/LangSmith). v1.0.0 was tagged but never landed on PyPI — v1.0.1 is the first published version.

## [1.0.0] — 2026-05-04 (tag-only, never landed on PyPI)

### Added

- Initial release with MCP protocol wiring + three pure-Python verification scanners.
- 3 tools: `verify_response_grounding` (claim-by-claim Jaccard overlap against retrieval context) + `find_swallowed_exceptions` (Python AST walk for try/except patterns that swallow errors or substitute mock data) + `review_transcript` (multi-turn pattern matching for unverified completion claims + cross-turn factual contradictions).
- 3 demo resources: `vetter://demo/grounded`, `vetter://demo/fabricated`, `vetter://demo/swallowed-exceptions` — pre-canned inputs so a Claude Desktop user can verify the protocol wiring without authoring sample data.
- 2 prompts: `verify-this-answer(threshold)`, `audit-this-code`.
- **`grounding` scanner** — sentence-level claim splitting + bag-of-words Jaccard overlap against context chunks. Threshold-tunable; CLEAN if all claims grounded, PARTIALLY_GROUNDED if mixed, FABRICATED if zero grounded, UNVERIFIED if input too short. Sub-second on typical agent answers (≤2 KB).
- **`swallowed-exceptions` scanner** — `ast.walk` for `Try` nodes; classifies handlers as `pass-only` (MEDIUM), `mock-substitution` (HIGH — return constant/dict/list literal or suspicious name like SAMPLE_DATA), `silent-log-and-return` (MEDIUM — print/logger then return without re-raising), `bare-except` (LOW). Returns line numbers + code excerpts. Graceful on unparseable input (returns UNVERIFIED with parse_error).
- **`transcript-review` scanner** — pattern matching for assistant turns containing completion verbs ("I've configured / set up / deployed / installed / wired / completed / ...") without any preceding tool-call evidence; cross-turn factual extraction (subject-verb-object triples) for contradiction detection.
- **GitHub Actions CI** — matrix testing on ubuntu/macos/windows × Python 3.11/3.12 + ruff + mypy strict-mode lint job.
- **GitHub Actions release workflow** — fires on `v*` tag push, verifies tag matches `pyproject.toml` version, builds + publishes to PyPI via Trusted Publishing.
- **`server.json`** for the official Model Context Protocol Registry submission.
- **40+ tests** across `test_grounding.py` (10) + `test_swallowed_exceptions.py` (12) + `test_transcript.py` (8) + `test_server.py` (12) — claim splitting + Jaccard math + AST classification per pattern + dispatch correctness + protocol-wiring registration.

### Pipeline lineage

This is P06 in the venture Pipeline (`Pipeline/opportunities.md` — see vault). Surfaced from r/ClaudeAI silent-fake-success thread (Pass 2) + r/AI_Agents 0-meeting agent thread (Pass 2) + r/SaaS hallucination thread (Pass 3) — three independent buyer-vocabulary citations clearing the ≥3 graduation gate. Incumbent-validated against DeepEval (Pass 4) — DeepEval's MCP is eval-pipeline-orchestration scope (run named eval suites, inspect dataset history); P06's differentiator is single-transcript inline scope (verify THIS conversation right now). Different surface, same metric philosophy.

[Unreleased]: https://github.com/temurkhan13/openclaw-output-vetter-mcp/compare/v1.3.0...HEAD
[1.3.0]: https://github.com/temurkhan13/openclaw-output-vetter-mcp/compare/v1.2.1...v1.3.0
[1.2.1]: https://github.com/temurkhan13/openclaw-output-vetter-mcp/compare/v1.2.0...v1.2.1
[1.2.0]: https://github.com/temurkhan13/openclaw-output-vetter-mcp/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/temurkhan13/openclaw-output-vetter-mcp/compare/v1.0.2...v1.1.0
[1.0.2]: https://github.com/temurkhan13/openclaw-output-vetter-mcp/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/temurkhan13/openclaw-output-vetter-mcp/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/temurkhan13/openclaw-output-vetter-mcp/releases/tag/v1.0.0
