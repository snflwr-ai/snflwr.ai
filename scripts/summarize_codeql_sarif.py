"""Read CodeQL's SARIF in CI and gate on it, without Advanced Security.

CodeQL's normal output path is a SARIF upload to GitHub code scanning, which on
a private repository needs Advanced Security. This repo went private on
2026-09-18 and the job has been red on every run since, main included, while
saying nothing whatsoever about the code. It was marked `continue-on-error`,
which keeps the RUN green and leaves the JOB red -- the worst of both: a check
that fails permanently, gates nothing, and trains everyone to ignore checks.

Two things were wrong, and only one was the one on the tin:

1. The recorded cause ("the SARIF upload needs GHAS") was never verified. The
   job log shows no upload attempt and no Advanced Security error. The single
   fatal error is a call to `GET /repos/{owner}/{repo}/actions/runs/{run_id}`,
   which needs the `actions: read` permission -- and the workflow's explicit
   `permissions:` block granted only `contents` and `security-events`, which
   makes every unlisted scope `none`. That is fixed in the workflow.
2. The analysis was completing and its results were being thrown away. Nobody
   had ever seen them. There were six.

So: run the analysis, skip the upload, read the SARIF here, and gate on it.
Same move already made for Trivy in this workflow, which was hiding a fixable
CRITICAL until its findings were printed into the job summary.

    python scripts/summarize_codeql_sarif.py <sarif-dir> <baseline.json> \
        [--scope full|diff]

The gate is a ratchet against a baseline of reviewed findings, because all six
current findings are false positives that cannot be fixed in code (CodeQL
propagates taint through `len()` and classifies high-entropy tokens as
passwords). Failing on them would rebuild the permanently-red check this
change exists to remove; ignoring them would miss the seventh. So the baseline
records each accepted finding WITH ITS REASON, and any drift from it fails --
in either direction, because a baseline looser than reality tolerates a
regression in silence.

SCOPE matters, and getting it wrong makes the gate useless in one direction.
On a pull_request, codeql-action runs DIFF-INFORMED: it builds the whole
database but reports only results inside the changed line ranges ("Computing
PR diff ranges... Persisted 11 diff range(s) across 5 file(s)"). A PR that
touches none of the flagged files therefore reports ZERO findings, which
against a full-tree baseline looks like every entry went stale. So:

  --scope diff  (pull_request)  enforce NEW and REGRESSION only
  --scope full  (push/schedule) enforce those AND the stale-baseline ratchet

The known gap, stated rather than papered over: in diff scope a PR adding a
THIRD sink to a file the baseline already allows two of reports 1, which is
under the cap and passes. The full run on main catches it as a regression at
merge time. Closing it at PR time would mean disabling diff-informed analysis
and paying a full ~5min analysis on every PR, on billed private-repo minutes.

A MISSING, empty, or unparseable SARIF is a FAILURE, not a pass. The point is
to prove the analysis ran; a summarizer that shrugs at no input rebuilds the
false green it exists to remove.
"""

import json
import os
import re
import sys
from pathlib import Path

# CodeQL points each message sentence at a related location with a trailing
# `](N)`. The index is what makes otherwise identical sentences look distinct.
_PATH_REF = re.compile(r"\]\(\d+\)")

REPORTED_LEVELS = ("error", "warning", "note")

# Inline annotations are capped: GitHub renders only a handful per file, and a
# wall of them trains people to collapse the section. The summary table is
# always complete; this is the inline convenience only.
MAX_ANNOTATIONS = 20


def _rule_index(run: dict) -> dict:
    """Map ruleId -> rule object for the rules declared in one SARIF run."""
    driver = run.get("tool", {}).get("driver", {})
    return {
        rule.get("id"): rule
        for rule in driver.get("rules", [])
        if isinstance(rule, dict) and rule.get("id")
    }


def _level_of(result: dict, rules: dict) -> str:
    """Severity of one result, falling back to the rule's own default.

    CodeQL omits `level` on a result when it matches the rule default, so
    reading only the result understates severity.
    """
    level = result.get("level")
    if level:
        return str(level)
    rule = rules.get(result.get("ruleId"), {})
    default = rule.get("defaultConfiguration", {}).get("level")
    return str(default or "warning")


