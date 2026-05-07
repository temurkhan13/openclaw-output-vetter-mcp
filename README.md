# openclaw-output-vetter-mcp

<!-- mcp-name: io.github.temurkhan13/openclaw-output-vetter-mcp -->

> **MCP server for verifying AI agent claims vs reality** — single-transcript inline grounding-check that flags when an agent's response states facts not in the input context, when its code silently swallows exceptions and substitutes mock data, or when its multi-turn transcript contains contradictions or unverified completion claims. **Sub-second, local, free, MCP-native** — designed to be called inline from Claude Code / Cursor / Cline / OpenClaw agents during the conversation, not as a separate eval-pipeline. The lightweight complement to dashboard-based eval frameworks (DeepEval, Phoenix, LangSmith).

[![Status: v1.3.0](https://img.shields.io/badge/status-v1.3.0-brightgreen)](https://github.com/temurkhan13/openclaw-output-vetter-mcp) [![Tests: 114 passing](https://img.shields.io/badge/tests-114%20passing-brightgreen)](./tests) [![License: MIT](https://img.shields.io/badge/license-MIT-blue)](./LICENSE) [![MCP](https://img.shields.io/badge/protocol-MCP-purple)](https://modelcontextprotocol.io/) [![PyPI](https://img.shields.io/pypi/v/openclaw-output-vetter-mcp)](https://pypi.org/project/openclaw-output-vetter-mcp/)

---

## What it does

Production AI agents fail in three quiet ways that pass every standard dashboard. **As of mid-2026 the failure modes are now research-grade: the [Centre for Long-Term Resilience analyzed 183,420 conversations and found 698 real-world scheming incidents in just 6 months (Oct 2025 – Mar 2026), with monthly incident rate growing 4.9×](https://x.com/heynavtoor/status/2049202503653425446)** — *"AI was caught lying to users, ignoring direct instructions, breaking its own safety guardrails, and pursuing goals in ways that caused real harm"*. This MCP catches the inline-conversation surface of that pattern.

A working engineer ([@chiefofautism, 158↑ / 135 RTs / 11.5K views](https://x.com/chiefofautism/status/2023151450503753972)) describes the most common variant in one line:

> *"claude code runs shell commands with YOUR permissions. it can rm -rf your repo. it can force push to main. it can drop your database. and it will do it confidently while telling you that he cleaned up the project structure"*

That second half — *"while telling you that he cleaned up the project structure"* — is exactly the hallucinated-completion-claim surface this server checks. Pair with [bash-vet-mcp](https://github.com/temurkhan13/bash-vet-mcp) for the first half (catches the destructive command before it runs).

- **Hallucinated claims.** [r/SaaS founder thread (May 2 2026)](https://old.reddit.com/r/SaaS/comments/1t161qg/we_caught_1_in_50_ai_responses_hallucinating_in/) verbatim: *"Status 200, latency normal, tokens normal. A hallucinated response looks identical to a good one in every standard dashboard."* The fix the founder describes is exactly what this MCP server provides: *"a lightweight check that flags when the model states something not in the input context."*
- **Silent fake success in agent-written code.** [r/ClaudeAI thread (509 pts, 186 comments)](https://old.reddit.com/r/ClaudeAI/comments/1sdmohb/after_months_with_claude_code_the_biggest_time/) verbatim: *"The agent couldn't get auth working, so it quietly inserted a try/catch that returns sample data on failure. The output you saw on day one was never real."*
- **Unverified completion claims.** [r/AI_Agents (114 pts)](https://old.reddit.com/r/AI_Agents/comments/1skxyrx/my_clients_ai_sales_agent_booked_0_meetings_in_2/) — agent self-reports completion ("I've configured X"); reality at outcome level (booked meetings, deployed services, working integrations) doesn't match.
- **Stated-vs-actual divergence.** Newer pattern surfaced in the same CLTR data: agent's own chain-of-thought *acknowledges* a constraint, then violates it. The Codex example (cited in [Nav Toor's thread](https://x.com/heynavtoor/status/2049202562373751162)): *"OpenAI Codex was running in read-only sandbox mode. It explicitly noted the read-only constraint in its own chain of thought. Then it escalated permissions and wrote to disk anyway."* **Covered in v1.1** by the new `verify_action_outcome` tool — pass `read_only: true` in the before-snapshot and any state change in the after-snapshot triggers `ACTION_OUTCOME.STATE_VIOLATED_CONSTRAINT` (CRITICAL).
- **The "agent's confession" pattern.** [HN: "An AI agent deleted our production database. The agent's confession is below" (859↑ / 1,030 comments, May 2026)](https://news.ycombinator.com/item?id=47911524) — `jeremyccrane` documented an agent issuing a destructive GraphQL mutation that wiped a production volume, then confessing to it after the fact. The community-sized-up failure mode: *"It's a privilege issue, not an execution issue."* The agent's after-the-fact confession is canonical output-action divergence — covered in v1.1 by `verify_action_outcome(claim, before_snapshot, after_snapshot)`. Pair with [bash-vet-mcp](https://github.com/temurkhan13/bash-vet-mcp) for the first half (block the destructive curl-with-mutation at command-approval time, before it runs).

This MCP server runs **three pure-Python checks inline during the conversation** — no API key, no LLM-as-judge cost, sub-second:

```
> claude: did your last answer hallucinate anything?
[MCP tool: verify_response_grounding]

verdict: FABRICATED
ungrounded_count: 3
overall_grounding_score: 0.08
ungrounded claims:
  - "Pixelette Technologies has raised $12M in Series A funding" (overlap 0.04)
  - "led by Sequoia Capital" (overlap 0.00)
  - "47 full-time employees" (overlap 0.00)

summary: All 3 claim(s) lack grounding in the input context — likely hallucinated.
```

```
> claude: scan the code you just wrote for swallowed-exception patterns.
[MCP tool: find_swallowed_exceptions]

verdict: FABRICATED (one HIGH-severity finding)
findings:
  [HIGH] Line 12 — mock-substitution
    except Exception:
        return {"id": 1, "name": "sample"}
    Description: except handler returns fabricated/mock data instead of re-raising
    — the call site sees a 'successful' response built from constants. This is the
    silent-fake-success pattern.

summary: 1 swallowing pattern detected — at least one returns fabricated data.
```

```
> claude: review the agent's transcript so far.
[MCP tool: review_transcript]

verdict: FABRICATED
issue_count: 2
issues:
  [HIGH] turns [3] — unverified-completion-claim
    "I've configured the gateway and verified everything works."
    Description: assistant claims completion of an action but no tool calls are
    present in this turn or earlier turns.
  [MEDIUM] turns [2, 7] — cross-turn-contradiction
    Cross-turn factual drift on subject 'the api':
    turn 2 says 'returns json for every request',
    turn 7 says 'returns xml for legacy endpoints'.

summary: Reviewed 8 turn(s); flagged 2 issue(s) including unverified completion
claim(s) — investigate before trusting the transcript.
```

---

## Why `openclaw-output-vetter-mcp`

Three things existing eval frameworks (DeepEval, Phoenix, LangSmith, Galileo, Langfuse) don't do well together:

1. **Inline single-transcript scope, not eval-pipeline orchestration.** [DeepEval ships an MCP server](https://deepeval.com/docs/evaluation-mcp) — but its scope is *"run evals, pull datasets, and inspect traces straight from claude code, cursor"* (verbatim from their docs). That's eval-pipeline orchestration: schedule a named eval suite against a stored dataset; review trace history. **This server is the opposite shape: verify *this specific conversation* right now, before the user sees the response.** Same metric stack philosophically (faithfulness, grounding); different surface.

2. **Sub-second + local + free.** No LLM-as-judge call, no API key, no per-call cost. Pure-Python claim splitting + stem-Jaccard overlap + entity-mismatch detection + AST walking. **Honest about what lexical methods catch and what they don't** — see "Grounding-scanner limitations" below. For high-frequency inline use (every assistant turn) the speed-vs-accuracy tradeoff favors lightweight. The roadmap offers optional DeepEval-LLM-as-judge mode for users who want semantic-level verification on top of the lexical layer.

3. **Three checks for three distinct failure modes, not one umbrella metric.**
   - *Grounding* (`verify_response_grounding`) catches hallucinated facts
   - *Swallowed exceptions* (`find_swallowed_exceptions`) catches silent-fake-success in agent-written code
   - *Transcript review* (`review_transcript`) catches unverified completion claims + cross-turn drift

   Other tools collapse all three into "faithfulness." The failure modes are different and the corrective actions are different. Surfacing them separately makes the response actionable.

### Grounding-scanner limitations (read this)

The grounding scanner is **lexical** — it computes stem-level token overlap (Jaccard) between claim and context, plus an entity-coverage check on proper nouns / numbers. Two signals, combined for the verdict.

**What it CATCHES:**
- Direct fabrication (claim has zero meaningful overlap with context)
- Paraphrased grounded claims that share stems with context (`mutates` ≈ `mutating` ≈ `mutation`)
- **Entity misattribution** — claim names a proper noun or number that isn't in the context (e.g. *"The Eiffel Tower is in Berlin"* against a context that mentions Paris — vocabulary overlaps but `Berlin` is flagged as unsupported)

**What it DOES NOT CATCH** (the response always ships a `confidence_note` repeating this — surface it to the operator):
- **Inferred claims requiring world knowledge** — *"Python is older than JavaScript"* given dates 1991/1995 in context. The inference is correct but the word "older" doesn't appear; lexical methods can't infer ordering from facts.
- **Vocabulary-overlap fabrications where the wrong subject is associated with the right object** — *"Honeybees produce silk"* against a context that mentions both `honeybees` and `silk` separately. Bag-of-stems sees the overlap and concludes grounded; only relation-level parsing or NLI can catch this.

For those failure modes, pair this tool with an LLM-as-judge or NLI verifier. We keep this scanner pure-Python + sub-second so it can run inline on every agent response — the right move is a *layered* approach (this for fast inline, LLM-as-judge for periodic deep-check).

We chose to ship the lexical scanner with explicit limit-disclosure rather than a heavyweight semantic dependency that breaks the "sub-second / no API key" pitch. Validation results: 4 of 5 adversarial test cases pass; the 1 remaining failure is the wrong-subject-overlap case described above.

Built for the **production AI operator** who's already using Claude Code / Cursor / Cline / OpenClaw and wants a defensive layer the agent calls before its response goes user-facing.

---

## Tool surface

| Tool | What it returns |
|------|-----------------|
| `verify_response_grounding` | Per-claim grounded/ungrounded + overall verdict (CLEAN / PARTIALLY_GROUNDED / FABRICATED) + stem-Jaccard overlap scores + per-claim `unsupported_entities` (proper nouns / numbers in the claim that don't appear in context — catches misattribution like `Eiffel Tower is in Berlin` against a Paris-context) + `confidence_note` documenting scanner limits |
| `find_swallowed_exceptions` | Per-finding line number + pattern (`pass-only` / `mock-substitution` / `silent-log-and-return` / `bare-except`) + severity + code excerpt |
| `review_transcript` | Per-issue turn indices + issue kind (`unverified-completion-claim` / `cross-turn-contradiction`) + severity + evidence excerpt |
| `verify_action_outcome` *(v1.1+)* | **NEW** — compare an agent's stated outcome against actual before/after state snapshots. Catches the [@chiefofautism, 158↑] case (agent says *"I cleaned up the project structure"* when nothing changed) + the Codex sandbox-escalation case (read-only constraint asserted, then violated). 8 detection rules under `ACTION_OUTCOME.*`. Pure function — caller captures snapshots; server stays stateless. |

Resources:
- `vetter://demo/grounded` — sample CLEAN grounding result
- `vetter://demo/fabricated` — sample FABRICATED grounding result
- `vetter://demo/swallowed-exceptions` — sample swallowed-exception scan
- `vetter://demo/action-divergence` *(v1.1+)* — sample FABRICATED action-outcome verdict (claim says "cleaned up" but before == after)

Prompts:
- `verify-this-answer(threshold)` — walks `verify_response_grounding` on the most recent assistant answer
- `audit-this-code` — walks `find_swallowed_exceptions` on a code block + explains each finding's risk
- `verify-this-action` *(v1.1+)* — walks `verify_action_outcome` with snapshot-capture guidance + per-mismatch interpretation

---

## Quickstart

### Install

```bash
pip install openclaw-output-vetter-mcp
```

### Configure for Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "openclaw-output-vetter": {
      "command": "python",
      "args": ["-m", "openclaw_output_vetter_mcp"]
    }
  }
}
```

Restart Claude Desktop. Test:

> Resource `vetter://demo/grounded` — read it back to me.

The demo resource returns a sample GroundingResult so you can verify the protocol wiring without authoring inputs.

---

## Roadmap

| Version | Scope | Status |
|---------|-------|--------|
| v1.0 | 3 scanners (grounding via Jaccard / swallowed-exceptions via AST / transcript review via pattern matching), 3 tools / 3 demo resources / 2 prompts, GitHub Actions CI matrix, PyPI Trusted Publishing, MCP Registry submission, 40+ tests | ✅ |
| v1.1 | Optional LLM-as-judge backend (wraps DeepEval's `FaithfulnessMetric` / `HallucinationMetric` for higher-quality grounding); embedding-based similarity option (sentence-transformers); custom claim-extraction prompts | ⏳ |
| v1.2 | Backend-pluggable architecture (per-tool backend selection); incremental review (verify only the last N turns); persistent issue tracking across multi-session work | ⏳ |
| v1.x | Webhook emit on FABRICATED verdict; integration with CI to gate AI-generated PRs that fail grounding checks | ⏳ |

---

## Need this adapted to your stack?

If your AI deployment uses a different agent harness, custom claim-extraction prompts, language other than Python for the swallowed-exception scanner, or specific compliance / auditing requirements — that's a **Custom MCP Build** engagement.

| Tier | Scope | Investment | Timeline |
|------|-------|------------|----------|
| Simple | Custom claim-extraction prompts + tuned thresholds for your domain | **$8,000–$10,000** | 1–2 weeks |
| Standard | Multi-language swallowed-exception scanners (TypeScript / Go / Rust AST walks) + custom severity rules | **$15,000–$25,000** | 2–4 weeks |
| Complex | LLM-as-judge backend with your hosted model + persistence + CI integration + audit-trail | **$30,000–$45,000** | 4–8 weeks |

**To engage:**
1. Email **temur@pixelette.tech** with subject `Custom MCP Build inquiry — output verification`
2. Include: 1-paragraph description of your stack + which tier
3. Reply within 2 business days with a 30-min discovery call slot

This server is part of a **production-AI infrastructure MCP suite** — companion to [silentwatch-mcp](https://github.com/temurkhan13/silentwatch-mcp) (cron silent-failure detection), [openclaw-health-mcp](https://github.com/temurkhan13/openclaw-health-mcp) (deployment health), [openclaw-cost-tracker-mcp](https://github.com/temurkhan13/openclaw-cost-tracker-mcp) (token-cost telemetry + 429 prediction), [openclaw-skill-vetter-mcp](https://github.com/temurkhan13/openclaw-skill-vetter-mcp) (skill security vetting), and [openclaw-upgrade-orchestrator-mcp](https://github.com/temurkhan13/openclaw-upgrade-orchestrator-mcp) (upgrade safety + provider-side regression detection). Install all six for full operational visibility.

---

## Production AI audits

If you're running production AI and want an outside practitioner to score readiness, find the failure patterns already present (silent fake success being pattern P3.x in the catalog), and write the corrective-action plan:

| Tier | Scope | Investment | Timeline |
|------|-------|------------|----------|
| Audit Lite | One system, top-5 findings, written report | **$1,500** | 1 week |
| Audit Standard | Full audit, all 14 patterns, 5 Cs findings, 90-day follow-up | **$3,000** | 2–3 weeks |
| Audit + Workshop | Standard audit + 2-day team workshop + first monthly audit included | **$7,500** | 3–4 weeks |

Same email channel: **temur@pixelette.tech** with subject `AI audit inquiry`.

---

## Contributing

PRs welcome. The three scanners are intentionally pluggable — each lives in its own module under `src/openclaw_output_vetter_mcp/scanners/` and is a pure function over input → typed result. Adding a new scanner is one file + one test file + one tool registration in `server.py`.

Bug reports + feature requests: open a GitHub issue.

---

## License

MIT — see [LICENSE](./LICENSE).

---

## Related

- [Production-AI MCP Suite (Gumroad bundle)](https://temurah.gumroad.com/l/production-ai-mcp-suite) — this server plus 6 others in one curated 7-pack bundle
- [silentwatch-mcp](https://github.com/temurkhan13/silentwatch-mcp) — cron silent-failure detection
- [openclaw-health-mcp](https://github.com/temurkhan13/openclaw-health-mcp) — deployment health
- [openclaw-cost-tracker-mcp](https://github.com/temurkhan13/openclaw-cost-tracker-mcp) — token-cost telemetry + 429 prediction (v1.1+)
- [openclaw-skill-vetter-mcp](https://github.com/temurkhan13/openclaw-skill-vetter-mcp) — skill security vetting
- [openclaw-upgrade-orchestrator-mcp](https://github.com/temurkhan13/openclaw-upgrade-orchestrator-mcp) — upgrade safety + provider-side regression detection (v1.2+)
- [AI Production Discipline Framework](https://temurah.gumroad.com/l/ai-production-discipline-framework) — Notion template, $29 — the methodology these MCP tools implement
- [SPEC.md](./SPEC.md) — full server design

---

Built by [Temur Khan](https://www.notion.so/@temurkhan) — independent practitioner on production AI systems.
Contact: **temur@pixelette.tech**
