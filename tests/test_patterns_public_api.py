"""Characterization guard for the safety.patterns public surface.

Passes against the pre-split module AND the post-split package. Catches a
dropped public name or a pattern lost during the file move.
"""
import re

import safety.patterns as patterns

PUBLIC_NAMES = [
    "LEET_MAP",
    "LEET_MAP_ALT",
    "HOMOGLYPH_MAP",
    "INVISIBLE_CHARS",
    "BIDI_CONTROLS",
    "STRIP_CHARS",
    "SINGLE_LETTER_SPACING_RE",
    "normalize_text",
    "CATEGORY_PATTERNS",
    "SUBSTR_CHECKS",
    "FALSE_POSITIVE_ALLOWLIST",
    "COMPILED_PATTERNS",
    "SEVERITY_MAP",
]


def test_all_public_names_importable():
    for name in PUBLIC_NAMES:
        assert hasattr(patterns, name), f"safety.patterns missing public name: {name}"


def test_compiled_patterns_match_category_patterns():
    # Every category is compiled, with the same number of patterns — no
    # pattern silently dropped in the move.
    assert set(patterns.COMPILED_PATTERNS.keys()) == set(
        patterns.CATEGORY_PATTERNS.keys()
    )
    for category, entries in patterns.CATEGORY_PATTERNS.items():
        assert len(patterns.COMPILED_PATTERNS[category]) == len(entries)


def test_compiled_patterns_are_compiled_regexes():
    for entries in patterns.COMPILED_PATTERNS.values():
        for compiled, desc in entries:
            assert isinstance(compiled, re.Pattern)
            assert isinstance(desc, str)


def test_severity_map_known_keys():
    assert patterns.SEVERITY_MAP == {
        "HATE_SPEECH": "high",
        "SEXUAL_CONTENT": "high",
        "VIOLENCE": "high",
        "SELF_HARM": "high",
        "PROFANITY": "medium",
        "DRUGS": "medium",
        "DEROGATORY": "medium",
    }


def test_normalize_text_returns_pair():
    result = patterns.normalize_text("test")
    assert isinstance(result, tuple) and len(result) == 2
    assert all(isinstance(s, str) for s in result)


def test_substr_checks_shape():
    # Dict[str, List[Tuple[str, str]]] — same container types after the move.
    assert isinstance(patterns.SUBSTR_CHECKS, dict)
    for category, entries in patterns.SUBSTR_CHECKS.items():
        assert isinstance(category, str)
        for pat, desc in entries:
            assert isinstance(pat, str) and isinstance(desc, str)


def test_allowlist_is_frozenset_of_str():
    assert isinstance(patterns.FALSE_POSITIVE_ALLOWLIST, frozenset)
    assert all(isinstance(x, str) for x in patterns.FALSE_POSITIVE_ALLOWLIST)
