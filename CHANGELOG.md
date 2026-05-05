# Changelog

All notable changes to `openclaw-output-vetter-mcp` are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

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

[Unreleased]: https://github.com/temurkhan13/openclaw-output-vetter-mcp/compare/v1.0.1...HEAD
[1.0.1]: https://github.com/temurkhan13/openclaw-output-vetter-mcp/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/temurkhan13/openclaw-output-vetter-mcp/releases/tag/v1.0.0