def _location_of(result: dict) -> tuple[str, int]:
    """First physical location of a result, as (path, line)."""
    for location in result.get("locations", []):
        physical = location.get("physicalLocation", {})
        path = physical.get("artifactLocation", {}).get("uri")
        if path:
            line = physical.get("region", {}).get("startLine", 0)
            return str(path), int(line)
    return "<unknown>", 0


def _tidy(message: str) -> str:
    """Collapse a CodeQL message to one readable line.

    CodeQL emits one sentence per distinct taint path into the same sink, so a
    single result's message can repeat the same sentence 22 times, differing
    only by the `](N)` index that points at a related location. Stripping that
    index makes the duplicates collapse; the path count is kept, because "22
    ways to reach this sink" is the one useful thing the repetition carried.
    Embedded newlines also break the markdown table this feeds.
    """
    lines = [part.strip() for part in message.splitlines() if part.strip()]
    seen: list[str] = []
    for line in lines:
        normalized = _PATH_REF.sub("]", line)
        if normalized not in seen:
            seen.append(normalized)
    text = " ".join(seen)
    if len(lines) > len(seen):
        text = f"{text} [{len(lines)} taint paths]"
    return text.replace("|", "\\|")


def _findings(sarif: dict) -> list[dict]:
    """Flatten every result across every run into one list."""
    out: list[dict] = []
    for run in sarif.get("runs", []):
        rules = _rule_index(run)
        for result in run.get("results", []):
            path, line = _location_of(result)
            out.append(
                {
                    "rule": str(result.get("ruleId") or "<no-rule>"),
                    "level": _level_of(result, rules),
                    "path": path,
                    "line": line,
                    "message": _tidy(str(result.get("message", {}).get("text", ""))),
                }
            )
    return out


def _load_sarif(sarif_dir: Path) -> tuple[list[dict], list[str]]:
    """Read every .sarif under a directory. Returns (findings, errors)."""
    files = sorted(sarif_dir.glob("*.sarif")) if sarif_dir.is_dir() else []
    if not files:
        return [], [f"no .sarif file found in {sarif_dir} - did the analysis run?"]

    findings: list[dict] = []
    errors: list[str] = []
    for path in files:
        try:
            sarif = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{path.name} is unreadable: {exc}")
            continue
        if not isinstance(sarif, dict) or "runs" not in sarif:
            errors.append(f"{path.name} has no 'runs' key - not a SARIF log")
            continue
        findings.extend(_findings(sarif))
    return findings, errors


