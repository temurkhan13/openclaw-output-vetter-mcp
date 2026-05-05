"""Action-outcome scanner tests — claim parsing + diff matching + constraint checks."""
from __future__ import annotations

import json

from openclaw_output_vetter_mcp.scanners import verify_action_outcome
from openclaw_output_vetter_mcp.types import Severity, Verdict

# ─────── State-unchanged (the chiefofautism failure mode) ───────


def test_claim_says_cleaned_up_but_state_identical_is_fabricated() -> None:
    before = {"files": ["a.py", "b.py"], "git_status": "clean", "git_tip": "abc"}
    after = dict(before)
    report = verify_action_outcome("I cleaned up the project structure.", before, after)
    assert report.verdict == Verdict.FABRICATED
    rule_ids = {m.rule_id for m in report.mismatches}
    assert "ACTION_OUTCOME.STATE_UNCHANGED" in rule_ids


def test_vague_completion_with_no_state_change_flags() -> None:
    before = {"files": ["x.py"]}
    after = {"files": ["x.py"]}
    report = verify_action_outcome("All done!", before, after)
    assert report.verdict == Verdict.FABRICATED
    assert any(m.rule_id == "ACTION_OUTCOME.STATE_UNCHANGED" for m in report.mismatches)


# ─────── File-creation claims ───────


def test_created_file_supported_by_diff_is_clean() -> None:
    before = {"files": ["main.py"]}
    after = {"files": ["main.py", "auth.py"]}
    report = verify_action_outcome("Created auth.py with the login flow.", before, after)
    assert report.verdict == Verdict.CLEAN
    assert report.matched_count >= 1


def test_created_file_unsupported_by_diff_is_fabricated() -> None:
    before = {"files": ["main.py"]}
    after = {"files": ["main.py"]}
    report = verify_action_outcome("Created auth.py with the login flow.", before, after)
    assert report.verdict == Verdict.FABRICATED
    assert any(m.rule_id == "ACTION_OUTCOME.UNSUPPORTED_CLAIM" for m in report.mismatches)


def test_created_file_terse_phrasing_detected() -> None:
    before = {"files": ["main.py"]}
    after = {"files": ["main.py"]}
    report = verify_action_outcome("Wrote helpers.py.", before, after)
    assert any(
        m.rule_id == "ACTION_OUTCOME.UNSUPPORTED_CLAIM"
        and "helpers.py" in m.expected
        for m in report.mismatches
    )


# ─────── File-deletion claims ───────


def test_deleted_file_supported_by_diff_is_clean() -> None:
    before = {"files": ["main.py", "old.py"]}
    after = {"files": ["main.py"]}
    report = verify_action_outcome("Removed old.py.", before, after)
    assert report.verdict == Verdict.CLEAN


def test_deleted_file_unsupported_by_diff_is_fabricated() -> None:
    before = {"files": ["main.py", "old.py"]}
    after = {"files": ["main.py", "old.py"]}
    report = verify_action_outcome("Deleted old.py.", before, after)
    assert report.verdict == Verdict.FABRICATED
    assert any(m.rule_id == "ACTION_OUTCOME.UNSUPPORTED_CLAIM" for m in report.mismatches)


# ─────── Tests-pass claims ───────


def test_tests_pass_when_tests_status_indicates_pass_is_clean() -> None:
    before = {"tests_status": "fail"}
    after = {"tests_status": "pass"}
    report = verify_action_outcome("All tests pass now.", before, after)
    assert report.verdict == Verdict.CLEAN


def test_tests_pass_claim_when_status_indicates_fail_is_fabricated() -> None:
    before = {"tests_status": "fail"}
    after = {"tests_status": "fail"}
    report = verify_action_outcome("All tests pass!", before, after)
    assert report.verdict == Verdict.FABRICATED
    assert any(m.rule_id == "ACTION_OUTCOME.TESTS_NOT_PASSING" for m in report.mismatches)


def test_tests_pass_with_no_status_field_is_ambiguous() -> None:
    before = {"files": ["a.py"]}
    after = {"files": ["a.py", "b.py"]}
    report = verify_action_outcome("Tests pass.", before, after)
    assert any(m.rule_id == "ACTION_OUTCOME.AMBIGUOUS_CLAIM" for m in report.mismatches)


