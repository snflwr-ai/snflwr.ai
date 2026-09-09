"""Backwards-compatible alias for :mod:`safety.compliance_screen`.

The S9051B screen originally lived here, which was a latent deployment bug:
``docker/Dockerfile`` copies api/ core/ safety/ models/ storage/ database/
utils/ tasks/ — but NOT evals/. Any production code importing the screen from
this path would have raised ImportError inside a fail-open handler and turned
the runtime check into a silent no-op, which is precisely the failure mode that
hid a missing langfuse for an unknown period.

The screen is a compliance component, not an eval artifact, so it now lives in
``safety/`` where it ships. This module re-exports it so the eval harness and
existing imports keep working from one definition — build-time and runtime must
never drift into two ideas of what a violation is.
"""

from safety.compliance_screen import (  # noqa: F401
    PENALTY_PER_HIT,
    anthropomorphism_hits,
    compliance_score,
    count_hits,
    cross_session_memory_hits,
    emotional_appeal_hits,
    relationship_hits,
    scan,
    sycophancy_hits,
)
