"""core.coppa_gate — the shared per-child COPPA consent gate (finding C1).

Both the Ollama proxy and the native /api/chat/send route call
coppa_consent_block_reason(), so testing it here covers the gate on every
model-reaching path. Fail-closed: a known under-13 profile is blocked unless
consent is positively verified.
"""

from unittest.mock import patch

from core.coppa_gate import COPPA_BLOCK_MESSAGE, coppa_consent_block_reason


def _rows(value):
    from core.authentication import auth_manager

    return patch.object(auth_manager.db, "execute_query", return_value=value)


def test_under13_unverified_blocked():
    with _rows([{"age": 9, "coppa_verified": 0}]):
        assert coppa_consent_block_reason("p") == COPPA_BLOCK_MESSAGE


def test_under13_verified_allowed():
    with _rows([{"age": 9, "coppa_verified": 1}]):
        assert coppa_consent_block_reason("p") is None


def test_teen_not_gated_even_if_unverified():
    with _rows([{"age": 15, "coppa_verified": 0}]):
        assert coppa_consent_block_reason("p") is None


def test_unknown_age_falls_through_to_pipeline():
    with _rows([{"age": None, "coppa_verified": 0}]):
        assert coppa_consent_block_reason("p", fallback_age=None) is None


def test_tuple_rows_supported():
    # adapters may return tuples rather than dict rows
    with _rows([(9, 0)]):
        assert coppa_consent_block_reason("p") == COPPA_BLOCK_MESSAGE


def test_missing_row_under13_fallback_blocks():
    with _rows([]):
        # no profile row, but caller knows the age is <13 → fail closed
        assert coppa_consent_block_reason("p", fallback_age=9) == COPPA_BLOCK_MESSAGE


def test_lookup_error_fails_closed_for_known_under13():
    from core.authentication import auth_manager

    with patch.object(
        auth_manager.db, "execute_query", side_effect=RuntimeError("db down")
    ):
        # lookup failed but caller-provided age is <13 → block (fail closed)
        assert coppa_consent_block_reason("p", fallback_age=9) == COPPA_BLOCK_MESSAGE
        # lookup failed and age unknown → fall through (safety pipeline handles)
        assert coppa_consent_block_reason("p", fallback_age=None) is None