def test_tests_pass_dict_shape_passing() -> None:
    before = {"tests_status": {"passed": 0, "failed": 5}}
    after = {"tests_status": {"passed": 12, "failed": 0}}
    report = verify_action_outcome("Tests are green.", before, after)
    assert report.verdict == Verdict.CLEAN


def test_tests_pass_dict_shape_still_failing() -> None:
    before = {"tests_status": {"passed": 0, "failed": 5}}
    after = {"tests_status": {"passed": 8, "failed": 2}}
    report = verify_action_outcome("Tests pass.", before, after)
    assert report.verdict == Verdict.FABRICATED


# ─────── Commit / push claims ───────


def test_committed_with_changed_git_tip_is_clean() -> None:
    before = {"git_tip": "abc123", "git_status": "dirty"}
    after = {"git_tip": "def456", "git_status": "clean"}
    report = verify_action_outcome("Committed and pushed the fix.", before, after)
    assert report.verdict == Verdict.CLEAN


def test_committed_without_git_tip_change_is_fabricated() -> None:
    before = {"git_tip": "abc123"}
    after = {"git_tip": "abc123"}
    report = verify_action_outcome("Committed and pushed the fix.", before, after)
    assert report.verdict == Verdict.FABRICATED
    assert any(m.rule_id == "ACTION_OUTCOME.NO_COMMIT" for m in report.mismatches)


def test_git_head_alias_recognized() -> None:
    before = {"git_head": "abc123"}
    after = {"git_head": "def456"}
    report = verify_action_outcome("I committed it.", before, after)
    assert report.verdict == Verdict.CLEAN


def test_clean_state_claim_with_dirty_status_is_fabricated() -> None:
    before = {"git_status": "dirty"}
    after = {"git_status": "dirty"}
    report = verify_action_outcome("The repo is now clean.", before, after)
    assert report.verdict == Verdict.FABRICATED
    assert any(m.rule_id == "ACTION_OUTCOME.UNCOMMITTED_CHANGES" for m in report.mismatches)


def test_clean_state_claim_with_clean_status_is_clean() -> None:
    before = {"git_status": "dirty"}
    after = {"git_status": "clean"}
    report = verify_action_outcome("Workspace is clean now.", before, after)
    assert report.verdict == Verdict.CLEAN


# ─────── Constraint violations (the Codex sandbox-escalation case) ───────


def test_read_only_constraint_violated_is_critical() -> None:
    before = {"read_only": True, "files": ["a.py"]}
    after = {"read_only": True, "files": ["a.py", "b.py"]}  # write happened
    report = verify_action_outcome("Acknowledged read-only mode.", before, after)
    assert any(
        m.rule_id == "ACTION_OUTCOME.STATE_VIOLATED_CONSTRAINT"
        and m.severity == Severity.CRITICAL
        for m in report.mismatches
    )
    assert report.verdict == Verdict.FABRICATED


def test_read_only_with_no_change_is_clean() -> None:
    before = {"read_only": True, "files": ["a.py"]}
    after = {"read_only": True, "files": ["a.py"]}
    report = verify_action_outcome("Looked at the code, made no changes.", before, after)
    # No state change + read_only=True is fine; vague claim with no change → STATE_UNCHANGED
    # but read_only constraint NOT violated
    assert not any(
        m.rule_id == "ACTION_OUTCOME.STATE_VIOLATED_CONSTRAINT"
        for m in report.mismatches
    )


# ─────── Expected-changes (caller-supplied checklist) ───────


def test_expected_change_file_added_satisfied() -> None:
    before = {"files": ["main.py"]}
    after = {"files": ["main.py", "auth.py"]}
    report = verify_action_outcome(
        "Done.", before, after, expected_changes=["file:auth.py:added"]
    )
    # No verdict-blocking mismatch on expected_changes
    assert not any(
        m.rule_id == "ACTION_OUTCOME.MISSING_EXPECTED_CHANGE"
        for m in report.mismatches
    )


