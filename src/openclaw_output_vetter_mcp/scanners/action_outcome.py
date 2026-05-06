"""Action-outcome verifier (v1.1+, P10 ABSORB).

Compares an agent's stated outcome ("I cleaned up the project structure",
"tests pass", "committed and pushed") against actual before/after snapshots
of the relevant state (filesystem, git, tests, DB, etc).

This is the next layer below `review_transcript`'s
`unverified-completion-claim` check:
- `review_transcript` flags claims with NO supporting tool calls (transcript-only).
- `verify_action_outcome` flags claims WITH tool calls whose side effects
  don't match what the agent said happened.

The targeted failure mode is the [@chiefofautism quote (158↑ / 11.5K views)](
https://x.com/chiefofautism/status/2023151450503753972):

  *"...and it will do it confidently while telling you that he cleaned up
  the project structure"*

…and the [Codex sandbox-escalation case](
https://x.com/heynavtoor/status/2049202562373751162) — agent's chain of
thought acknowledged the read-only constraint, then wrote to disk anyway.

## Snapshot shape

The scanner is **schema-loose**: snapshots are arbitrary `dict[str, Any]`
captured by the caller. The matchers inspect a small set of recognized
keys when present:

  files:        list[str]        — file paths in working dir; set-diff
  git_status:   str | dict       — "clean" / "dirty" semantics
  git_tip:      str              — HEAD commit SHA
  git_head:     str              — alias for git_tip
  git_log_tip:  str              — alias for git_tip
  tests_status: str | dict       — "pass" / "fail" or {"passed": N, "failed": N}
  test_status:  str | dict       — alias for tests_status
  read_only:    bool             — caller-asserted no-write constraint;
                                   if True in before AND files/git changed
                                   → STATE_VIOLATED_CONSTRAINT (Codex case)

Keys outside this set are still tracked for general "did anything change?"
diff-summary, but no claim-specific matchers run.

## Output

`ActionOutcomeReport` with verdict + per-mismatch evidence. Verdicts use
the same Verdict enum the rest of the server uses:

  CLEAN              — all extracted claim assertions match the diff
  PARTIALLY_GROUNDED — some match, some don't
  FABRICATED         — diff actively contradicts the claim
                       (state unchanged or constraint violated)
  UNVERIFIED         — claim couldn't be parsed into testable assertions
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from openclaw_output_vetter_mcp.types import (
    ActionOutcomeMismatch,
    ActionOutcomeReport,
    Severity,
    Verdict,
)

SCANNER_NAME = "action-outcome"

# Followup pattern for multi-target claims: matches separators ", " / " and " /
# ", and " followed by a filename-like token. Used after the primary verb-anchored
# match to expand "Created A and B" / "Removed A, B, and C" into per-file assertions.
_MULTI_TARGET_FOLLOWUP = re.compile(
    r"(?:\s*,\s*(?:and\s+)?|\s+and\s+)['`\"]?([\w./\\-]+\.\w{1,8})['`\"]?",
    re.IGNORECASE,
)

# ─────── Claim-extraction patterns ───────
# Each tuple: (kind, regex). The kind is what the claim is asserting in plain
# domain terms. Matching is intentionally permissive — false positives in
# claim extraction become "ambiguous claim" findings rather than false hits.

_CLAIM_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "created_file",
        re.compile(
            r"(?i)\b(?:created|added|wrote|made|generated)\s+(?:a\s+|the\s+|new\s+)?"
            r"(?:file|module|script|test|component)\s+(?:called\s+|named\s+)?"
            r"['`\"]?([\w./\\-]+\.\w+)['`\"]?",
        ),
    ),
    (
        "created_file_terse",
        # "created foo.py" / "added bar.py" — naked filename after verb
        re.compile(
            r"(?i)\b(?:created|added|wrote)\s+['`\"]?([\w./\\-]+\.\w{1,8})['`\"]?",
        ),
    ),
    (
        "deleted_file",
        re.compile(
            r"(?i)\b(?:removed|deleted|cleaned\s+up|got\s+rid\s+of)\s+(?:the\s+)?"
            r"(?:file\s+)?['`\"]?([\w./\\-]+\.\w+)['`\"]?",
        ),
    ),
    (
        "tests_pass",
        re.compile(
            r"(?i)\b(?:tests?\s+(?:are\s+)?(?:all\s+)?(?:pass(?:ing|ed)?|green)"
            r"|all\s+(?:tests\s+)?(?:pass(?:ing|ed)?|green)"
            r"|tests?\s+succeed(?:ed)?)\b",
        ),
    ),
    (
        "committed",
        re.compile(r"(?i)\b(?:committed|pushed|shipped|landed)\b"),
    ),
    (
        "clean_state",
        re.compile(
            r"(?i)\b(?:everything|the\s+project|workspace|tree|repo)\s+(?:is\s+)?"
            r"(?:now\s+)?(?:clean|tidy|cleaned\s+up|in\s+order)\b",
        ),
    ),
    (
        "vague_completion",
        # "I cleaned up the project structure" / "did the thing" / "All done!" —
        # match-without-target; used as a fallback when nothing more specific matched
        re.compile(
            r"(?i)\b(?:cleaned\s+up|tidied|organized|finished|completed|done\s+with"
            r"|all\s+done|all\s+set|that['’]?s\s+done|that['’]?s\s+all)\b",
        ),
    ),
]

# ─────── Diff computation ───────


def _compute_diff(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    """Return a structured diff: added keys, removed keys, value changes, file-set changes.

    Output shape:
      {
        "any_change": bool,
        "added_keys":   {k: v for k in after - before},
        "removed_keys": {k: v for k in before - after},
        "changed_keys": {k: (before[k], after[k])},
        "files_added":   set[str],
        "files_removed": set[str],
        "git_tip_changed": bool,
        "git_status_after": str | None,
      }
    """
    before_keys = set(before.keys())
    after_keys = set(after.keys())

    added_keys = {k: after[k] for k in after_keys - before_keys}
    removed_keys = {k: before[k] for k in before_keys - after_keys}
    changed_keys: dict[str, tuple[Any, Any]] = {}
    for k in before_keys & after_keys:
        if before[k] != after[k]:
            changed_keys[k] = (before[k], after[k])

    # File-set diff
    files_before = _coerce_file_set(before.get("files"))
    files_after = _coerce_file_set(after.get("files"))
    files_added = files_after - files_before
    files_removed = files_before - files_after

    # Git tip / SHA change
    git_tip_keys = ("git_tip", "git_head", "git_log_tip")
    git_tip_before = next(
        (str(before[k]) for k in git_tip_keys if k in before),
        None,
    )
    git_tip_after = next(
        (str(after[k]) for k in git_tip_keys if k in after),
        None,
    )
    git_tip_changed = bool(git_tip_after and git_tip_before and git_tip_after != git_tip_before)

    # Git status (after-state)
    git_status_after_raw = after.get("git_status")
    git_status_after = _coerce_git_status(git_status_after_raw)

    any_change = bool(
        added_keys or removed_keys or changed_keys or files_added or files_removed or git_tip_changed,
    )

    return {
        "any_change": any_change,
        "added_keys": added_keys,
        "removed_keys": removed_keys,
        "changed_keys": changed_keys,
        "files_added": files_added,
        "files_removed": files_removed,
        "git_tip_before": git_tip_before,
        "git_tip_after": git_tip_after,
        "git_tip_changed": git_tip_changed,
        "git_status_after": git_status_after,
    }


def _coerce_file_set(raw: Any) -> set[str]:
    """Normalize a `files` field to a set[str]. Accepts list/tuple/set; ignores other shapes."""
    if isinstance(raw, set | list | tuple):
        return {str(x) for x in raw}
    return set()


def _coerce_git_status(raw: Any) -> str | None:
    """Extract a 'clean'/'dirty'/None signal from a git_status field of unknown shape."""
    if raw is None:
        return None
    if isinstance(raw, str):
        text = raw.strip().lower()
        if not text or text == "clean":
            return "clean"
        return "dirty"
    if isinstance(raw, Mapping):
        is_clean = bool(raw.get("clean", False))
        if is_clean:
            return "clean"
        # Common alternative shape: lists of changed/untracked files
        for k in ("modified", "untracked", "staged", "added", "deleted"):
            v = raw.get(k)
            if isinstance(v, list | tuple) and len(v) > 0:
                return "dirty"
        return "clean"
    return None


def _coerce_tests_status(raw: Any) -> tuple[bool | None, str]:
    """Extract a (tests_pass: bool|None, summary: str) from a tests_status field.

    Returns (None, "") when the field is absent/unrecognized.
    """
    if raw is None:
        return None, ""
    if isinstance(raw, str):
        text = raw.strip().lower()
        if text in {"pass", "passed", "passing", "green", "ok", "success"}:
            return True, f"tests_status={raw!r}"
        if text in {"fail", "failed", "failing", "red", "error"}:
            return False, f"tests_status={raw!r}"
        return None, f"tests_status={raw!r} (unknown)"
    if isinstance(raw, Mapping):
        failed = int(raw.get("failed", 0) or 0)
        passed = int(raw.get("passed", 0) or 0)
        if failed == 0 and passed > 0:
            return True, f"{passed} passed / {failed} failed"
        if failed > 0:
            return False, f"{passed} passed / {failed} failed"
        return None, f"{passed} passed / {failed} failed (no tests run?)"
    if isinstance(raw, bool):
        return raw, f"tests_status={raw}"
    return None, ""


# ─────── Claim extraction ───────


def _extract_claim_assertions(claim: str) -> list[tuple[str, str | None, str]]:
    """Parse claim text into list of (kind, target_or_none, claim_excerpt) tuples."""
    if not claim or not claim.strip():
        return []

    assertions: list[tuple[str, str | None, str]] = []
    seen_specific = False

    for kind, pattern in _CLAIM_PATTERNS:
        for m in pattern.finditer(claim):
            target = m.group(1) if m.groups() else None
            excerpt = claim[max(0, m.start() - 10) : min(len(claim), m.end() + 30)].strip()
            if len(excerpt) > 200:
                excerpt = excerpt[:200] + "..."
            if kind == "vague_completion" and seen_specific:
                # Skip vague matches if we already have specific ones
                continue
            assertions.append((kind, target, excerpt))
            if kind not in ("vague_completion",):
                seen_specific = True

            # Multi-target expansion (v1.2+): for file-creation/deletion claims,
            # scan the text immediately after the matched span for chained
            # filenames connected by ", " / " and " / ", and ". This catches
            # "Created auth.py and helpers.py" / "Removed old.py, legacy.py".
            if kind in ("created_file", "created_file_terse", "deleted_file") and target:
                tail_start = m.end()
                # Bound the tail at the next sentence boundary so we don't drag
                # filenames from later sentences into this assertion's scope.
                sentence_end = _next_sentence_boundary(claim, tail_start)
                tail = claim[tail_start:sentence_end]
                for fm in _MULTI_TARGET_FOLLOWUP.finditer(tail):
                    chained_target = fm.group(1)
                    if chained_target == target:
                        continue
                    chained_excerpt = (
                        f"{excerpt} (chained: '{chained_target}')"
                        if len(excerpt) + len(chained_target) + 14 <= 200
                        else excerpt
                    )
                    assertions.append((kind, chained_target, chained_excerpt))

    # Dedupe identical (kind, target) pairs while preserving order
    seen: set[tuple[str, str | None]] = set()
    unique: list[tuple[str, str | None, str]] = []
    for a in assertions:
        key = (a[0], a[1])
        if key in seen:
            continue
        seen.add(key)
        unique.append(a)
    return unique


def _next_sentence_boundary(text: str, start: int) -> int:
    """Return the index of the next sentence-ending boundary at or after `start`,
    or len(text) if none. Used to bound multi-target expansion to one sentence.

    A period only counts as a boundary when followed by whitespace or end-of-string —
    so periods inside filenames ('helpers.py') don't terminate the scan, but
    'helpers.py. Then…' does (the second period, before whitespace, terminates).
    """
    boundary_chars = ".!?"
    n = len(text)
    for i in range(start, n):
        # End-of-string OR whitespace immediately after = real boundary.
        if text[i] in boundary_chars and (i + 1 >= n or text[i + 1].isspace()):
            return i
    return n


# ─────── Mismatch detection ───────


def _check_assertion(
    kind: str,
    target: str | None,
    excerpt: str,
    diff: dict[str, Any],
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> ActionOutcomeMismatch | None:
    """Return a mismatch finding if the assertion contradicts the diff, else None."""
    if kind == "created_file" or kind == "created_file_terse":
        if target and target not in diff["files_added"]:
            return ActionOutcomeMismatch(
                severity=Severity.HIGH,
                rule_id="ACTION_OUTCOME.UNSUPPORTED_CLAIM",
                claim_excerpt=excerpt,
                expected=f"file {target!r} added between before/after",
                actual=(
                    f"diff shows files_added = {sorted(diff['files_added'])!r}; "
                    f"{target!r} is not in the added set"
                ),
                description=(
                    f"Claim says agent created {target!r}, but the after-snapshot's "
                    f"file list does not contain it. Agent claim is unsupported."
                ),
            )
        return None

    if kind == "deleted_file":
        if target and target not in diff["files_removed"]:
            return ActionOutcomeMismatch(
                severity=Severity.HIGH,
                rule_id="ACTION_OUTCOME.UNSUPPORTED_CLAIM",
                claim_excerpt=excerpt,
                expected=f"file {target!r} removed between before/after",
                actual=(
                    f"diff shows files_removed = {sorted(diff['files_removed'])!r}; "
                    f"{target!r} is still in the after-snapshot"
                ),
                description=(
                    f"Claim says agent deleted {target!r}, but the file is still "
                    f"in the after-snapshot. Agent claim is unsupported."
                ),
            )
        return None

    if kind == "tests_pass":
        passed_b, _ = _coerce_tests_status(before.get("tests_status") or before.get("test_status"))
        passed_a, status_a = _coerce_tests_status(after.get("tests_status") or after.get("test_status"))
        if passed_a is False:
            return ActionOutcomeMismatch(
                severity=Severity.CRITICAL,
                rule_id="ACTION_OUTCOME.TESTS_NOT_PASSING",
                claim_excerpt=excerpt,
                expected="tests passing in after-snapshot",
                actual=status_a or "after-snapshot tests_status indicates failure",
                description=(
                    "Claim asserts tests pass, but the after-snapshot's tests_status "
                    "field indicates failure. Either the claim is wrong or the snapshot "
                    "was captured before the agent re-ran tests."
                ),
            )
        if passed_a is None and passed_b is None:
            return ActionOutcomeMismatch(
                severity=Severity.MEDIUM,
                rule_id="ACTION_OUTCOME.AMBIGUOUS_CLAIM",
                claim_excerpt=excerpt,
                expected="tests_status field present in either snapshot",
                actual="no tests_status field in either before or after snapshot",
                description=(
                    "Claim asserts tests pass, but neither snapshot includes a "
                    "tests_status field. Claim cannot be verified — capture "
                    "tests_status in the after-snapshot before re-running."
                ),
            )
        return None

    if kind == "committed":
        if not diff["git_tip_changed"]:
            return ActionOutcomeMismatch(
                severity=Severity.HIGH,
                rule_id="ACTION_OUTCOME.NO_COMMIT",
                claim_excerpt=excerpt,
                expected="git_tip / git_head SHA changed between before/after",
                actual=(
                    f"git_tip_before={diff['git_tip_before']!r} == "
                    f"git_tip_after={diff['git_tip_after']!r} (or both absent)"
                ),
                description=(
                    "Claim asserts a commit / push, but the git tip SHA did not "
                    "change between snapshots. Either the commit didn't happen "
                    "or the snapshot was captured before the commit."
                ),
            )
        return None

    if kind == "clean_state":
        status_a = diff["git_status_after"]
        if status_a == "dirty":
            return ActionOutcomeMismatch(
                severity=Severity.HIGH,
                rule_id="ACTION_OUTCOME.UNCOMMITTED_CHANGES",
                claim_excerpt=excerpt,
                expected="git_status indicates clean tree in after-snapshot",
                actual="git_status in after-snapshot indicates dirty / uncommitted changes",
                description=(
                    "Claim asserts everything is clean / tidy, but the after-snapshot's "
                    "git_status shows uncommitted changes. The 'clean' claim is unsupported."
                ),
            )
        return None

    if kind == "vague_completion":
        if not diff["any_change"]:
            return ActionOutcomeMismatch(
                severity=Severity.HIGH,
                rule_id="ACTION_OUTCOME.STATE_UNCHANGED",
                claim_excerpt=excerpt,
                expected="some change visible between before/after snapshot",
                actual="before and after snapshots are identical (no change detected)",
                description=(
                    "Claim asserts an action was taken, but the before and after "
                    "snapshots are identical. The claim is the chiefofautism / Codex "
                    "failure mode: agent reports a completed action with nothing to show for it."
                ),
            )
        return None

    return None


def _check_constraint_violations(
    before: Mapping[str, Any],
    diff: dict[str, Any],
) -> list[ActionOutcomeMismatch]:
    """Catch the Codex-sandbox case: read-only constraint asserted, but state changed anyway."""
    mismatches: list[ActionOutcomeMismatch] = []
    if before.get("read_only") is True and diff["any_change"]:
        actual_bits = []
        if diff["files_added"]:
            actual_bits.append(f"files_added={sorted(diff['files_added'])!r}")
        if diff["files_removed"]:
            actual_bits.append(f"files_removed={sorted(diff['files_removed'])!r}")
        if diff["git_tip_changed"]:
            actual_bits.append("git_tip changed")
        if diff["changed_keys"]:
            actual_bits.append(f"changed_keys={list(diff['changed_keys'].keys())!r}")
        actual_str = "; ".join(actual_bits) or "diff shows changes"
        mismatches.append(
            ActionOutcomeMismatch(
                severity=Severity.CRITICAL,
                rule_id="ACTION_OUTCOME.STATE_VIOLATED_CONSTRAINT",
                claim_excerpt="(implicit) before-snapshot asserted read_only=True",
                expected="no state change while in read-only mode",
                actual=actual_str,
                description=(
                    "Before-snapshot declared read_only=True (agent was in a sandboxed "
                    "/ read-only mode), but state changed anyway. This is the Codex-CoT "
                    "sandbox-escalation pattern — agent acknowledged the constraint, then "
                    "violated it. Treat as evidence of agent unreliability."
                ),
            )
        )
    return mismatches


def _check_expected_changes(
    expected_changes: list[str] | None,
    diff: dict[str, Any],
) -> list[ActionOutcomeMismatch]:
    """For each entry in expected_changes, verify the diff supports it.

    Recognized formats:
      "file:foo.py:added"      → file in files_added
      "file:foo.py:removed"    → file in files_removed
      "git:committed"          → git tip changed
      "git:clean"              → git_status_after == "clean"
      "tests:pass"             → tests_status semantically passing
    """
    if not expected_changes:
        return []
    out: list[ActionOutcomeMismatch] = []
    for raw in expected_changes:
        spec = str(raw).strip()
        if spec.startswith("file:") and ":added" in spec:
            target = spec[len("file:") : spec.rfind(":added")]
            if target not in diff["files_added"]:
                out.append(
                    ActionOutcomeMismatch(
                        severity=Severity.HIGH,
                        rule_id="ACTION_OUTCOME.MISSING_EXPECTED_CHANGE",
                        claim_excerpt=f"expected_changes entry: {spec!r}",
                        expected=f"file {target!r} added",
                        actual=f"file {target!r} not in files_added={sorted(diff['files_added'])!r}",
                        description=f"Caller-supplied expected change {spec!r} did not occur.",
                    )
                )
        elif spec.startswith("file:") and ":removed" in spec:
            target = spec[len("file:") : spec.rfind(":removed")]
            if target not in diff["files_removed"]:
                out.append(
                    ActionOutcomeMismatch(
                        severity=Severity.HIGH,
                        rule_id="ACTION_OUTCOME.MISSING_EXPECTED_CHANGE",
                        claim_excerpt=f"expected_changes entry: {spec!r}",
                        expected=f"file {target!r} removed",
                        actual=f"file {target!r} not in files_removed={sorted(diff['files_removed'])!r}",
                        description=f"Caller-supplied expected change {spec!r} did not occur.",
                    )
                )
        elif spec == "git:committed":
            if not diff["git_tip_changed"]:
                out.append(
                    ActionOutcomeMismatch(
                        severity=Severity.HIGH,
                        rule_id="ACTION_OUTCOME.MISSING_EXPECTED_CHANGE",
                        claim_excerpt=f"expected_changes entry: {spec!r}",
                        expected="git tip SHA changed",
                        actual="git tip SHA unchanged or unspecified",
                        description="Caller expected a commit; git_tip did not change.",
                    )
                )
        elif spec == "git:clean":
            if diff["git_status_after"] != "clean":
                out.append(
                    ActionOutcomeMismatch(
                        severity=Severity.MEDIUM,
                        rule_id="ACTION_OUTCOME.MISSING_EXPECTED_CHANGE",
                        claim_excerpt=f"expected_changes entry: {spec!r}",
                        expected="git_status_after == 'clean'",
                        actual=f"git_status_after = {diff['git_status_after']!r}",
                        description="Caller expected a clean tree; git_status indicates otherwise.",
                    )
                )
    return out


# ─────── Public API ───────


def _diff_summary(diff: dict[str, Any]) -> str:
    parts: list[str] = []
    if diff["files_added"]:
        parts.append(f"+{len(diff['files_added'])} files")
    if diff["files_removed"]:
        parts.append(f"-{len(diff['files_removed'])} files")
    if diff["git_tip_changed"]:
        parts.append("git tip changed")
    if diff["changed_keys"]:
        parts.append(f"{len(diff['changed_keys'])} keys changed")
    if diff["added_keys"]:
        parts.append(f"+{len(diff['added_keys'])} new keys")
    if diff["removed_keys"]:
        parts.append(f"-{len(diff['removed_keys'])} dropped keys")
    if not parts:
        return "no change between before/after"
    return ", ".join(parts)


def verify_action_outcome(
    claim: str,
    before_snapshot: Mapping[str, Any],
    after_snapshot: Mapping[str, Any],
    expected_changes: list[str] | None = None,
) -> ActionOutcomeReport:
    """Compare an agent claim against actual before/after state diff.

    Pure function — does not capture state itself; the caller passes both
    snapshots. The server stays stateless (same posture as `verify_grounding`).

    Both snapshots must be Mapping-shaped; non-dict inputs should be coerced
    by the call site (server.py does this for MCP tool calls).
    """
    diff = _compute_diff(before_snapshot, after_snapshot)
    diff_summary = _diff_summary(diff)

    assertions = _extract_claim_assertions(claim)

    mismatches: list[ActionOutcomeMismatch] = []
    matched = 0
    mismatched = 0

    for kind, target, excerpt in assertions:
        m = _check_assertion(kind, target, excerpt, diff, before_snapshot, after_snapshot)
        if m is None:
            matched += 1
        else:
            mismatched += 1
            mismatches.append(m)

    # Always check constraint violations + caller-supplied expected_changes
    mismatches.extend(_check_constraint_violations(before_snapshot, diff))
    mismatches.extend(_check_expected_changes(expected_changes, diff))

    # Sort: CRITICAL → HIGH → MEDIUM → LOW → INFO, then by rule_id
    severity_rank = {
        Severity.CRITICAL: 0,
        Severity.HIGH: 1,
        Severity.MEDIUM: 2,
        Severity.LOW: 3,
        Severity.INFO: 4,
    }
    mismatches.sort(key=lambda m: (severity_rank[m.severity], m.rule_id))

    # Verdict composition
    has_critical = any(m.severity == Severity.CRITICAL for m in mismatches)
    has_high = any(m.severity == Severity.HIGH for m in mismatches)

    if not assertions and not expected_changes and not _check_constraint_violations(before_snapshot, diff):
        verdict = Verdict.UNVERIFIED
        summary = (
            "UNVERIFIED — claim has no extractable assertions and no "
            "expected_changes / constraint were supplied. Provide a more "
            "specific claim (filename, 'tests pass', 'committed', etc.) or "
            "pass expected_changes."
        )
    elif mismatched == 0 and not mismatches:
        verdict = Verdict.CLEAN
        summary = (
            f"CLEAN — claim is supported by the diff. "
            f"{matched} assertion(s) matched. Diff: {diff_summary}."
        )
    elif has_critical or (has_high and matched == 0):
        verdict = Verdict.FABRICATED
        summary = (
            f"FABRICATED — claim is contradicted by the diff. "
            f"{matched} matched / {mismatched} mismatched. "
            f"Worst: {mismatches[0].rule_id}. Diff: {diff_summary}."
        )
    else:
        verdict = Verdict.PARTIALLY_GROUNDED
        summary = (
            f"PARTIALLY_GROUNDED — some claim assertions match the diff, others don't. "
            f"{matched} matched / {mismatched} mismatched. Diff: {diff_summary}."
        )

    return ActionOutcomeReport(
        verdict=verdict,
        matched_count=matched,
        mismatched_count=mismatched,
        mismatches=mismatches,
        diff_summary=diff_summary,
        summary=summary,
    )
