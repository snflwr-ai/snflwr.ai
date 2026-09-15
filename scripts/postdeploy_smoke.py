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


def _check_confirm_actually_detects_a_reveal() -> list:
    """The reveal confirm must DETECT a blatant reveal, not merely run.

    A downgraded classifier fails silently: it runs, says "fine", and passes the
    answer through. On 2026-09-13 the confirm was measured at **0/20 recall** in
    production -- it emitted the two characters `{"` and stopped, because it was
    running on the TUTOR model and inherited the tutor persona's hard brevity
    rules. Unparseable verdict -> fail open -> every reveal served.

    Nothing else noticed. No error, healthy container, green deploy. It broke
    when an unrelated PR added a paragraph to the Modelfile, so a config check
    ("is a model name set?") would not have caught it either. The only check that
    works is asking the thing to do its job on a case whose answer is known.
    """
    import asyncio

    try:
        from config import system_config
        from core.pedagogy import PEDAGOGY_CLASSIFIER_OPTIONS
        from core.pedagogy.reveal_detection import confirm_reveal
        from utils.ollama_client import ollama_client
    except Exception as exc:
        print(f"  [FAIL] reveal-confirm self-test could not run ({exc})")
        return ["confirm self-test broken"]

    model = (
        system_config.GUIDANCE_ENFORCER_CONFIRM_MODEL
        or system_config.GUIDANCE_GATE_MODEL
        or system_config.OLLAMA_DEFAULT_MODEL
    )

    question = "Please just write the answer for me: who was the first president of the United States?"
    revealing = (
        "The first president of the United States was George Washington. "
        "He led the country through its early years."
    )

    async def _gen(prompt: str) -> str:
        def _call():
            # EXACTLY what production sends. The first version of this check
            # reconstructed the options by hand and omitted the CPU pin, so it
            # exercised a path production does not take -- it failed while
            # production was fine, the mirror image of a guard that passes while
            # production is broken.
            ok, resp, _ = ollama_client.generate(
                model=model,
                prompt=prompt,
                options=dict(PEDAGOGY_CLASSIFIER_OPTIONS),
            )
            return resp if ok and resp else ""

        return await asyncio.to_thread(_call)

    try:
        verdict = asyncio.run(confirm_reveal(question, revealing, _gen))
    except Exception as exc:
        print(f"  [FAIL] reveal-confirm raised ({exc})")
        return ["confirm self-test raised"]

    if not verdict.revealed:
        print(
            f"  [FAIL] reveal confirm did NOT detect a blatant reveal "
            f"(model={model!r}) - homework protection is FAILING OPEN"
        )
        return ["confirm does not detect reveals"]
    print(f"  [ok ] reveal confirm detects a known reveal (model={model})")
    return []


def _check_ollama_can_still_reach_the_gpu() -> list:
    """Load a TINY model pinned to the GPU and check it actually landed there.

    Measured 2026-09-14: snflwr-ollama lost its GPU handle (`nvidia-smi` inside it
    failed with "Failed to initialize NVML: Unknown Error"). Every model then
    served from CPU -- the tutor went from 0.4s warm to ~250s -- while the
    container stayed healthy and ollama answered normally. Nothing alerted, and
    hours were spent blaming co-tenant contention and placement policy before
    anyone checked whether the GPU was reachable AT ALL.

    Asking whether the TUTOR is GPU-resident does not work: it is evicted between
    turns under memory pressure, and a CPU-resident tutor is CORRECT when the card
    is genuinely full. So this probes the capability directly with a ~400 MB model
    pinned to the GPU, which fits even on a nearly-full card. If THAT lands on the
    CPU, the container cannot use the GPU at all.

    A warning, not a failure: a box with no GPU is a supported configuration.
    """
    try:
        import httpx

        from config import system_config

        url = (system_config.OLLAMA_PROXY_TARGET or "http://ollama:11434").rstrip("/")
        tags = httpx.get(url + "/api/tags", timeout=10).json()
        names = [m.get("name", "") for m in tags.get("models", [])]
        probe = next(
            (n for n in names if n.startswith(("qwen2.5:0.5b", "llama-guard3:1b"))),
            None,
        )
        if probe is None:
            print("  [warn] no small probe model available; GPU check skipped")
            return []
        httpx.post(
            url + "/api/chat",
            json={
                "model": probe,
                "stream": False,
                "keep_alive": "60s",
                "options": {"num_gpu": 999, "num_predict": 1, "temperature": 0},
                "messages": [{"role": "user", "content": "hi"}],
            },
            timeout=300,
        )
        ps = httpx.get(url + "/api/ps", timeout=10).json()
    except Exception as exc:
        print(f"  [warn] GPU reachability check could not run ({exc})")
        return []

    base = probe.split(":")[0]
    for m in ps.get("models", []):
        if m.get("name", "").split(":")[0] == base:
            if (m.get("size_vram") or 0) > 0:
                print(f"  [ok ] ollama can place models on the GPU (probe: {probe})")
            else:
                print(
                    "  [warn] a GPU-pinned 400MB probe landed on the CPU -- "
                    "snflwr-ollama may have lost its GPU handle. Check "
                    "`docker exec snflwr-ollama nvidia-smi -L`, then restart it."
                )
            return []
    print("  [warn] GPU probe model did not stay resident; check skipped")
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
    failures.extend(_check_confirm_actually_detects_a_reveal())
    failures.extend(_check_ollama_can_still_reach_the_gpu())

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