def test_expected_change_file_added_missing() -> None:
    before = {"files": ["main.py"]}
    after = {"files": ["main.py"]}
    report = verify_action_outcome(
        "Done.", before, after, expected_changes=["file:auth.py:added"]
    )
    assert any(
        m.rule_id == "ACTION_OUTCOME.MISSING_EXPECTED_CHANGE"
        for m in report.mismatches
    )


def test_expected_change_git_committed_missing() -> None:
    before = {"git_tip": "abc"}
    after = {"git_tip": "abc"}
    report = verify_action_outcome("Did stuff.", before, after, expected_changes=["git:committed"])
    assert any(
        m.rule_id == "ACTION_OUTCOME.MISSING_EXPECTED_CHANGE"
        for m in report.mismatches
    )


# ─────── Verdict semantics + ranking ───────


def test_unverified_when_no_assertions_and_no_expected() -> None:
    before = {"files": ["a.py"]}
    after = {"files": ["a.py", "b.py"]}
    # claim with no recognized verbs → no assertions
    report = verify_action_outcome("Hmm, looking at this...", before, after)
    assert report.verdict == Verdict.UNVERIFIED


def test_partially_grounded_with_one_match_one_miss() -> None:
    before = {"files": ["main.py"]}
    after = {"files": ["main.py", "auth.py"]}
    # claim says created two files (in two sentences so each is parsed independently);
    # only one is in diff
    report = verify_action_outcome(
        "Created auth.py. Also wrote helpers.py.", before, after
    )
    assert report.matched_count >= 1
    assert report.mismatched_count >= 1


def test_critical_mismatch_drives_fabricated_verdict() -> None:
    before = {"read_only": True}
    after = {"read_only": True, "files": ["something.txt"]}
    report = verify_action_outcome("All done.", before, after)
    assert report.verdict == Verdict.FABRICATED


def test_mismatches_sorted_by_severity_desc() -> None:
    before = {"read_only": True, "tests_status": "fail"}
    after = {"read_only": True, "tests_status": "fail", "files": ["new.py"]}
    report = verify_action_outcome("Tests pass and I cleaned up.", before, after)
    if len(report.mismatches) >= 2:
        rank = {Severity.CRITICAL: 0, Severity.HIGH: 1, Severity.MEDIUM: 2, Severity.LOW: 3, Severity.INFO: 4}
        for a, b in zip(report.mismatches, report.mismatches[1:], strict=False):
            assert rank[a.severity] <= rank[b.severity]


# ─────── Diff summary + serialization ───────


def test_diff_summary_describes_changes() -> None:
    before = {"files": ["a.py"]}
    after = {"files": ["a.py", "b.py", "c.py"]}
    report = verify_action_outcome("Created b.py and c.py.", before, after)
    assert "files" in report.diff_summary or "+" in report.diff_summary


def test_diff_summary_no_change() -> None:
    before = {"files": ["a.py"]}
    after = {"files": ["a.py"]}
    report = verify_action_outcome("Did nothing visible.", before, after)
    assert "no change" in report.diff_summary.lower()


def test_report_serializes_to_json() -> None:
    before = {"files": ["a.py"]}
    after = {"files": ["a.py", "b.py"]}
    report = verify_action_outcome("Created b.py.", before, after)
    payload = json.loads(report.model_dump_json())
    assert payload["verdict"] == "clean"
    assert "diff_summary" in payload
    assert isinstance(payload["mismatches"], list)


# ─────── Robustness ───────


def test_handles_non_dict_snapshots_gracefully() -> None:
    # type-validate at the API boundary turns these into {} — verify_action_outcome
    # also coerces internally
    report = verify_action_outcome("Done.", {}, {})  # type: ignore[arg-type]
    assert report.verdict in (Verdict.UNVERIFIED, Verdict.FABRICATED)


def test_handles_empty_claim() -> None:
    before = {"files": ["a.py"]}
    after = {"files": ["a.py"]}
    report = verify_action_outcome("", before, after)
    assert report.verdict == Verdict.UNVERIFIED


def test_files_can_be_set_or_tuple() -> None:
    before = {"files": ("a.py",)}
    after = {"files": {"a.py", "b.py"}}
    report = verify_action_outcome("Created b.py.", before, after)
    assert report.verdict == Verdict.CLEAN
