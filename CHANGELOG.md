# Changelog

All notable changes to `openclaw-output-vetter-mcp` are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.0.0] — 2026-05-04

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

[Unreleased]: https://github.com/temurkhan13/openclaw-output-vetter-mcp/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/temurkhan13/openclaw-output-vetter-mcp/releases/tag/v1.0.0
