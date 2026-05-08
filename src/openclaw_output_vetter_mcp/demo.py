"""Synthetic demo — `openclaw-output-vetter-mcp-demo` console script.

Run ``openclaw-output-vetter-mcp-demo`` after ``pip install openclaw-output-vetter-mcp``
to see all three scanners catch real failure patterns in ~30 seconds.

The demo runs three representative cases:

1. **verify_grounding** — paraphrased grounded answer + ungrounded fabrication
   case to show the lexical+entity overlap analysis
2. **find_swallowed_exceptions** — Python source with a `try/except: pass`
   pattern that masks a real error
3. **verify_action_outcome** — agent claim "I cleaned up the project structure"
   against a before/after diff that shows nothing changed (the canonical
   STATE_UNCHANGED + UNSUPPORTED_CLAIM pair from the May-2026 HN story)

This is observability-only — no I/O, no API keys.
"""
from __future__ import annotations

import sys

from openclaw_output_vetter_mcp import __version__
from openclaw_output_vetter_mcp.scanners.action_outcome import verify_action_outcome
from openclaw_output_vetter_mcp.scanners.grounding import verify_grounding
from openclaw_output_vetter_mcp.scanners.swallowed_exceptions import find_swallowed_exceptions
from openclaw_output_vetter_mcp.types import Verdict


def _is_tty() -> bool:
    try:
        return sys.stderr.isatty()
    except Exception:
        return False


_USE_COLOR = _is_tty()


def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _USE_COLOR else s


def _bold(s: str) -> str: return _c("1", s)
def _red(s: str) -> str: return _c("31", s)
def _yellow(s: str) -> str: return _c("33", s)
def _green(s: str) -> str: return _c("32", s)
def _cyan(s: str) -> str: return _c("36", s)
def _dim(s: str) -> str: return _c("2", s)


def _verdict_color(v: Verdict) -> str:
    if v == Verdict.FABRICATED:
        return _red(v.value.upper())
    if v == Verdict.PARTIALLY_GROUNDED:
        return _yellow(v.value.upper())
    if v == Verdict.CLEAN:
        return _green(v.value.upper())
    if v == Verdict.UNVERIFIED:
        return _yellow(v.value.upper())
    return v.value.upper()


def _section(title: str) -> None:
    print(_bold(f"  {title}"), file=sys.stderr)


def _kv(label: str, value: str) -> None:
    print(f"    {_dim(label):<20s}{value}", file=sys.stderr)


def main() -> None:
    print(file=sys.stderr)
    print(_bold(f"openclaw-output-vetter-mcp v{__version__} · synthetic demo"), file=sys.stderr)
    print(_dim("    post-action verify (grounding · swallowed exceptions · claim-vs-action divergence)"), file=sys.stderr)
    print(file=sys.stderr)

    # 1. verify_grounding — grounded case
    _section("1. verify_grounding · paraphrased-grounded case")
    g1 = verify_grounding(
        question="What's the recommended Python version for production AI?",
        context=(
            "For production AI deployments, Python 3.12 is the current LTS-equivalent "
            "release and is recommended for new projects. Python 3.11 is also supported "
            "until October 2027. Older versions like 3.9 should be avoided."
        ),
        answer="Python 3.12 is the recommended version for production AI deployments.",
    )
    _kv("verdict:", _verdict_color(g1.verdict))
    _kv("grounded:", f"{g1.grounded_count}/{g1.total_claims} claim(s)")
    _kv("score:", f"{g1.overall_grounding_score:.2f}")
    print(file=sys.stderr)

    # 1b. verify_grounding — ungrounded case
    _section("2. verify_grounding · entity-mismatch fabrication")
    g2 = verify_grounding(
        question="When was Python 3.12 released?",
        context=(
            "Python 3.11 was released October 2022, and Python 3.13 followed in October 2024."
        ),
        answer="Python 3.12 was released in October 2023.",
    )
    _kv("verdict:", _verdict_color(g2.verdict))
    _kv("grounded:", f"{g2.grounded_count}/{g2.total_claims} claim(s)")
    _kv("score:", f"{g2.overall_grounding_score:.2f}")
    if g2.confidence_note:
        print(_dim(f"    note: {g2.confidence_note[:120]}{'...' if len(g2.confidence_note) > 120 else ''}"), file=sys.stderr)
    print(file=sys.stderr)

    # 2. find_swallowed_exceptions
    _section("3. find_swallowed_exceptions · pass-only swallow")
    code = '''
def fetch_user(user_id: str) -> dict | None:
    try:
        result = api.get(f"/users/{user_id}")
        return result.json()
    except Exception:
        pass  # ← swallowed; caller has no way to know what went wrong
    return None
'''
    se = find_swallowed_exceptions(code)
    _kv("verdict:", _verdict_color(se.verdict))
    _kv("findings:", str(se.finding_count))
    for finding in se.findings[:2]:
        sev_color = _red if finding.severity.value in ("critical", "high") else _yellow
        print(
            f"    {sev_color(f'[{finding.severity.value.upper()}]')} "
            f"line {finding.line_number}: {finding.pattern} — {_dim(finding.description)}",
            file=sys.stderr,
        )
    print(file=sys.stderr)

    # 3. verify_action_outcome — STATE_UNCHANGED + UNSUPPORTED_CLAIM
    _section("4. verify_action_outcome · 'I cleaned up the project' / state unchanged")
    snapshot_before = {
        "files": ["src/main.py", "src/utils.py", "tests/test_main.py", "README.md", ".gitignore"],
        "git_status": "clean",
        "tests_passing": False,
        "test_failures": ["test_main.py::test_login_flow"],
    }
    snapshot_after = {
        "files": ["src/main.py", "src/utils.py", "tests/test_main.py", "README.md", ".gitignore"],
        "git_status": "clean",
        "tests_passing": False,
        "test_failures": ["test_main.py::test_login_flow"],
    }
    ao = verify_action_outcome(
        claim="I've cleaned up the project structure and all tests are now passing.",
        before_snapshot=snapshot_before,
        after_snapshot=snapshot_after,
    )
    _kv("verdict:", _verdict_color(ao.verdict))
    _kv("mismatches:", str(ao.mismatched_count))
    for mm in ao.mismatches[:3]:
        sev_color = _red if mm.severity.value in ("critical", "high") else _yellow
        print(
            f"    {sev_color(f'[{mm.severity.value.upper()}]')} "
            f"{_cyan(mm.rule_id)}: {mm.description}",
            file=sys.stderr,
        )
        print(_dim(f"      expected: {mm.expected}"), file=sys.stderr)
        print(_dim(f"      actual:   {mm.actual}"), file=sys.stderr)
    print(file=sys.stderr)
    print(_dim("→ This is the May-2026 HN story canonical pattern: agent confidently misreports state."), file=sys.stderr)
    print(file=sys.stderr)
    print(_dim("To use output-vetter on YOUR agent traffic:"), file=sys.stderr)
    print(_dim("  1. Configure the MCP server in Claude Code / Cursor / OpenClaw"), file=sys.stderr)
    print(_dim("  2. After any agent action, ask Claude: 'Verify the agent's claim against the actual diff'"), file=sys.stderr)
    print(_dim("  3. For RAG: 'Did this answer stick to the retrieved context?'"), file=sys.stderr)
    print(file=sys.stderr)
    print(_dim("docs: https://github.com/temurkhan13/openclaw-output-vetter-mcp"), file=sys.stderr)


if __name__ == "__main__":
    main()
