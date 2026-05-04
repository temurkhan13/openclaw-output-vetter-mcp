"""AST-based detector for swallowed-exception + mock-substitution patterns.

Catches the specific failure mode in [r/ClaudeAI silent fake success thread
(509 pts)](https://old.reddit.com/r/ClaudeAI/comments/1sdmohb/after_months_with_claude_code_the_biggest_time/):
the agent inserts `try/except` that returns sample data on auth failure, the
output looks correct on day one, the integration was broken from the start.

Patterns flagged:
  - `pass-only`: `except: pass` or `except E: pass` (silently absorbs error).
  - `mock-substitution`: `except: return <constant-or-dict-literal>` (returns
    fabricated data).
  - `silent-log-and-return`: logs + returns fabricated data without re-raising.
  - `bare-except`: `except:` with no exception type (catches everything).
"""
from __future__ import annotations

import ast

from openclaw_output_vetter_mcp.types import (
    Severity,
    SwallowedExceptionFinding,
    SwallowedExceptionReport,
    Verdict,
)

_SUSPICIOUS_NAMES = {
    "sample",
    "mock",
    "default",
    "placeholder",
    "fake",
    "dummy",
    "fixture",
    "test",
    "example",
}


def _excerpt_from_lines(lines: list[str], start_lineno: int, end_lineno: int) -> str:
    """Return the source span [start, end] truncated to 250 chars."""
    if not lines:
        return ""
    start = max(0, start_lineno - 1)
    end = min(len(lines), max(end_lineno, start_lineno))
    snippet = "\n".join(lines[start:end])
    if len(snippet) > 250:
        snippet = snippet[:247] + "..."
    return snippet


def _is_pass_only(handler: ast.ExceptHandler) -> bool:
    return all(isinstance(stmt, ast.Pass) for stmt in handler.body)


def _is_log_and_return(handler: ast.ExceptHandler) -> bool:
    """Logs (or prints) then returns without re-raising."""
    has_log = False
    has_return = False
    has_raise = False
    for stmt in handler.body:
        if isinstance(stmt, ast.Raise):
            has_raise = True
        if isinstance(stmt, ast.Return):
            has_return = True
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            func = stmt.value.func
            name = ""
            if isinstance(func, ast.Name):
                name = func.id.lower()
            elif isinstance(func, ast.Attribute):
                name = func.attr.lower()
            if name in {"print", "log", "logger", "logging", "info", "warn", "warning", "error", "debug"}:
                has_log = True
    return has_log and has_return and not has_raise


def _looks_like_mock_data(value: ast.expr) -> bool:
    """Detect dict/list literals or constants that look like fabricated mock data.

    Excludes booleans, small ints (≤2), and None — those are legitimate fallback
    returns. Flags only structures that look like fabricated payloads (dict
    literals, non-empty list literals, strings >5 chars, suspicious names).
    """
    if isinstance(value, ast.Dict):
        return True
    if isinstance(value, ast.List):
        return len(value.elts) > 0
    if isinstance(value, ast.Constant):
        v = value.value
        # Booleans + None + small ints are legit fallback returns, not mock data
        if v is None or isinstance(v, bool):
            return False
        if isinstance(v, int | float) and abs(v) <= 2:
            return False
        return not (isinstance(v, str) and len(v) <= 5)
    if isinstance(value, ast.Name):
        name_lower = value.id.lower()
        # Substring match — catches SAMPLE_DATA, MOCK_RESPONSE, FAKE_USER, etc.
        return any(s in name_lower for s in _SUSPICIOUS_NAMES)
    if isinstance(value, ast.Attribute):
        # e.g., self.SAMPLE_DATA or constants.MOCK_RESPONSE
        attr_lower = value.attr.lower()
        return any(s in attr_lower for s in _SUSPICIOUS_NAMES)
    if isinstance(value, ast.Call):
        # e.g., dict(...), list(...) constructors
        func = value.func
        if isinstance(func, ast.Name) and func.id in {"dict", "list", "tuple", "set"}:
            return True
    return False


