"""
Unified 5-Stage Safety Pipeline for snflwr.ai (K-12 COPPA/FERPA)

Replaces the previous five separate safety filter modules with a single
sequential pipeline:

    Stage 1: Input Validation    (length, empty, special-char ratio)
    Stage 2: Text Normalization  (leet-speak, spacing tricks, NFKD)
    Stage 3: Pattern Matcher     (danger phrases, keywords, PII regex)
    Stage 4: Semantic Classifier (Ollama LLM-based classification)
    Stage 5: Age Gate + Redirects (age-band content restrictions)

Design principles:
    - Fail closed: every stage blocks on error (broad except Exception)
    - Short-circuit: first block wins, no further stages run
    - Deterministic stages (1-3, 5) always protect even if Ollama is down
    - SafetyResult is a frozen dataclass for immutability / thread safety
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

from config import safety_config
from safety.patterns import (
    STRIP_CHARS as _STRIP_CHARS,
)
from safety.patterns import (
    normalize_text as _normalize_text,
)
from safety.pipeline.classifier import _SemanticClassifier

# Re-export extracted engines + models so `safety.pipeline.X` resolves and
# the test patchability contract holds (SafetyPipeline below looks these up
# as module globals here).
from safety.pipeline.models import (
    Category,
    SafetyResult,
    Severity,
    _allow,
    _block,
)
from safety.pipeline.pattern_matcher import _PatternMatcher
from utils.logger import get_logger, log_safety_incident

logger = get_logger(__name__)


# =============================================================================
# 2. Stage 1 -- Input Validation
# =============================================================================

MAX_INPUT_LENGTH = 2000


def _stage_validate(text: str) -> Optional[SafetyResult]:
    """
    Stage 1: fast structural validation.

    Returns a block result on failure, or None to continue to the next stage.
    Fails closed on any exception.
    """
    try:
        # Empty / whitespace-only
        if not text or not text.strip():
            return _block(
                Severity.MINOR,
                Category.VALIDATION_ERROR,
                "Input is empty or whitespace-only.",
                stage="validate",
            )

        # Length limit
        if len(text) > MAX_INPUT_LENGTH:
            return _block(
                Severity.MINOR,
                Category.VALIDATION_ERROR,
                f"Input exceeds maximum length of {MAX_INPUT_LENGTH} characters.",
                stage="validate",
            )

        # Special-character ratio (>30% is suspicious / prompt-injection)
        total = len(text)
        special = sum(1 for ch in text if not ch.isalnum() and not ch.isspace())
        if total > 0 and (special / total) > 0.3:
            return _block(
                Severity.MINOR,
                Category.VALIDATION_ERROR,
                "Input contains excessive special characters.",
                stage="validate",
            )

        return None  # pass -- continue pipeline

    except Exception as exc:  # Intentional catch-all: fail closed
        logger.error("Stage 1 (validate) error, failing closed: %s", exc, exc_info=True)
        return _block(
            Severity.MAJOR,
            Category.VALIDATION_ERROR,
            "Validation error (fail closed).",
            stage="validate",
        )


# =============================================================================
# 3. Stage 2 -- Text Normalization
# =============================================================================


def _stage_normalize(text: str) -> str:
    """
    Stage 2: best-effort text normalization.

    Delegates to the shared ``safety.patterns.normalize_text`` function,
    returning only the *letters_only* form (used by downstream substring
    checks to defeat obfuscation).  This includes dual leet-speak
    interpretation (``1→l`` *and* ``1→i``) joined by ``|``.

    Never raises -- ``normalize_text`` returns a safe fallback on error.
    """
    _lightly, letters_only = _normalize_text(text)
    return letters_only


def _strip_invisible(text: str) -> str:
    """
    Strip zero-width characters and bidi controls from text.

    Used on the original text before regex pattern matching so that
    invisible character insertions (e.g. "k\\u200dill") don't prevent
    word-boundary patterns from matching.

    Never raises -- returns original on error.
    """
    try:
        return "".join(ch for ch in text if ch not in _STRIP_CHARS)
    except Exception:
        return text


# =============================================================================
# 6. Stage 5 -- Age Gate + Topic Redirects
# =============================================================================


def _stage_age_gate(original: str, age: Optional[int]) -> Optional[SafetyResult]:
    """
    Stage 5: age-band restrictions and topic redirects.

    Returns a block/redirect result, or None to continue.
    """
    try:
        text_lower = original.lower()

        # -- Topic redirects (all ages) from safety_config ----------------------
        redirect_topics: Dict[str, str] = getattr(safety_config, "REDIRECT_TOPICS", {})
        for topic, redirect_to in redirect_topics.items():
            # Build lightweight keyword patterns for each redirect topic
            topic_keywords = _REDIRECT_KEYWORDS.get(topic, [topic])
            for kw in topic_keywords:
                if re.search(r"\b" + re.escape(kw) + r"\b", text_lower):
                    # Exempt if the message has a clear civics / social-studies
                    # educational context (e.g. "civics class", "world history",
                    # "religious studies"). Bare "class" or "school" alone is
                    # NOT sufficient — see _CIVICS_INDICATORS for the full list.
                    if any(ind in text_lower for ind in _CIVICS_INDICATORS):
                        continue
                    return _block(
                        Severity.MINOR,
                        Category.TOPIC_REDIRECT,
                        f"Topic '{topic}' is redirected to {redirect_to}.",
                        stage="age_gate",
                        keywords=(kw,),
                        redirection=redirect_to,
                    )

        # If age is unknown, skip age-specific checks (deterministic stages
        # upstream already caught dangerous content).
        if age is None:
            return None

        # -- Elementary (age < 10) ------------------------------------------------
        if age < 10:
            elementary_blocked = [
                "dating",
                "boyfriend",
                "girlfriend",
                "horror",
                "scary",
                "mature content",
                "adult",
                # Substances — parents can ask admin to raise age if ready
                "alcohol",
                "beer",
                "wine",
                "liquor",
                "drunk",
                "tobacco",
                "cigarette",
                "cigarettes",
                "smoking",
                "vaping",
                "vape",
                "marijuana",
                "cannabis",
            ]
            for kw in elementary_blocked:
                if re.search(r"\b" + re.escape(kw) + r"\b", text_lower):
                    return _block(
                        Severity.MINOR,
                        Category.AGE_INAPPROPRIATE,
                        f"Content not appropriate for elementary students: {kw}",
                        stage="age_gate",
                        keywords=(kw,),
                        redirection="age-appropriate topics for young learners",
                    )

        # -- Middle school (10-13) ------------------------------------------------
        elif age <= 13:
            middle_blocked = [
                "hookup",
                "making out",
                "romantic relationship",
            ]
            for kw in middle_blocked:
                if re.search(r"\b" + re.escape(kw) + r"\b", text_lower):
                    return _block(
                        Severity.MINOR,
                        Category.AGE_INAPPROPRIATE,
                        f"Content not appropriate for middle school students: {kw}",
                        stage="age_gate",
                        keywords=(kw,),
                        redirection="age-appropriate social topics",
                    )

        # High school (14+): only universal prohibited categories apply (handled
        # by earlier stages).

        return None  # pass

    except Exception as exc:  # Intentional catch-all: fail closed
        logger.error("Stage 5 (age_gate) error, failing closed: %s", exc, exc_info=True)
        return _block(
            Severity.MINOR,
            Category.AGE_INAPPROPRIATE,
            "Age gate error (fail closed).",
            stage="age_gate",
        )


# Indicators of legitimate civics, government, world-history, or
# religious-studies educational context. Used by _stage_age_gate() to
# exempt topic redirects when the student is clearly doing coursework.
# Intentionally multi-word or subject-specific to prevent bypass with
# generic words like "class" or "school" alone.
_CIVICS_INDICATORS = (
    # Government / civics courses
    "civics",
    "civic",
    "social studies",
    "government class",
    "government course",
    "government lesson",
    "how government works",
    "how laws work",
    "electoral college",
    "constitution",
    "amendment",
    "bill of rights",
    "branches of government",
    # History courses (specific — "history" alone is too broad)
    "history class",
    "history lesson",
    "history homework",
    "world history",
    "us history",
    "american history",
    # Religion in academic context
    "world religion",
    "world religions",
    "religious studies",
    "comparative religion",
    "history of religion",
    "cultural studies",
)

# Keyword lists for redirect topics
_REDIRECT_KEYWORDS: Dict[str, list] = {
    "politics": [
        "politics",
        "political",
        "election",
        "vote",
        "democrat",
        "republican",
        "liberal",
        "conservative",
        "congress",
        "senator",
    ],
    "religion": [
        "religion",
        "religious",
        "church",
        "mosque",
        "temple",
        "bible",
        "quran",
        "torah",
        "prayer",
        "worship",
    ],
}


# =============================================================================
# 7. SafetyPipeline -- orchestrator
# =============================================================================


class SafetyPipeline:
    """
    Unified 5-stage sequential safety pipeline.

    Usage::

        result = safety_pipeline.check_input("hello", age=10, profile_id="abc")
        if not result.is_safe:
            msg = safety_pipeline.get_safe_response(result)
    """

    def __init__(self) -> None:
        self._pattern_matcher = _PatternMatcher()
        self._classifier = _SemanticClassifier()
        self._stats: Dict[str, int] = {
            "inputs_checked": 0,
            "outputs_checked": 0,
            "inputs_blocked": 0,
            "outputs_blocked": 0,
        }
        logger.info("SafetyPipeline initialized.")

    # ------------------------------------------------------------------ #
    # check_input  (all 5 stages)
    # ------------------------------------------------------------------ #

    def check_input(
        self,
        text: str,
        age: Optional[int] = None,
        profile_id: str = "",
    ) -> SafetyResult:
        """
        Run the full 5-stage pipeline on user input.

        Short-circuits on first block. Fails closed on any unhandled error.
        """
        try:
            self._stats["inputs_checked"] += 1

            # Stage 1: Input Validation
            result = _stage_validate(text)
            if result is not None:
                self._log_block(result, text, profile_id)
                return result

            # Stage 2: Normalization (produces normalized text for Stage 3)
            normalized = _stage_normalize(text)

            # Sanitize original text for regex matching: strip invisible chars
            # and bidi controls so that zero-width insertions don't break
            # word-boundary patterns (e.g. "k\u200dill" -> "kill").
            sanitized = _strip_invisible(text)

            # Stage 3: Pattern Matcher
            result = self._pattern_matcher.check(sanitized, normalized)
            if result is not None:
                self._log_block(result, text, profile_id)
                return result

            # Stage 4: Semantic Classifier
            result = self._classifier.classify(text, age)
            if result is not None:
                # Educational override: if pattern matching already passed (no
                # dangerous keywords found) and the message has clear educational
                # context, override non-critical classifier blocks. This prevents
                # false positives from the LLM (e.g. "math" flagged as "meth").
                if (
                    result.severity != Severity.CRITICAL
                    and self._pattern_matcher._has_educational_context(text.lower())
                ):
                    logger.info(
                        "Classifier blocked but educational context detected — overriding "
                        "(category=%s, reason=%s)",
                        result.category,
                        result.reason,
                    )
                else:
                    self._log_block(result, text, profile_id)
                    return result

            # Stage 5: Age Gate
            result = _stage_age_gate(text, age)
            if result is not None:
                self._log_block(result, text, profile_id)
                return result

            return _allow(stage="pipeline")

        except Exception as exc:  # Intentional catch-all: fail closed at top level
            logger.error(
                "SafetyPipeline.check_input unhandled error, failing closed: %s",
                exc,
                exc_info=True,
            )
            return _block(
                Severity.MAJOR,
                Category.CLASSIFIER_ERROR,
                "Pipeline error (fail closed).",
                stage="pipeline",
            )

    # ------------------------------------------------------------------ #
    # check_output  (stages 3, 4, + 5, with normalization)
    # ------------------------------------------------------------------ #

    def check_output(
        self,
        text: str,
        age: Optional[int] = None,
        profile_id: str = "",
    ) -> SafetyResult:
        """
        Run output validation (stages 3, 4, + 5) on AI-generated text.

        Stages:
            3 — Pattern Matcher (keyword + regex, CRITICAL/MAJOR)
            4 — Semantic Classifier (LLM-based; skipped if Ollama unavailable)
            5 — Age Gate + Topic Redirects

        Unlike check_input(), there is no educational context override here.
        If the classifier flags AI-generated content, it is blocked unconditionally.
        Attaches a modified_content fallback on every block.
        """
        try:
            self._stats["outputs_checked"] += 1

            # Normalize for pattern matching
            normalized = _stage_normalize(text)
            sanitized = _strip_invisible(text)

            # Stage 3: Pattern Matcher
            result = self._pattern_matcher.check(sanitized, normalized)
            if result is not None:
                fallback = self._output_fallback(result.category)
                result = SafetyResult(
                    is_safe=result.is_safe,
                    severity=result.severity,
                    category=result.category,
                    reason=result.reason,
                    triggered_keywords=result.triggered_keywords,
                    suggested_redirection=result.suggested_redirection,
                    stage=result.stage,
                    modified_content=fallback,
                    possible_false_positive=result.possible_false_positive,
                )
                self._log_block(result, text, profile_id, is_output=True)
                return result

            # Stage 4: Semantic Classifier (no educational override for AI output)
            result = self._classifier.classify(text, age)
            if result is not None:
                fallback = self._output_fallback(result.category)
                result = SafetyResult(
                    is_safe=result.is_safe,
                    severity=result.severity,
                    category=result.category,
                    reason=result.reason,
                    triggered_keywords=result.triggered_keywords,
                    suggested_redirection=result.suggested_redirection,
                    stage=result.stage,
                    modified_content=fallback,
                    possible_false_positive=result.possible_false_positive,
                )
                self._log_block(result, text, profile_id, is_output=True)
                return result

            # Stage 5: Age Gate
            result = _stage_age_gate(text, age)
            if result is not None:
                fallback = self._output_fallback(result.category)
                result = SafetyResult(
                    is_safe=result.is_safe,
                    severity=result.severity,
                    category=result.category,
                    reason=result.reason,
                    triggered_keywords=result.triggered_keywords,
                    suggested_redirection=result.suggested_redirection,
                    stage=result.stage,
                    modified_content=fallback,
                    possible_false_positive=result.possible_false_positive,
                )
                self._log_block(result, text, profile_id, is_output=True)
                return result

            return _allow(stage="output_pipeline")

        except Exception as exc:  # Intentional catch-all: fail closed
            logger.error(
                "SafetyPipeline.check_output unhandled error, failing closed: %s",
                exc,
                exc_info=True,
            )
            return _block(
                Severity.MAJOR,
                Category.CLASSIFIER_ERROR,
                "Output pipeline error (fail closed).",
                stage="output_pipeline",
                modified_content=self._output_fallback(Category.CLASSIFIER_ERROR),
            )

    # ------------------------------------------------------------------ #
    # get_safe_response
    # ------------------------------------------------------------------ #

    def get_safe_response(self, result: SafetyResult) -> str:
        """
        Generate a child-friendly redirect message from a block result.

        CRITICAL: for SELF_HARM, always include the 988 crisis text.
        """
        if result.is_safe:
            return ""

        # Self-harm requires crisis resource information -- this is non-negotiable
        if result.category == Category.SELF_HARM:
            return (
                "I'm really sorry you're feeling this way. You are not alone, and help is available. "
                "If you or someone you know is struggling, please talk to a trusted adult, teacher, "
                "or counselor. You can also reach the 988 Suicide & Crisis Lifeline by calling or "
                "texting 988."
            )

        # Redirection hint from the result itself
        if result.suggested_redirection:
            return (
                f"I can't help with that topic, but I can help you with "
                f"{result.suggested_redirection}. What would you like to learn about?"
            )

        # Category-specific fallbacks
        _MESSAGES: Dict[Category, str] = {
            Category.VIOLENCE: "I can't help with that topic. Let's focus on your schoolwork instead.",
            Category.EXPLOITATION: "I can't help with that. Let's focus on learning together.",
            Category.SEXUAL: "That's not appropriate for our learning environment. Let's stay focused on your studies.",
            Category.DRUGS: "I can't help with that topic. Let's focus on your educational questions.",
            Category.WEAPONS: "I can't provide that information. Let's work on your homework instead.",
            Category.PII: "I can't share or ask for personal information. Let's keep our conversation focused on learning.",
            Category.BULLYING: "Let's keep our conversation positive and respectful. How can I help with your schoolwork?",
            Category.BYPASS_ATTEMPT: "I'm here to help you learn. Let's get back to your studies!",
            Category.TOPIC_REDIRECT: "Let's explore that in an age-appropriate way. What would you like to learn?",
            Category.AGE_INAPPROPRIATE: "That topic isn't suitable for our learning session. How about we explore something else?",
            Category.VALIDATION_ERROR: "Could you rephrase your question? I want to make sure I understand you correctly.",
            Category.CLASSIFIER_ERROR: "I'm having trouble processing that. Could you try asking in a different way?",
            Category.HATE_SPEECH: "I can't help with that. Let's keep our conversation respectful and focus on learning together.",
            Category.PROFANITY: "Let's keep our language respectful. How can I help with your schoolwork?",
            Category.DEROGATORY: "Let's keep our conversation kind and respectful. What would you like to learn about?",
        }

        return _MESSAGES.get(
            result.category,
            "I'm not able to help with that right now. Let's try something else!",
        )

    # ------------------------------------------------------------------ #
    # Statistics
    # ------------------------------------------------------------------ #

    def get_statistics(self) -> Dict[str, int]:
        """Return pipeline usage statistics."""
        return dict(self._stats)

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _log_block(
        self,
        result: SafetyResult,
        text: str,
        profile_id: str,
        *,
        is_output: bool = False,
    ) -> None:
        """Log a blocked interaction via the structured safety incident logger."""
        try:
            self._stats["outputs_blocked" if is_output else "inputs_blocked"] += 1
            log_safety_incident(
                incident_type=result.category.value,
                profile_id=profile_id or "unknown",
                content=text[:500],  # truncate for safety log storage
                severity=result.severity.value,
                metadata={
                    "reason": result.reason,
                    "stage": result.stage,
                    "triggered_keywords": list(result.triggered_keywords),
                },
            )
        except Exception as exc:  # Intentional: logging must never crash the pipeline
            logger.error("Failed to log safety block: %s", exc)

    @staticmethod
    def _output_fallback(category) -> str:
        """Generate a safe fallback message for blocked AI output."""
        _FALLBACKS: Dict[Category, str] = {
            Category.VIOLENCE: "I can't provide that information. Let's focus on your schoolwork instead.",
            Category.SELF_HARM: (
                "If you or someone you know is struggling, please talk to a trusted adult, "
                "teacher, or counselor. You can also reach the 988 Suicide & Crisis Lifeline "
                "by calling or texting 988."
            ),
            Category.EXPLOITATION: "I can't help with that. Let's focus on learning together.",
            Category.SEXUAL: "That's not appropriate for our learning environment. Let's stay focused on your studies.",
            Category.DRUGS: "I can't help with that topic. Let's focus on your educational questions.",
            Category.WEAPONS: "I can't provide that information. Let's work on your homework instead.",
            Category.PII: "I shouldn't share personal information. Let's keep our conversation focused on learning.",
            Category.BULLYING: "Let's keep our conversation positive and respectful.",
            Category.BYPASS_ATTEMPT: "I'm here to help you learn.",
            Category.TOPIC_REDIRECT: "Let's explore that topic in an age-appropriate way.",
            Category.AGE_INAPPROPRIATE: "That topic isn't suitable right now. Let's try something else.",
            Category.HATE_SPEECH: "I can't provide that. Let's keep our conversation respectful and focus on learning.",
            Category.PROFANITY: "Let's keep our language respectful.",
            Category.DEROGATORY: "Let's keep our conversation kind and respectful.",
        }
        return _FALLBACKS.get(
            category,
            "I need to rephrase my response. Let me try a different approach to your question.",
        )


# =============================================================================
# Module-level singleton and exports
# =============================================================================

safety_pipeline = SafetyPipeline()

__all__ = ["SafetyPipeline", "SafetyResult", "Severity", "Category", "safety_pipeline"]
