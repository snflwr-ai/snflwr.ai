"""Access gate: a student must have a real learning profile to use the tutor.

``ollama_proxy._get_profile_for_user`` returns a synthetic ``safety_required_*``
marker when the account has no child profile OR the profile lookup errored.
Both are treated as "no profile" and fail CLOSED (block), consistent with the
per-child COPPA gate, which also blocks on a lookup failure.
"""

from typing import Optional

NO_PROFILE_MESSAGE = (
    "No learning profile is set up yet — please ask your parent or teacher "
    "to create one in Settings before chatting."
)


def no_profile_block_reason(profile_id: Optional[str]) -> Optional[str]:
    """Return a block message if a student has no real learning profile, else None."""
    if (
        not profile_id
        or profile_id.startswith("safety_required_")
        or profile_id.startswith("no_profile_")
    ):
        return NO_PROFILE_MESSAGE
    return None
