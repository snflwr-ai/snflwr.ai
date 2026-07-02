"""Stage 3 deterministic pattern matcher.

Extracted verbatim from the former monolithic safety/pipeline.py.
"""

from __future__ import annotations

import re
from typing import Dict, Optional, Tuple

from config import safety_config
from safety.patterns import (
    COMPILED_PATTERNS as _SHARED_COMPILED,
)
from safety.patterns import (
    FALSE_POSITIVE_ALLOWLIST as _SHARED_ALLOWLIST,
)
from safety.patterns import (
    SUBSTR_CHECKS as _SHARED_SUBSTR,
)
from safety.patterns import normalize_text as _normalize_text
from safety.pipeline.models import Category, SafetyResult, Severity, _block
from utils.logger import get_logger

logger = get_logger(__name__)


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

    # Common leet / homoglyph substitutions, so the obfuscation matcher below
    # also catches "m3th", "k1ll", "dru9", etc.
    _LEET_VARIANTS = {
        "a": "a@4",
        "b": "b8",
        "e": "e3",
        "g": "g69",
        "i": "i1!|",
        "l": "l1|",
        "o": "o0",
        "s": "s5$",
        "t": "t7",
        "z": "z2",
    }
    _OBF_CACHE: dict = {}

    # Categories whose PLAIN-TEXT matches are educational-topic (war/violence,
    # weapons, drugs) and may be deferred to the semantic classifier.
    _DEFERRABLE_CATS = frozenset({Category.VIOLENCE, Category.WEAPONS, Category.DRUGS})

    @classmethod
    def _obfuscation_pattern(cls, keyword: str):
        """Boundary-anchored, obfuscation-tolerant pattern for ``keyword``.

        Matches the keyword's letters in order, tolerating non-word separators
        between them ("m-e-t-h", "m e t h", zero-width chars) and common leet
        substitutions ("m3th"), but ANCHORED so the keyword must be a whole
        token — never a substring of an innocent word. This fixes the
        "Scunthorpe problem": 'meth' must not match 'method', 'kill' must not
        match 'skill', 'coon' must not match 'raccoon'.
        """
        cached = cls._OBF_CACHE.get(keyword)
        if cached is not None:
            return cached
        letters = keyword.replace(" ", "").replace("-", "").lower()
        if len(letters) < 3:
            # Too short to obfuscation-match without risking FPs; the plain
            # word-boundary regex (\bkw\b) already covers the literal form.
            pattern = re.compile(r"(?!x)x")  # never matches
        else:
            classes = []
            for ch in letters:
                variants = cls._LEET_VARIANTS.get(ch, ch)
                classes.append(
                    "[" + re.escape(variants) + "]"
                    if len(variants) > 1
                    else re.escape(ch)
                )
            body = r"[\W_]*".join(classes)
            # Lookarounds (not \b) so a leading/trailing separator in the
            # obfuscated form doesn't defeat the boundary.
            pattern = re.compile(
                r"(?<![a-z0-9])" + body + r"(?![a-z0-9])", re.IGNORECASE
            )
        cls._OBF_CACHE[keyword] = pattern
        return pattern

    _SUBSTR_PREFIX_CACHE: dict = {}

    @classmethod
    def _substr_prefix_pattern(cls, substr: str):
        """Leading-boundary, separator/leet-tolerant PREFIX matcher for a
        SUBSTR_CHECKS entry.

        Unlike ``_obfuscation_pattern`` there is NO trailing boundary, so the
        intentional stems in SUBSTR_CHECKS still match their suffixes
        (``murder``->murderer, ``porn``->pornography, ``masturbat``->
        masturbating). But the entry must START at a word boundary, so it no
        longer matches as a suffix inside an innocent word (``coon`` in
        'raccoon', ``killer`` in 'painkiller', ``rape`` in 'grape',
        ``spic`` in 'suspicious').
        """
        cached = cls._SUBSTR_PREFIX_CACHE.get(substr)
        if cached is not None:
            return cached
        letters = substr.replace(" ", "").replace("-", "").lower()
        classes = []
        for ch in letters:
            variants = cls._LEET_VARIANTS.get(ch, ch)
            classes.append(
                "[" + re.escape(variants) + "]" if len(variants) > 1 else re.escape(ch)
            )
        body = r"[\W_]*".join(classes)
        pattern = re.compile(r"(?<![a-z0-9])" + body, re.IGNORECASE)
        cls._SUBSTR_PREFIX_CACHE[substr] = pattern
        return pattern

    # Innocent words that share a PREFIX with a SUBSTR_CHECKS entry (so the
    # leading-boundary prefix match would still flag them). A keyword match that
    # falls inside one of these spans is suppressed. Whole-word/suffix collisions
    # (raccoon, painkiller, grape, suspicious) are already handled by the leading
    # boundary and don't need listing here.
    _SUBSTR_ALLOWLIST_RE = re.compile(
        r"(?<![a-z0-9])(?:"
        r"shi+take|"  # shitake / shiitake (mushroom)  vs 'shit'
        r"retardants?|retardation|"  # fire retardant / growth retardation vs 'retard'
        r"spic(?:e|es|y|ily|ier|iest|a)|"  # spice/spicy/spica (NOT 'spics') vs 'spic'
        r"van\s*dyke|vandyke|"  # Van Dyke (name / beard style) vs 'dyke'
        r"niger(?:ia|ian|ien)?|"  # Niger / Nigeria / Nigerian (country) vs slur
        # 'Dick' as a proper noun (Cheney, Moby Dick, Van Dyke, spotted dick…) —
        # bare vulgar 'dick' is NOT listed and still blocks.
        r"dick\s+(?:cheney|clark|tracy|grayson|smith|van\s*dyke)|"
        r"moby\s*dick|spotted\s+dick|philip\s+k\.?\s*dick"
        r")(?![a-z0-9])",
        re.IGNORECASE,
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
        # Health / medical / life-science subjects — legitimate contexts for
        # words like "drug(s)", "die", "blood". These are academic subject terms
        # that are very unlikely in a genuine drug-seeking / harmful request.
        "anatomy",
        "physiology",
        "human body",
        "the body",
        "nervous system",
        "immune system",
        "circulatory system",
        "respiratory system",
        "bloodstream",
        "medicine",
        "medication",
        "pharmacology",
        "nutrition",
        "first aid",
        "vaccine",
        "disease",
        "infection",
        "bacteria and viruses",
        "how medicines work",
        "how medicine works",
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
        for cat, kw_set in all_keywords.items():
            category_enum = _cat_map.get(cat, Category.VIOLENCE)
            for kw in sorted(kw_set):
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

    def check(
        self, original: str, normalized: str, context: str = ""
    ) -> Optional[SafetyResult]:
        """
        Run all pattern checks.

        Args:
            original: the user's original text (for word-boundary matches)
            normalized: letters-only normalized form (for obfuscation defeat)
            context: optional extra text whose educational context is INHERITED
                for the contextual-keyword exemption. Used when checking model
                OUTPUT so that an answer to an educational question (e.g. "...for
                my biology homework") keeps the exemption even if the answer text
                alone lacks an indicator. Only affects MAJOR contextual keywords
                and the violence-term educational exemption — never CRITICAL
                danger phrases or the semantic classifier.

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
            # Educational context: present in this text OR inherited from the
            # originating question (passed as `context` when vetting OUTPUT).
            has_edu_context = self._has_educational_context(original_lower) or (
                bool(context) and self._has_educational_context(context.lower())
            )
            # Structure-preserving normalized form: folds homoglyphs/leet and
            # collapses separator obfuscation ("m-e-t-h" -> "meth") while KEEPING
            # word boundaries (unlike the letters-only `normalized`). Boundary-
            # aware keyword matching runs on this so obfuscation is still caught
            # but 'meth' no longer matches inside 'method'.
            try:
                folded_lower = _normalize_text(original)[0].lower()
            except Exception:
                folded_lower = original_lower
            for pat, kw, category in self._prohibited_patterns:
                matched = False
                matched_plain = False
                # Check original text (word boundary) — a PLAIN-TEXT match.
                if pat.search(original):
                    matched = True
                    matched_plain = True
                # Check obfuscated form (separator/leet/homoglyph evasion),
                # boundary-aware so the keyword matches only as a whole token,
                # never as a substring of an innocent word ('meth' in 'method').
                if not matched and self._obfuscation_pattern(kw).search(folded_lower):
                    matched = True

                if matched:
                    # Educational exemption for contextual keywords
                    if kw in self._CONTEXTUAL_KEYWORDS:
                        if has_edu_context:
                            continue  # strong indicator (incl. inherited) → exempt
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
                        # Plain-text educational-topic keyword (kill/bomb/gun/drug
                        # in history/literature/science) → let the classifier
                        # adjudicate. Obfuscated matches are evasion, never deferred.
                        deferrable=matched_plain and category in self._DEFERRABLE_CATS,
                    )

            # 2b. Shared bilingual regex patterns (MAJOR)
            # These use the pre-compiled patterns from safety.patterns,
            # checked on both the original lowercased form and a separator-
            # normalised form to catch underscore/hyphen-separated evasion.
            pre_norm = re.sub(r"[_\-.]", " ", original_lower)
            input_words = set(re.sub(r"[_\-.~\s]+", " ", original_lower).split())
            allowlisted_words = input_words & _SHARED_ALLOWLIST
            # Innocent homograph/prefix spans (e.g. "Van Dyke" surname vs the
            # 'dyke' slur, "spice" vs 'spic') — a regex hit inside one is an FP.
            allow_spans_orig = [
                mm.span() for mm in self._SUBSTR_ALLOWLIST_RE.finditer(original_lower)
            ]
            allow_spans_pre = [
                mm.span() for mm in self._SUBSTR_ALLOWLIST_RE.finditer(pre_norm)
            ]

            for shared_cat, patterns in _SHARED_COMPILED.items():
                category_enum = self._SHARED_CAT_MAP.get(shared_cat, Category.VIOLENCE)
                for regex, desc in patterns:
                    hit_orig = regex.search(original_lower)
                    hit_pre = regex.search(pre_norm)
                    if hit_orig or hit_pre:
                        # Skip innocent homographs (Van Dyke, spice, …)
                        if hit_orig and any(
                            s <= hit_orig.start() < e for s, e in allow_spans_orig
                        ):
                            continue
                        if (
                            hit_pre
                            and not hit_orig
                            and any(
                                s <= hit_pre.start() < e for s, e in allow_spans_pre
                            )
                        ):
                            continue
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
                            if has_edu_context:
                                continue
                        return _block(
                            Severity.MAJOR,
                            category_enum,
                            f"Shared pattern matched: {desc}",
                            stage="pattern",
                            keywords=(desc,),
                            # Plain-text (hit_orig) educational-topic term →
                            # defer to classifier. Separator-obfuscated (hit_pre
                            # only) matches are evasion, never deferred.
                            deferrable=bool(hit_orig)
                            and category_enum in self._DEFERRABLE_CATS,
                        )

            # 2c. Substring evasion checks (leading-boundary prefix match on the
            # structure-preserving folded form). Stems still match their
            # suffixes (murder->murderer), but whole-word entries no longer match
            # inside innocent words (coon in raccoon). A small allowlist covers
            # innocent words that share a prefix with an entry (shitake, spice).
            if original_lower not in _SHARED_ALLOWLIST:
                allow_spans = [
                    mm.span() for mm in self._SUBSTR_ALLOWLIST_RE.finditer(folded_lower)
                ]
                for shared_cat, substr_list in _SHARED_SUBSTR.items():
                    category_enum = self._SHARED_CAT_MAP.get(
                        shared_cat, Category.VIOLENCE
                    )
                    for substr, desc in substr_list:
                        m = self._substr_prefix_pattern(substr).search(folded_lower)
                        if m and not any(s <= m.start() < e for s, e in allow_spans):
                            # Plain-text (matches the un-folded original) violence
                            # topic word (genocide/massacre/murder) → defer to
                            # classifier. Obfuscated matches are never deferred.
                            plain = self._substr_prefix_pattern(substr).search(
                                original_lower
                            )
                            return _block(
                                Severity.MAJOR,
                                category_enum,
                                f"Substring evasion detected: {desc}",
                                stage="pattern",
                                keywords=(substr,),
                                deferrable=bool(plain)
                                and category_enum in self._DEFERRABLE_CATS,
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
