"""Data models and enums for the safety pipeline.

Extracted verbatim from the former monolithic safety/pipeline.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple


class Severity(Enum):
    """Severity levels for safety findings, ordered from benign to critical."""

    NONE = "none"
    MINOR = "minor"
    MAJOR = "major"
    CRITICAL = "critical"


class Category(Enum):
    """Classification categories for safety findings."""

    VALID = "valid"
    VIOLENCE = "violence"
    SELF_HARM = "self_harm"
    EXPLOITATION = "exploitation"
    SEXUAL = "sexual"
    DRUGS = "drugs"
    WEAPONS = "weapons"
    PII = "pii"
    BULLYING = "bullying"
    HATE_SPEECH = "hate_speech"
    PROFANITY = "profanity"
    DEROGATORY = "derogatory"
    BYPASS_ATTEMPT = "bypass_attempt"
    TOPIC_REDIRECT = "topic_redirect"
    AGE_INAPPROPRIATE = "age_inappropriate"
    VALIDATION_ERROR = "validation_error"
    CLASSIFIER_ERROR = "classifier_error"


@dataclass(frozen=True)
class SafetyResult:
    """
    Immutable result from any stage of the safety pipeline.

    Frozen for thread safety -- once created, cannot be mutated.
    """

    is_safe: bool
    severity: Severity
    category: Category
    reason: str
    triggered_keywords: Tuple[str, ...] = ()
    suggested_redirection: Optional[str] = None
    stage: Optional[str] = None
    modified_content: Optional[str] = None
    possible_false_positive: bool = False
    # True when this deterministic block is a PLAIN-TEXT match on an
    # educational-topic keyword (violence/weapons/drugs) that should be
    # adjudicated by the semantic classifier rather than hard-blocked. Set ONLY
    # for non-obfuscated matches — an obfuscated keyword ("b0mb", "k i l l") is
    # treated as evasion and never deferred.
    deferrable: bool = False


def _block(
    severity: Severity,
    category: Category,
    reason: str,
    *,
    stage: str = "",
    keywords: Tuple[str, ...] = (),
    redirection: Optional[str] = None,
    modified_content: Optional[str] = None,
    possible_false_positive: bool = False,
    deferrable: bool = False,
) -> SafetyResult:
    """Convenience constructor for a BLOCK result."""
    return SafetyResult(
        is_safe=False,
        severity=severity,
        category=category,
        reason=reason,
        triggered_keywords=keywords,
        suggested_redirection=redirection,
        stage=stage,
        modified_content=modified_content,
        possible_false_positive=possible_false_positive,
        deferrable=deferrable,
    )


def _allow(*, stage: str = "", modified_content: Optional[str] = None) -> SafetyResult:
    """Convenience constructor for an ALLOW result."""
    return SafetyResult(
        is_safe=True,
        severity=Severity.NONE,
        category=Category.VALID,
        reason="Content is safe",
        stage=stage,
        modified_content=modified_content,
    )