def _load_baseline(path: Path) -> tuple[dict, list[str]]:
    """Read the reviewed-findings baseline as {(rule, path): entry}."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {}, [f"baseline {path} is unreadable: {exc}"]

    accepted = raw.get("accepted")
    if not isinstance(accepted, list):
        return {}, [f"baseline {path} has no 'accepted' list"]

    baseline: dict = {}
    errors: list[str] = []
    for entry in accepted:
        rule, where = entry.get("rule"), entry.get("path")
        if not rule or not where:
            errors.append(f"baseline entry missing rule/path: {entry}")
            continue
        if not str(entry.get("reason", "")).strip():
            errors.append(f"baseline entry for {rule} at {where} has no reason")
            continue
        baseline[(str(rule), str(where))] = entry
    return baseline, errors


def _counts(findings: list[dict]) -> dict:
    """Count findings per (rule, path)."""
    tally: dict = {}
    for finding in findings:
        key = (finding["rule"], finding["path"])
        tally[key] = tally.get(key, 0) + 1
    return tally


def _emit(line: str, summary: Path | None) -> None:
    """Write one line to stdout and, in CI, to the job summary."""
    print(line)
    if summary is not None:
        with summary.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def _annotate(findings: list[dict]) -> None:
    """Emit GitHub inline annotations for the most serious findings."""

    def order(finding: dict) -> int:
        level = finding["level"]
        return REPORTED_LEVELS.index(level) if level in REPORTED_LEVELS else 9

    for finding in sorted(findings, key=order)[:MAX_ANNOTATIONS]:
        print(
            f"::notice file={finding['path']},line={finding['line']},"
            f"title=CodeQL {finding['rule']} ({finding['level']})::"
            f"{finding['message']}"
        )


def _report(findings: list[dict], summary: Path | None, scope: str) -> None:
    """Write the findings table to stdout and the job summary."""
    where = "full tree" if scope == "full" else "changed lines only (diff-informed)"
    _emit(f"### CodeQL (security-extended) -- {where}", summary)
    _emit("", summary)
    if not findings:
        _emit(f"Analysis ran and returned no findings across the {where}.", summary)
        return

    _emit(f"{len(findings)} finding(s):", summary)
    _emit("", summary)
    _emit("| level | rule | location | message |", summary)
    _emit("|---|---|---|---|", summary)
    for finding in sorted(findings, key=lambda f: (f["level"], f["rule"], f["path"])):
        _emit(
            f"| {finding['level']} | `{finding['rule']}` | "
            f"`{finding['path']}:{finding['line']}` | {finding['message']} |",
            summary,
        )
    _emit("", summary)


def _gate(
    observed: dict, baseline: dict, summary: Path | None, scope: str
) -> list[str]:
    """Compare observed findings to the baseline. Returns failure messages."""
    problems: list[str] = []

    for key, count in sorted(observed.items()):
        rule, where = key
        if key not in baseline:
            problems.append(
                f"NEW: {rule} at {where} ({count}x) is not in the baseline. "
                "Review it: fix the code, or add an entry with a reason."
            )
        elif count > int(baseline[key].get("count", 0)):
            problems.append(
                f"REGRESSION: {rule} at {where} is now {count}x, "
                f"baseline allows {baseline[key].get('count')}."
            )

    # Only a full-tree run can tell "this finding is gone" from "this run was
    # never looking there". A diff-informed PR reports nothing outside the
    # changed lines, so enforcing this there would fail every PR.
    if scope == "full":
        for key, entry in sorted(baseline.items()):
            rule, where = key
            expected = int(entry.get("count", 0))
            actual = observed.get(key, 0)
            if actual < expected:
                problems.append(
                    f"STALE BASELINE: {rule} at {where} is now {actual}x but the "
                    f"baseline still allows {expected}x. Tighten it -- a baseline "
                    "looser than reality hides the next regression."
                )

    if problems:
        _emit("#### Gate failed", summary)
        for problem in problems:
            _emit(f"- {problem}", summary)
    elif scope == "full":
        _emit(
            f"Gate passed (full tree): every finding is one of the "
            f"{len(baseline)} reviewed baseline entries, at the recorded count.",
            summary,
        )
    else:
        _emit(
            "Gate passed (diff scope): the changed lines introduce no finding "
            f"outside the {len(baseline)} reviewed baseline entries. The "
            "stale-baseline check runs on the full analysis on main.",
            summary,
        )
    return problems


def main(argv: list[str]) -> int:
    """Summarize and gate a CodeQL SARIF directory. Returns an exit code."""
    scope = "full"
    args = list(argv[1:])
    if "--scope" in args:
        index = args.index("--scope")
        if index + 1 >= len(args) or args[index + 1] not in ("full", "diff"):
            print("--scope takes 'full' or 'diff'", file=sys.stderr)
            return 2
        scope = args[index + 1]
        del args[index : index + 2]

    if len(args) != 2:
        print(
            f"usage: {Path(argv[0]).name} <sarif-dir> <baseline.json> "
            "[--scope full|diff]",
            file=sys.stderr,
        )
        return 2
    argv = [argv[0], *args]

    env_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    summary = Path(env_summary) if env_summary else None

    findings, sarif_errors = _load_sarif(Path(argv[1]))
    if sarif_errors:
        _emit("### CodeQL: analysis output missing or unreadable", summary)
        for error in sarif_errors:
            _emit(f"- {error}", summary)
        print("::error::CodeQL produced no readable SARIF; treating as a failure")
        return 1

    baseline, baseline_errors = _load_baseline(Path(argv[2]))
    if baseline_errors:
        _emit("### CodeQL: baseline is unusable", summary)
        for error in baseline_errors:
            _emit(f"- {error}", summary)
        print("::error::CodeQL baseline is unusable; treating as a failure")
        return 1

    _report(findings, summary, scope)
    _annotate(findings)
    problems = _gate(_counts(findings), baseline, summary, scope)

    if problems:
        for problem in problems:
            print(f"::error::{problem}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
