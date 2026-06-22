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
