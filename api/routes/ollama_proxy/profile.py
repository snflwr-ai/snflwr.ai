"""Profile helpers: look up the child profile linked to a user."""

from __future__ import annotations

from typing import Optional

from utils.logger import get_logger

logger = get_logger(__name__)


def _resolve_age(profile_id: Optional[str]) -> Optional[int]:
    """Resolve age (in years) from *profile_id*; returns None on any failure.

    Best-effort: a missing or invalid profile yields None, which is acceptable
    — the safety pipeline handles None age gracefully.
    """
    try:
        from core.authentication import auth_manager
        from core.profile_manager import ProfileManager

        pm = ProfileManager(auth_manager.db)
        prof = pm.get_profile(profile_id)
        if prof is not None:
            return prof.age or None
    except Exception as exc:
        logger.debug("Could not resolve age for profile %s: %s", profile_id, exc)
    return None


async def _get_profile_for_user(user_id: Optional[str]) -> str:
    """Look up the first child profile linked to *user_id*.

    Returns the profile_id string.  Fails closed: any error yields
    ``"safety_required_<user_id>"`` so the safety pipeline still runs.
    """
    if user_id is None:
        return "safety_required_unknown"
    try:
        from core.authentication import auth_manager
        from core.profile_manager import ProfileManager

        pm = ProfileManager(auth_manager.db)
        profiles = pm.get_profiles_by_parent(user_id)
        if profiles:
            return profiles[0].profile_id
        # No profiles found — still run safety with a synthetic profile id
        return f"safety_required_{user_id}"
    except Exception as exc:
        logger.warning(
            "_get_profile_for_user failed for user %s (fail-closed): %s",
            user_id,
            exc,
        )
        return f"safety_required_{user_id}"