def _is_mock_substitution(handler: ast.ExceptHandler) -> bool:
    has_raise = any(isinstance(stmt, ast.Raise) for stmt in handler.body)
    if has_raise:
        return False
    for stmt in handler.body:
        if isinstance(stmt, ast.Return) and stmt.value is not None and _looks_like_mock_data(stmt.value):
            return True
    return False


def _is_bare_except(handler: ast.ExceptHandler) -> bool:
    return handler.type is None


def _classify(handler: ast.ExceptHandler) -> tuple[str, Severity, str]:
    """Return (pattern, severity, description) for a swallowing handler.

    Higher-severity patterns are checked first so a single handler maps to its
    most-damaging classification.
    """
    if _is_mock_substitution(handler):
        return (
            "mock-substitution",
            Severity.HIGH,
            (
                "except handler returns fabricated/mock data instead of re-raising — "
                "the call site sees a 'successful' response built from constants, dict literals, "
                "or names like 'sample' / 'default' / 'mock'. This is the silent-fake-success pattern."
            ),
        )
    if _is_log_and_return(handler):
        return (
            "silent-log-and-return",
            Severity.MEDIUM,
            (
                "except handler logs the error then returns without re-raising — caller "
                "won't know the operation failed. Either re-raise or have the caller check a flag."
            ),
        )
    if _is_pass_only(handler):
        return (
            "pass-only",
            Severity.MEDIUM,
            (
                "except: pass silently swallows the exception. If this is intentional "
                "(e.g., best-effort cleanup), document it; otherwise let the caller see the error."
            ),
        )
    if _is_bare_except(handler):
        return (
            "bare-except",
            Severity.LOW,
            (
                "Bare `except:` catches every exception type including SystemExit + KeyboardInterrupt. "
                "Specify the exception types you actually expect."
            ),
        )
    return ("", Severity.INFO, "")


def find_swallowed_exceptions(code: str) -> SwallowedExceptionReport:
    """Scan Python source code for try/except patterns that swallow errors.

    Skips graceful: returns parse_error in the report when input is unparseable.
    """
    if not code.strip():
        return SwallowedExceptionReport(
            verdict=Verdict.UNVERIFIED,
            finding_count=0,
            findings=[],
            summary="No code provided.",
            parse_error=None,
        )

    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return SwallowedExceptionReport(
            verdict=Verdict.UNVERIFIED,
            finding_count=0,
            findings=[],
            summary="Could not parse the input as Python — verdict UNVERIFIED.",
            parse_error=f"{type(exc).__name__}: {exc.msg} at line {exc.lineno}",
        )

    lines = code.splitlines()
    findings: list[SwallowedExceptionFinding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        for handler in node.handlers:
            pattern, severity, description = _classify(handler)
            if not pattern:
                continue
            start = handler.lineno
            end = handler.end_lineno or handler.lineno
            findings.append(
                SwallowedExceptionFinding(
                    severity=severity,
                    line_number=start,
                    pattern=pattern,
                    code_excerpt=_excerpt_from_lines(lines, start, end),
                    description=description,
                )
            )

    if not findings:
        verdict = Verdict.CLEAN
        summary = "No swallowed-exception patterns detected."
    else:
        # Verdict ladder: any HIGH → FABRICATED-equivalent; mixed → PARTIALLY_GROUNDED
        any_high = any(f.severity in {Severity.HIGH, Severity.CRITICAL} for f in findings)
        verdict = Verdict.FABRICATED if any_high else Verdict.PARTIALLY_GROUNDED
        summary = (
            f"{len(findings)} swallowing pattern(s) detected"
            + (
                " — at least one returns fabricated data (mock-substitution)."
                if any_high
                else " — review whether each is intentional."
            )
        )

    findings.sort(key=lambda f: f.line_number)
    return SwallowedExceptionReport(
        verdict=verdict,
        finding_count=len(findings),
        findings=findings,
        summary=summary,
        parse_error=None,
    )
