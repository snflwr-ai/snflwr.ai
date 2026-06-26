"""F2: when the ML safety classifier is unavailable, fail closed for under-13
(always) and for all ages when SAFETY_CLASSIFIER_REQUIRED is set; teens/unknown
degrade to deterministic stages otherwise."""

from unittest.mock import patch

from safety.pipeline import _SemanticClassifier


def _unavailable_classifier():
    clf = _SemanticClassifier.__new__(_SemanticClassifier)
    clf._available = False
    clf._client = None
    clf._model = "test"
    return clf


def test_under13_fails_closed_when_unavailable():
    clf = _unavailable_classifier()
    from config import system_config

    with patch.object(system_config, "SAFETY_CLASSIFIER_REQUIRED", False):
        result = clf.classify("hello", age=9)
    assert result is not None
    assert result.is_safe is False


def test_teen_degrades_when_unavailable_and_not_required():
    clf = _unavailable_classifier()
    from config import system_config

    with patch.object(system_config, "SAFETY_CLASSIFIER_REQUIRED", False):
        assert clf.classify("hello", age=15) is None
        assert clf.classify("hello", age=None) is None


def test_required_flag_fails_closed_for_all_ages():
    clf = _unavailable_classifier()
    from config import system_config

    with patch.object(system_config, "SAFETY_CLASSIFIER_REQUIRED", True):
        for age in (15, None, 9):
            r = clf.classify("hello", age=age)
            assert r is not None and r.is_safe is False


def test_missing_config_attr_fails_closed_for_all_ages():
    """If system_config is malformed and LACKS SAFETY_CLASSIFIER_REQUIRED, the
    getattr fallback must be fail-CLOSED (True), matching the config default — a
    broken config must not silently degrade teen/unknown-age safety to open."""
    import types
    import config

    clf = _unavailable_classifier()
    bare = types.SimpleNamespace()  # no SAFETY_CLASSIFIER_REQUIRED attribute
    with patch.object(config, "system_config", bare):
        for age in (15, None, 9):
            r = clf.classify("hello", age=age)
            assert r is not None and r.is_safe is False, f"age={age} must fail closed"


def test_default_config_fails_closed_for_teens_and_unknown_age():
    """F2 fix: the SHIPPED default must fail closed for teens AND unknown age,
    not just under-13. Every user on a K-12 platform is a minor, and the safety
    classifier ships in every stack — so a downed classifier is a broken safety
    layer that should block, not silently degrade to deterministic-only."""
    from config import system_config

    # The shipped default (no env override) must be strict.
    assert system_config.SAFETY_CLASSIFIER_REQUIRED is True

    clf = _unavailable_classifier()  # classifier down
    for age in (15, None, 9):  # teen, unknown, child — all blocked
        r = clf.classify("hello", age=age)
        assert r is not None and r.is_safe is False
