"""The CodeQL gate must fail on silence, not just on findings.

The job this guards was red for four days for a reason that had nothing to do
with the code, and its analysis results -- six findings -- were discarded
unseen the whole time. The failure mode to protect against is therefore not
"misses a finding" but "reports success having looked at nothing", which is the
same false green that let a canned fallback string score as a PASS elsewhere in
this repo.
"""

import importlib.util
import json
from pathlib import Path

import pytest

# scripts/ is not a package (CI type-checks it in its own mypy invocation), so
# load the module by path -- the same form tests/test_latency_bar.py uses.
_spec = importlib.util.spec_from_file_location(
    "summarize_codeql_sarif",
    Path(__file__).resolve().parent.parent / "scripts" / "summarize_codeql_sarif.py",
)
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)

RULE = "py/clear-text-logging-sensitive-data"
WHERE = "scripts/validate_env.py"


def _sarif(results: list[dict]) -> dict:
    """A minimal SARIF log carrying the given results."""
    return {
        "runs": [
            {
                "tool": {
                    "driver": {
                        "rules": [
                            {
                                "id": RULE,
                                "defaultConfiguration": {"level": "error"},
                            }
                        ]
                    }
                },
                "results": results,
            }
        ]
    }


def _result(path: str = WHERE, line: int = 46, rule: str = RULE) -> dict:
    """One SARIF result at a location."""
    return {
        "ruleId": rule,
        "message": {"text": "This expression logs [sensitive data (secret)](1)."},
        "locations": [
            {
                "physicalLocation": {
                    "artifactLocation": {"uri": path},
                    "region": {"startLine": line},
                }
            }
        ],
    }


def _write(tmp_path: Path, results: list[dict], accepted: list[dict]) -> tuple[str, str]:
    """Lay out a sarif dir and a baseline file. Returns their paths."""
    sarif_dir = tmp_path / "results"
    sarif_dir.mkdir()
    (sarif_dir / "python.sarif").write_text(json.dumps(_sarif(results)))
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"accepted": accepted}))
    return str(sarif_dir), str(baseline)


def _run(sarif_dir: str, baseline: str) -> int:
    """Invoke the gate the way CI does."""
    return gate.main(["summarize_codeql_sarif.py", sarif_dir, baseline])


def test_findings_matching_the_baseline_pass(tmp_path):
    """The current tree: every finding reviewed and recorded."""
    sarif_dir, baseline = _write(
        tmp_path,
        [_result()],
        [{"rule": RULE, "path": WHERE, "count": 1, "reason": "reviewed: taint via len()"}],
    )
    assert _run(sarif_dir, baseline) == 0


def test_a_clean_tree_passes(tmp_path):
    """No findings and no baseline entries is a legitimate green."""
    sarif_dir, baseline = _write(tmp_path, [], [])
    assert _run(sarif_dir, baseline) == 0


def test_a_new_finding_fails(tmp_path):
    """The seventh finding is the whole point of keeping the job."""
    sarif_dir, baseline = _write(tmp_path, [_result(path="core/new_module.py")], [])
    assert _run(sarif_dir, baseline) == 1


def test_more_findings_than_the_baseline_allows_fails(tmp_path):
    """A second sink in an already-accepted file is a regression."""
    sarif_dir, baseline = _write(
        tmp_path,
        [_result(line=46), _result(line=343)],
        [{"rule": RULE, "path": WHERE, "count": 1, "reason": "reviewed"}],
    )
    assert _run(sarif_dir, baseline) == 1


def test_a_baseline_looser_than_reality_fails(tmp_path):
    """The ratchet tightens, or it decays into a no-op.

    A stale baseline sat at 13 while the real count was 9 elsewhere in this
    stack and silently tolerated a 44% rise back to it.
    """
    sarif_dir, baseline = _write(
        tmp_path,
        [],
        [{"rule": RULE, "path": WHERE, "count": 1, "reason": "reviewed"}],
    )
    assert _run(sarif_dir, baseline) == 1


@pytest.mark.parametrize(
    "layout",
    ["missing_dir", "empty_dir", "unparseable", "not_sarif"],
)
def test_no_readable_sarif_fails(tmp_path, layout):
    """Missing evidence is a failure, never a pass."""
    sarif_dir = tmp_path / "results"
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"accepted": []}))

    if layout != "missing_dir":
        sarif_dir.mkdir()
    if layout == "unparseable":
        (sarif_dir / "python.sarif").write_text("{not json")
    if layout == "not_sarif":
        (sarif_dir / "python.sarif").write_text(json.dumps({"hello": "world"}))

    assert _run(str(sarif_dir), str(baseline)) == 1


def test_a_baseline_entry_without_a_reason_is_rejected(tmp_path):
    """An entry with no reason is a silencer, not a review."""
    sarif_dir, baseline = _write(
        tmp_path,
        [_result()],
        [{"rule": RULE, "path": WHERE, "count": 1}],
    )
    assert _run(sarif_dir, baseline) == 1


def test_severity_falls_back_to_the_rule_default():
    """CodeQL omits `level` when it matches the rule, so read the rule."""
    findings = gate._findings(_sarif([_result()]))
    assert findings[0]["level"] == "error"


def test_repeated_taint_path_sentences_collapse():
    """One result can repeat a sentence per taint path; keep the count."""
    message = "\n".join(f"Logs [sensitive data (secret)]({n})." for n in range(1, 15))
    tidied = gate._tidy(message)
    assert tidied.count("Logs") == 1
    assert "[14 taint paths]" in tidied
    assert "\n" not in tidied


def test_the_shipped_baseline_is_well_formed():
    """The real baseline must load, and every entry must carry a reason."""
    root = Path(__file__).resolve().parent.parent
    baseline, errors = gate._load_baseline(root / ".github" / "codeql-baseline.json")
    assert not errors
    assert baseline, "the shipped baseline should not be empty"
    for entry in baseline.values():
        assert len(str(entry["reason"]).strip()) > 40, entry
