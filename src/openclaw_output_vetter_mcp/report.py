"""`openclaw-output-vetter-mcp-report` console script — markdown verification report.

Runs the same representative cases as the demo (grounding + swallowed-exceptions
+ action-outcome) but renders to GitHub-flavored markdown on stdout instead of
ANSI-styled stderr. Use the demo for a tour; use the report for piping into a
doc, posting to Slack, opening a GitHub issue, or rendering inline in a chat.
"""
from __future__ import annotations

import sys

from openclaw_output_vetter_mcp import __version__
from openclaw_output_vetter_mcp.render import render_output_vet_report
from openclaw_output_vetter_mcp.scanners.action_outcome import verify_action_outcome
from openclaw_output_vetter_mcp.scanners.grounding import verify_grounding
from openclaw_output_vetter_mcp.scanners.swallowed_exceptions import find_swallowed_exceptions


def _build_grounding_cases():
    g_clean = verify_grounding(
        question="What's the recommended Python version for production AI?",
        context=(
            "For production AI deployments, Python 3.12 is the current LTS-equivalent "
            "release and is recommended for new projects. Python 3.11 is also supported "
            "until October 2027. Older versions like 3.9 should be avoided."
        ),
        answer="Python 3.12 is the recommended version for production AI deployments.",
    )
    g_fabricated = verify_grounding(
        question="When was Python 3.12 released?",
        context="Python 3.11 was released October 2022, and Python 3.13 followed in October 2024.",
        answer="Python 3.12 was released in October 2023.",
    )
    return [
        ("Paraphrased grounded answer (control)", g_clean),
        ("Entity-mismatch fabrication", g_fabricated),
    ]


def _build_swallowed_cases():
    code = '''
def fetch_user(user_id: str) -> dict | None:
    try:
        result = api.get(f"/users/{user_id}")
        return result.json()
    except Exception:
        pass  # ← swallowed; caller has no way to know what went wrong
    return None
'''
    return [("Pass-only swallow in fetch_user", find_swallowed_exceptions(code))]


def _build_outcome_cases():
    snapshot_before = {
        "files": ["src/main.py", "src/utils.py", "tests/test_main.py", "README.md", ".gitignore"],
        "git_status": "clean",
        "tests_passing": False,
        "test_failures": ["test_main.py::test_login_flow"],
    }
    snapshot_after = dict(snapshot_before)  # identical — no change
    return [
        (
            "'I cleaned up the project structure' (state unchanged)",
            verify_action_outcome(
                claim="I've cleaned up the project structure and all tests are now passing.",
                before_snapshot=snapshot_before,
                after_snapshot=snapshot_after,
            ),
        ),
    ]


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

    md = render_output_vet_report(
        grounding_cases=_build_grounding_cases(),
        swallowed_cases=_build_swallowed_cases(),
        outcome_cases=_build_outcome_cases(),
        version=__version__,
    )
    print(md, file=sys.stdout)


if __name__ == "__main__":
    main()
