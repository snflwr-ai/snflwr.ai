"""Shared per-child COPPA consent gate (audit finding C1).

An under-13 profile may only tutor once parental consent has been verified
(``coppa_verified=1``, set per-profile by ``/api/parental-consent/verify`` — never
at profile creation). This gate is enforced on EVERY path that can reach the
tutor model: the Open WebUI / Ollama proxy (``api/routes/ollama_proxy.py``) and
the native chat route (``api/routes/chat.py``). Keeping it in one place stops the
two paths from drifting (the native route previously lacked the gate).

Fail CLOSED: a known under-13 profile is blocked unless consent is POSITIVELY
confirmed. ``coppa_verified`` stays ``False`` on any lookup error or missing row,
so a COPPA-lookup failure blocks the child rather than letting them through. An
unknown/None age falls through to the (itself fail-closed) safety pipeline.
"""

from typing import Optional

from utils.logger import get_logger

logger = get_logger(__name__)

COPPA_BLOCK_MESSAGE = (
    "A parent needs to confirm permission before this account can chat. "
    "Please ask a parent to complete the consent step in Settings."
)


def coppa_consent_block_reason(
    profile_id: str, fallback_age: Optional[int] = None
) -> Optional[str]:
    """Return a block message if an under-13 profile lacks verified consent.

    Returns ``None`` when the profile is allowed to proceed (consent verified,
    13+, or an unknown age that the safety pipeline will handle). ``fallback_age``
    is a best-effort age used only when the profile row can't be read.
    """
    gate_age = fallback_age
    coppa_verified = False
    try:
        from core.authentication import auth_manager as _am

        rows = _am.db.execute_query(
            "SELECT age, coppa_verified FROM child_profiles WHERE profile_id = ?",
            (profile_id,),
        )
        if rows:
            r0 = rows[0]
            row_age = r0["age"] if isinstance(r0, dict) else r0[0]
            if row_age is not None:
                gate_age = row_age
            coppa_verified = bool(
                r0["coppa_verified"] if isinstance(r0, dict) else r0[1]
            )
    except Exception as exc:  # fail closed for under-13 (see gate_age check below)
        logger.warning(
            "COPPA gate lookup failed for %s — failing closed if under-13: %s",
            profile_id,
            exc,
        )

    # isinstance guard: a non-int age (malformed row) is treated as unknown and
    # falls through to the safety pipeline rather than raising on the comparison.
    if isinstance(gate_age, int) and gate_age < 13 and not coppa_verified:
        logger.info(
            "COPPA gate blocked under-13 profile %s (consent not verified)",
            profile_id,
        )
        return COPPA_BLOCK_MESSAGE
    return None
