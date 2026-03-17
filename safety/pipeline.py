# safety/pipeline.py
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
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional, Tuple

from config import safety_config
from safety.patterns import (
    COMPILED_PATTERNS as _SHARED_COMPILED,
    SUBSTR_CHECKS as _SHARED_SUBSTR,
    FALSE_POSITIVE_ALLOWLIST as _SHARED_ALLOWLIST,
    STRIP_CHARS as _STRIP_CHARS,
    normalize_text as _normalize_text,
)
from utils.logger import get_logger, log_safety_incident

logger = get_logger(__name__)


# =============================================================================
# 1. Data Models and Enums
# =============================================================================


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
# 4. Stage 3 -- Pattern Matcher
# =============================================================================


class _PatternMatcher:
    """
    Deterministic pattern-matching engine.

    Checks, in order:
        1. Danger phrases (CRITICAL -- never educationally exempted)
        2. Prohibited keywords (MAJOR -- with educational exemption for contextual words)
        3. PII patterns (MAJOR)
    """

    # -- Educational exemption configuration ----------------------------------

    # Keywords that may appear in legitimate educational contexts
    _CONTEXTUAL_KEYWORDS = frozenset(
        {
            "die",
            "death",
            "dying",
            "kill",
            "killed",
            "killing",
            "bomb",
            "explosive",
            "gun",
            "knife",
            "poison",
            "meth",
            "drug",
            "drugs",
            "weed",
            "cocaine",
            # Sexual keywords: blocked by default, exempt in clear educational contexts
            # (e.g., health class / biology). Also prevents "sextant" false positive
            # since the normalized-text substring check has no word boundaries.
            "sex",
            "sexual",
            "sexting",
            "grooming",
        }
    )

    # Subject-specific indicators sufficient on their own to grant exemption
    _STRONG_EDUCATIONAL_INDICATORS = (
        "stars die",
        "dinosaurs",
        "chemical formula",
        "life cycle",
        "biology",
        "science",
        "history",
        "geography",
        "astronomy",
        "physics",
        "chemistry",
        "ecosystem",
        "photosynthesis",
        "extinction",
        "evolution",
        "bacteria",
        "cell death",
        "world war",
        "civil war",
        "revolution",
        "ancient",
        "medieval",
        "nuclear energy",
        "atoms",
        "molecules",
        "periodic table",
        "experiment",
        "hypothesis",
        "research",
        "natural disaster",
        "earthquake",
        "volcano",
        "tornado",
        "food chain",
        "predator",
        "prey",
        "habitat",
        "math",
        "algebra",
        "geometry",
        "calculus",
        "arithmetic",
        "trigonometry",
        "equation",
        "fraction",
        "multiplication",
        "reading",
        "writing",
        "spelling",
        "grammar",
        "vocabulary",
        "literature",
        "essay",
        "book report",
        "novel",
        "health class",
        "health education",
    )

    # Generic words that are not sufficient alone to grant exemption;
    # preserved here for use in possible_false_positive detection (Task 2).
    _WEAK_EDUCATIONAL_INDICATORS = (
        "textbook",
        "homework",
        "class",
        "lesson",
        "teacher",
        "assignment",
        "project",
        "study",
        "learn",
        "school",
    )

    # Phrases indicating concerning (non-educational) intent
    _CONCERNING_INDICATORS = (
        "kill someone",
        "kill people",
        "kill a person",
        "how do people die",
        "i die",
        "i want to die",
        "hurt someone",
        "murder someone",
        "attack someone",
        "kill myself",
        "how to kill",
        "best way to kill",
        "cut myself",
        "hurt myself",
    )

    def __init__(self) -> None:
        """Compile all regex patterns once at init time."""
        self._danger_phrases = self._build_danger_phrases()
        self._prohibited_patterns = self._build_prohibited_patterns()
        self._pii_patterns = self._build_pii_patterns()
        logger.info(
            "PatternMatcher initialized (%d danger, %d prohibited, %d PII patterns)",
            len(self._danger_phrases),
            len(self._prohibited_patterns),
            len(self._pii_patterns),
        )

    # -- Danger phrases (CRITICAL, pfp-flagged but never exempted) ------------

    @staticmethod
    def _build_danger_phrases():
        """
        Build compiled regex list for danger phrases.

        Each entry is (compiled_pattern, Category, description).
        """
        raw: list[Tuple[str, Category, str]] = [
            # VIOLENCE
            (
                r"\bhow\s+to\s+make\s+a\s+bomb\b",
                Category.VIOLENCE,
                "bomb-making request",
            ),
            (
                r"\b(make|build|create)\s+(a\s+)?(fertilizer\s+)?bomb\b",
                Category.VIOLENCE,
                "bomb-making request",
            ),
            (
                r"\bhow\s+to\s+build\s+a\s+weapon\b",
                Category.VIOLENCE,
                "weapon-building request",
            ),
            (
                r"\b(kill|murder|attack)\s+(someone|people|a\s+person)\b",
                Category.VIOLENCE,
                "violent intent toward people",
            ),
            (
                r"\bbest\s+way\s+to\s+(kill|hurt)\b",
                Category.VIOLENCE,
                "seeking methods to harm",
            ),
            # EXPLOITATION
            (
                r"\b(send|show|get|find)\s+(me\s+)?(pictures?|photos?|images?|pics|vids|videos?)\s+(of\s+)?(children|kids|minors|child|kid|little\s+(boy|girl)s?)\b",
                Category.EXPLOITATION,
                "child exploitation material request",
            ),
            (
                r"\b(pictures?|photos?|images?)\s+of\s+(children|kids|minors|child|kid)\b",
                Category.EXPLOITATION,
                "child exploitation reference",
            ),
            (
                r"\b(child|kid|minor)\s+(porn|pictures?|photos?|images?)\b",
                Category.EXPLOITATION,
                "child exploitation reference",
            ),
            # SELF-HARM
            (
                r"\bi\s+want\s+to\s+kill\s+myself\b",
                Category.SELF_HARM,
                "suicidal ideation",
            ),
            (r"\bi\s+want\s+to\s+die\b", Category.SELF_HARM, "suicidal ideation"),
            (
                r"\bhow\s+to\s+commit\s+suicide\b",
                Category.SELF_HARM,
                "suicide method seeking",
            ),
            (r"\bend\s+my\s+life\b", Category.SELF_HARM, "suicidal ideation"),
            (
                r"\bi\s+don'?t\s+want\s+to\s+be\s+here\s+anymore\b",
                Category.SELF_HARM,
                "distress / suicidal ideation",
            ),
            (r"\bcut\s+myself\b", Category.SELF_HARM, "self-harm reference"),
            (r"\bsuicid(?:e|al)\b", Category.SELF_HARM, "suicide reference"),
            # WEAPONS
            (
                r"\bhow\s+to\s+(make|create|build)\s+(explosives?|a?\s*bombs?|poisons?|chemical\s+weapons?)\b",
                Category.WEAPONS,
                "weapon/explosive creation request",
            ),
            (
                r"\b(help\s+me|can\s+you\s+help\s+me|i\s+need\s+help|show\s+me)\s+(to\s+)?(make|create|build)\s+(a\s+)?(bomb|explosive|weapon|poison|grenade|molotov)\b",
                Category.WEAPONS,
                "weapon/explosive creation request",
            ),
            (
                r"\bhow\s+to\s+synthesize\b.+\bat\s+home\b",
                Category.WEAPONS,
                "home synthesis request",
            ),
            # BYPASS ATTEMPTS
            (
                r"\b(ignore|forget)\s+(your\s+)?previous\s+instructions\b",
                Category.BYPASS_ATTEMPT,
                "prompt injection attempt",
            ),
            (
                r"\bpretend\s+you\s+have\s+no\s+restrictions\b",
                Category.BYPASS_ATTEMPT,
                "restriction bypass attempt",
            ),
            (
                r"\bbypass\s+your\s+safety\b",
                Category.BYPASS_ATTEMPT,
                "safety bypass attempt",
            ),
            (
                r"\b(developer|god|admin)\s+mode\b",
                Category.BYPASS_ATTEMPT,
                "privilege escalation attempt",
            ),
            (r"\bjailbreak\b", Category.BYPASS_ATTEMPT, "jailbreak attempt"),
        ]
        compiled = []
        for pattern_str, category, description in raw:
            compiled.append(
                (re.compile(pattern_str, re.IGNORECASE), category, description)
            )
        return compiled

    # -- Prohibited keywords (MAJOR, with educational exemption) ---------------

    def _build_prohibited_patterns(self):
        """
        Build compiled word-boundary regex for every prohibited keyword.

        Sources:
            1. safety_config.PROHIBITED_KEYWORDS (category -> keyword_list dict)
            2. Extended hardcoded keywords for comprehensive protection

        Each entry is (compiled_pattern, keyword_text, Category).
        """
        # Gather keywords from config
        config_keywords: Dict[str, list] = {}
        if isinstance(safety_config.PROHIBITED_KEYWORDS, dict):
            for cat, kws in safety_config.PROHIBITED_KEYWORDS.items():
                config_keywords[cat] = list(kws)

        # Extended keyword lists (union with config)
        extended: Dict[str, list] = {
            "violence": [
                "weapon",
                "weapons",
                "bomb",
                "explosive",
                "murder",
                "assault",
                "attack",
                "gun",
                "knife",
                "blade",
                "torture",
                "bombing",
                "weaponize",
                "molotov",
                "detonate",
                "snuff",
            ],
            "self_harm": [
                "suicidal",
                "selfharm",
                "self-harm",
                "overdose",
            ],
            "sexual": [
                "porn",
                "pornography",
                "nude",
                "naked",
                "xxx",
                "sexual",
                "masturbate",
                "escort",
                "explicit",
                "rape",
                "sex",
                "sexting",
                "grooming",
            ],
            "drugs": [
                "cocaine",
                "heroin",
                "meth",
                "marijuana",
                "cannabis",
                "weed",
            ],
            "other": [
                "shoplift",
                "steal",
                "hack",
                "phish",
                "terror",
                "abuse",
            ],
        }

        # Merge config + extended, deduplicate
        all_keywords: Dict[str, set] = {}
        for cat, kws in config_keywords.items():
            all_keywords.setdefault(cat, set()).update(kw.lower() for kw in kws)
        for cat, kws in extended.items():
            all_keywords.setdefault(cat, set()).update(kw.lower() for kw in kws)

        # Map config category names to Category enum
        _cat_map = {
            "violence": Category.VIOLENCE,
            "self_harm": Category.SELF_HARM,
            "sexual": Category.SEXUAL,
            "drugs": Category.DRUGS,
            "personal_info": Category.PII,
            "bullying": Category.BULLYING,
            "dangerous_activity": Category.WEAPONS,
            "other": Category.VIOLENCE,  # fallback
        }

        compiled = []
        seen: set = set()
        for cat, kws in all_keywords.items():
            category_enum = _cat_map.get(cat, Category.VIOLENCE)
            for kw in sorted(kws):
                if kw in seen or not kw:
                    continue
                seen.add(kw)
                try:
                    pat = re.compile(r"\b" + re.escape(kw) + r"\b", re.IGNORECASE)
                    compiled.append((pat, kw, category_enum))
                except re.error:
                    logger.warning("Failed to compile keyword pattern: %s", kw)

        return compiled

    # -- Shared bilingual patterns (from safety.patterns) ----------------------

    _SHARED_CAT_MAP = {
        "HATE_SPEECH": Category.HATE_SPEECH,
        "SEXUAL_CONTENT": Category.SEXUAL,
        "VIOLENCE": Category.VIOLENCE,
        "SELF_HARM": Category.SELF_HARM,
        "PROFANITY": Category.PROFANITY,
        "DRUGS": Category.DRUGS,
        "DEROGATORY": Category.DEROGATORY,
    }

    # -- PII patterns (MAJOR) -------------------------------------------------

    @staticmethod
    def _build_pii_patterns():
        """
        Build compiled regex for personally identifiable information.

        Each entry is (compiled_pattern, description).
        """
        raw = [
            # SSN: 123-45-6789
            (r"\b\d{3}-\d{2}-\d{4}\b", "SSN pattern"),
            # Phone: 123-456-7890 or 123.456.7890 or 1234567890
            (r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b", "phone number"),
            # Email
            (r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b", "email address"),
            # Street address: 123 Main Street
            (
                r"\b\d+\s+[A-Za-z]+\s+(?:street|st|avenue|ave|road|rd|drive|dr|boulevard|blvd|lane|ln|way|court|ct|circle|cir|place|pl)\b",
                "street address",
            ),
            # Credit card: 1234 5678 1234 5678
            (r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b", "credit card number"),
            # Textual PII requests
            (r"\bsocial\s+security\s+number\b", "SSN reference"),
            (
                r"\b(?:my|your)\s+(?:address|phone\s+number|credit\s+card)\b",
                "personal info reference",
            ),
        ]
        return [(re.compile(p, re.IGNORECASE), desc) for p, desc in raw]

    # -- Public check method --------------------------------------------------

    def check(self, original: str, normalized: str) -> Optional[SafetyResult]:
        """
        Run all pattern checks.

        Args:
            original: the user's original text (for word-boundary matches)
            normalized: letters-only normalized form (for obfuscation defeat)

        Returns:
            SafetyResult on block, or None to continue.
        """
        try:
            # 1. Danger phrases (CRITICAL, checked on original AND normalized text)
            original_lower_danger = original.lower()
            for pat, category, description in self._danger_phrases:
                if pat.search(original) or pat.search(normalized):
                    pfp_danger = any(
                        ind in original_lower_danger
                        for ind in self._WEAK_EDUCATIONAL_INDICATORS
                    ) and not any(
                        ind in original_lower_danger
                        for ind in self._CONCERNING_INDICATORS
                    )
                    return _block(
                        Severity.CRITICAL,
                        category,
                        description,
                        stage="pattern",
                        keywords=(pat.pattern,),
                        possible_false_positive=pfp_danger,
                    )

            # 2. Prohibited keywords (MAJOR, with educational exemption)
            original_lower = original.lower()
            for pat, kw, category in self._prohibited_patterns:
                matched = False
                # Check original text (word boundary)
                if pat.search(original):
                    matched = True
                # Check normalized text (obfuscation defeat)
                if not matched and kw.replace(" ", "").replace("-", "") in normalized:
                    matched = True

                if matched:
                    # Educational exemption for contextual keywords
                    if kw in self._CONTEXTUAL_KEYWORDS:
                        if self._has_educational_context(original_lower):
                            continue  # strong indicator → exempt
                        # Check weak indicator → possible false positive
                        pfp = any(
                            ind in original_lower
                            for ind in self._WEAK_EDUCATIONAL_INDICATORS
                        )
                    else:
                        pfp = False
                    return _block(
                        Severity.MAJOR,
                        category,
                        f"Prohibited keyword detected: {kw}",
                        stage="pattern",
                        keywords=(kw,),
                        possible_false_positive=pfp,
                    )

            # 2b. Shared bilingual regex patterns (MAJOR)
            # These use the pre-compiled patterns from safety.patterns,
            # checked on both the original lowercased form and a separator-
            # normalised form to catch underscore/hyphen-separated evasion.
            pre_norm = re.sub(r"[_\-.]", " ", original_lower)
            input_words = set(re.sub(r"[_\-.~\s]+", " ", original_lower).split())
            allowlisted_words = input_words & _SHARED_ALLOWLIST

            for shared_cat, patterns in _SHARED_COMPILED.items():
                category_enum = self._SHARED_CAT_MAP.get(shared_cat, Category.VIOLENCE)
                for regex, desc in patterns:
                    hit_orig = regex.search(original_lower)
                    hit_pre = regex.search(pre_norm)
                    if hit_orig or hit_pre:
                        # Skip false positives from allowlisted words
                        if allowlisted_words:
                            hit = hit_orig or hit_pre
                            matched_text = hit.group(0).strip()
                            if any(matched_text in aw for aw in allowlisted_words):
                                continue
                        # Educational exemption for contextual violence terms
                        if shared_cat == "VIOLENCE" and desc.startswith(
                            "violence term"
                        ):
                            if self._has_educational_context(original_lower):
                                continue
                        return _block(
                            Severity.MAJOR,
                            category_enum,
                            f"Shared pattern matched: {desc}",
                            stage="pattern",
                            keywords=(desc,),
                        )

            # 2c. Substring evasion checks (letters-only normalised form)
            if original_lower not in _SHARED_ALLOWLIST:
                for shared_cat, substr_list in _SHARED_SUBSTR.items():
                    category_enum = self._SHARED_CAT_MAP.get(
                        shared_cat, Category.VIOLENCE
                    )
                    for substr, desc in substr_list:
                        if substr in normalized:
                            return _block(
                                Severity.MAJOR,
                                category_enum,
                                f"Substring evasion detected: {desc}",
                                stage="pattern",
                                keywords=(substr,),
                            )

            # 3. PII patterns (MAJOR, checked on original text)
            for pat, description in self._pii_patterns:
                if pat.search(original):
                    return _block(
                        Severity.MAJOR,
                        Category.PII,
                        f"Personal information detected: {description}",
                        stage="pattern",
                        keywords=(description,),
                    )

            return None  # pass

        except Exception as exc:  # Intentional catch-all: fail closed
            logger.error(
                "Stage 3 (pattern) error, failing closed: %s", exc, exc_info=True
            )
            return _block(
                Severity.MAJOR,
                Category.CLASSIFIER_ERROR,
                "Pattern matching error (fail closed).",
                stage="pattern",
            )

    # -- Educational exemption helper -----------------------------------------

    def _has_educational_context(self, text_lower: str) -> bool:
        """
        Determine if the text has a legitimate educational context.

        Returns True (exempt) only if educational indicators are present
        AND no concerning indicators are found.
        """
        has_educational = any(
            ind in text_lower for ind in self._STRONG_EDUCATIONAL_INDICATORS
        )
        has_concerning = any(ind in text_lower for ind in self._CONCERNING_INDICATORS)

        return has_educational and not has_concerning


# =============================================================================
# 5. Stage 4 -- Semantic Classifier (Ollama LLM)
# =============================================================================


class _SemanticClassifier:
    """
    LLM-based semantic safety classifier using a local Ollama model.

    If Ollama is unavailable at init time the classifier marks itself as
    unavailable and ``classify()`` returns None (skip). The deterministic
    stages (1-3, 5) still protect.  If Ollama errors *during* a classify
    call the result is BLOCK (fail closed).
    """

    CONFIDENCE_THRESHOLD = 0.7

    def __init__(self) -> None:
        self._available = False
        self._model: Optional[str] = None
        self._client = None

        try:
            from utils.ollama_client import (
                OllamaClient as _OllamaClient,
                OllamaError as _OE,
            )  # noqa: F811

            # Use a dedicated short-timeout client for the classifier.
            # The global ollama_client has a 300s timeout (same as the middleware's
            # httpx timeout), so a slow first-inference on a freshly-pulled model
            # would cause a ReadTimeout race that surfaces as "Safety pipeline
            # unavailable" instead of a graceful blocked/skip response.
            # 45s is enough for a warm model; on cold load it will fail-closed
            # (block the message) and recover on the next request once loaded.
            self._client = _OllamaClient(timeout=45, max_retries=1)
            self._OllamaError = _OE

            ok, _version = self._client.check_connection()
            if not ok:
                logger.warning(
                    "Ollama not reachable at init; semantic classifier disabled."
                )
                return

            # Determine which model to use (prefer configured > fallbacks)
            preferred = getattr(safety_config, "SAFETY_MODEL", "llama-guard3:8b")
            fallbacks = getattr(
                safety_config, "SAFETY_MODEL_FALLBACKS", ["llama-guard3:1b"]
            )
            success, models, _err = self._client.list_models()
            if success and models:
                names = [m.get("name", "") for m in models]
                if preferred in names:
                    self._model = preferred
                else:
                    for fallback in fallbacks:
                        if fallback in names:
                            self._model = fallback
                            break

            if self._model:
                self._available = True
                logger.info("Semantic classifier ready (model=%s)", self._model)
            else:
                logger.warning(
                    "No suitable safety model found; semantic classifier disabled."
                )

        except ImportError:
            logger.warning(
                "ollama_client not importable; semantic classifier disabled."
            )
        except Exception as exc:  # Intentional: init must not crash
            logger.warning("Semantic classifier init failed: %s", exc)

    # --------------------------------------------------------------------- #

    def classify(self, text: str, age: Optional[int] = None) -> Optional[SafetyResult]:
        """
        Classify text via the Ollama safety model.

        Returns:
            SafetyResult on block, None to continue (including when unavailable).
        """
        if not self._available or self._client is None:
            return None  # skip -- deterministic stages still protect

        try:
            prompt = self._build_prompt(text, age)
            success, response, _meta = self._client.generate(
                model=self._model,
                prompt=prompt,
                options={"temperature": 0.0, "num_predict": 250},
            )

            if not success or response is None:
                logger.error("Ollama generation failed; failing closed.")
                return _block(
                    Severity.MAJOR,
                    Category.CLASSIFIER_ERROR,
                    "Semantic classifier generation failed (fail closed).",
                    stage="classifier",
                )

            return self._parse_response(response)

        except Exception as exc:  # Intentional catch-all: fail closed
            logger.error(
                "Stage 4 (classifier) error, failing closed: %s", exc, exc_info=True
            )
            return _block(
                Severity.MAJOR,
                Category.CLASSIFIER_ERROR,
                "Semantic classifier error (fail closed).",
                stage="classifier",
            )

    # --------------------------------------------------------------------- #

    @staticmethod
    def _build_prompt(text: str, age: Optional[int]) -> str:
        age_ctx = f"Student age: {age}" if age else "Age unknown"
        return (
            "You are a content safety classifier for a K-12 educational platform "
            "(COPPA/FERPA compliant). Classify the following student message.\n\n"
            f"{age_ctx}\n\n"
            f'Message: "{text}"\n\n'
            "Categories: violence, self_harm, exploitation, sexual, drugs, weapons, "
            "pii, bullying, bypass_attempt, age_inappropriate, acceptable\n\n"
            "Respond ONLY with valid JSON (no markdown, no extra text):\n"
            '{"safe": true/false, "category": "...", "confidence": 0.0-1.0, '
            '"reason": "brief explanation"}'
        )

    # Llama Guard 3 category mapping (S1-S13 codes)
    _GUARD_CATEGORIES = {
        "S1": Category.VIOLENCE,
        "S2": Category.SEXUAL,
        "S3": Category.WEAPONS,
        "S4": Category.WEAPONS,
        "S5": Category.SELF_HARM,
        "S6": Category.EXPLOITATION,
        "S7": Category.EXPLOITATION,
        "S8": Category.PII,
        "S9": Category.BYPASS_ATTEMPT,
        "S10": Category.DRUGS,
        "S11": Category.BULLYING,
        "S12": Category.VIOLENCE,
        "S13": Category.AGE_INAPPROPRIATE,
    }

    def _parse_response(self, raw: str) -> Optional[SafetyResult]:
        """Parse the LLM response. Handles both JSON and Llama Guard plain text format."""
        try:
            text = raw.strip()

            # Handle Llama Guard 3 plain text format: "safe" or "unsafe\nS1"
            text_lower = text.lower()
            if text_lower == "safe":
                return None  # pass
            if text_lower.startswith("unsafe"):
                lines = text.split("\n")
                guard_code = lines[1].strip() if len(lines) > 1 else ""
                category = self._GUARD_CATEGORIES.get(guard_code, Category.VIOLENCE)
                return _block(
                    Severity.MAJOR,
                    category,
                    f"Llama Guard classified as unsafe ({guard_code})",
                    stage="classifier",
                    keywords=(guard_code,) if guard_code else (),
                )

            # Handle markdown code blocks
            if text.startswith("```"):
                lines = text.split("\n")
                json_lines: list[str] = []
                in_block = False
                for line in lines:
                    stripped = line.strip()
                    if stripped.startswith("```"):
                        if in_block:
                            break
                        in_block = True
                        continue
                    if in_block:
                        json_lines.append(line)
                text = "\n".join(json_lines)

            result = json.loads(text)

            is_safe = result.get("safe", False)
            category_str = result.get("category", "")
            confidence = float(result.get("confidence", 0.0))
            reason = result.get("reason", "")

            # If model says safe with acceptable category -> allow
            if is_safe and category_str == "acceptable":
                return None  # pass

            # Map string category to enum
            cat_map = {
                "violence": Category.VIOLENCE,
                "self_harm": Category.SELF_HARM,
                "exploitation": Category.EXPLOITATION,
                "sexual": Category.SEXUAL,
                "drugs": Category.DRUGS,
                "weapons": Category.WEAPONS,
                "pii": Category.PII,
                "bullying": Category.BULLYING,
                "bypass_attempt": Category.BYPASS_ATTEMPT,
                "age_inappropriate": Category.AGE_INAPPROPRIATE,
                "acceptable": Category.VALID,
            }
            category = cat_map.get(category_str, Category.CLASSIFIER_ERROR)

            # If not safe -> block
            if not is_safe:
                return _block(
                    Severity.MAJOR,
                    category,
                    reason or f"Classified as {category_str}",
                    stage="classifier",
                )

            # Safe but non-acceptable category with low confidence -> block
            if category != Category.VALID and confidence < self.CONFIDENCE_THRESHOLD:
                return _block(
                    Severity.MAJOR,
                    category,
                    f"Low confidence ({confidence:.2f}) on category {category_str} (fail closed).",
                    stage="classifier",
                )

            return None  # pass

        except (json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
            logger.error(
                "Failed to parse classifier response: %s (raw=%s)", exc, raw[:200]
            )
            return _block(
                Severity.MAJOR,
                Category.CLASSIFIER_ERROR,
                "Unparseable classifier response (fail closed).",
                stage="classifier",
            )


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
