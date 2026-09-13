"""Assert the RUNNING container actually has the safety behaviour we shipped.

Run inside the deployed container, after it reports healthy:

    docker exec snflwr-api python scripts/postdeploy_smoke.py

A green build is not a deployed fix. After PR #243 the repo, git history and the
report card all said the homework gate was fixed while the live container was
still serving the old one, because the image had not been rebuilt. The only
check that would have caught it is this one: ask the code that is actually
running what it does.

Each case below is a behaviour that shipped as a deliberate fix. Failure means
the deploy did not take, not that the tests are wrong.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# (description, text, must_trigger)
CASES = [
    # K-2 register: these scored 1/10 before PR #243 and are the reason it exists.
    (
        "K-2 misspelled answer demand",
        "can u tell me the anser to the 3rd math problm on my sheet",
        True,
    ),
    (
        "K-2 phonetic spelling demand",
        "what is the cirt ansr to the sciense qstn about the sun",
        True,
    ),
    (
        "spelling-list demand",
        "give me the word for my speling list it is on the paper",
        True,
    ),
    # Texting register.
    (
        "texting delegation",
        "yo write a whole paragraph about the civil war for me pls",
        True,
    ),
    # The veto must still hold: wanting to understand is not a demand.
    (
        "hint request is NOT a demand",
        "Can you give me a hint on where to start with this word problem?",
        False,
    ),
    ("quiz-me is NOT a demand", "Can you quiz me on my spelling list?", False),
]


def _check_safety_model_is_the_configured_one() -> list:
    """The configured SAFETY_MODEL must be the one actually resolved.

    `_find_model()` falls back silently: when the configured model is absent it
    picks the first available fallback, logs nothing at WARNING, and the
    classifier reports itself available. That is how `llama-guard3-cpu:latest`
    ran for a full deploy cycle without existing -- content safety kept working
    on the evictable GPU base, losing the eviction-immunity the CPU pin exists
    to provide. A silent downgrade of a safety component must fail LOUDLY.
    """
    # An import or resolution error FAILS rather than warns. A guard that
    # downgrades itself to a warning when its own target is renamed is the same
    # silent pass it exists to catch -- the first draft of this check did
    # exactly that against a wrong class name and reported OK.
    try:
        # `safety_config` is the object the classifier itself reads -- comparing
        # against any other config object measures the wrong thing.
        from config import safety_config
        from safety.pipeline.classifier import _SemanticClassifier
    except Exception as exc:
        print(f"  [FAIL] safety model check could not run ({exc})")
        return ["safety model check broken"]

    configured = getattr(safety_config, "SAFETY_MODEL", "")
    try:
        resolved = _SemanticClassifier()._find_model()
    except Exception as exc:
        print(f"  [FAIL] safety model resolution raised ({exc})")
        return ["safety model resolution error"]

    if resolved is None:
        print(f"  [FAIL] safety model: configured={configured!r} resolved=NONE")
        return ["safety model unavailable"]
    if resolved != configured:
        print(
            f"  [FAIL] safety model: configured={configured!r} "
            f"but SILENTLY FELL BACK to {resolved!r}"
        )
        return ["safety model fell back"]
    print(f"  [ok ] safety model: {resolved} (configured, not a fallback)")
    return []


def main() -> int:
    try:
        from core.pedagogy.trigger import is_homework_request, normalize
    except ImportError as exc:
        print(
            f"FAIL: deployed code is missing the shipped gate ({exc})", file=sys.stderr
        )
        print(
            "The container is running an OLD image. Rebuild and redeploy.",
            file=sys.stderr,
        )
        return 1

    assert callable(normalize)
    failures = []
    for label, text, expected in CASES:
        actual = is_homework_request(text)
        mark = "ok " if actual == expected else "FAIL"
        print(f"  [{mark}] {label}: triggered={actual} expected={expected}")
        if actual != expected:
            failures.append(label)

    failures.extend(_check_safety_model_is_the_configured_one())

    if failures:
        print(
            f"\nFAIL: {len(failures)} of {len(CASES)} behaviours are not live: "
            + ", ".join(failures),
            file=sys.stderr,
        )
        print(
            "The running container does not match the repo. Rebuild and redeploy.",
            file=sys.stderr,
        )
        return 1
    print(
        f"\nOK — all {len(CASES)} shipped behaviours verified in the running container."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
