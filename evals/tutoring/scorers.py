"""
Deterministic, dependency-free scorers for tutoring-quality evaluation.

These measure properties of a tutor response that don't need a judge model:
length-vs-age-band, readability-vs-age-band, whether the response invites the
student to think (a guiding question), and whether it just hands over the
answer to a homework-integrity probe. All functions are pure so they run in
CI without a live model.

Age bands and word-count targets mirror models/Snflwr_AI_Kids.modelfile
(AGE ADAPTATION SYSTEM). Grade ranges map each band to its US school grades
for Flesch-Kincaid comparison.
"""

import re

# band -> {ages, words (min,max), grade (min,max)}
AGE_BANDS = {
    "K-2":  {"ages": (5, 7),   "words": (30, 50),   "grade": (0, 2)},
    "3-5":  {"ages": (8, 10),  "words": (50, 75),   "grade": (3, 5)},
    "6-8":  {"ages": (11, 13), "words": (75, 125),  "grade": (6, 8)},
    "9-12": {"ages": (14, 18), "words": (125, 200), "grade": (9, 12)},
}

_WORD_RE = re.compile(r"[A-Za-z0-9']+")
_SENTENCE_RE = re.compile(r"[.!?]+")
_VOWEL_GROUP_RE = re.compile(r"[aeiouy]+")


def count_words(text: str) -> int:
    """Number of word tokens in the text."""
    return len(_WORD_RE.findall(text or ""))


def count_sentences(text: str) -> int:
    """Number of sentences (terminator runs); minimum 1 for non-empty text."""
    if not text or not text.strip():
        return 0
    n = len([s for s in _SENTENCE_RE.split(text) if s.strip()])
    return max(n, 1)


def count_syllables(word: str) -> int:
    """Heuristic English syllable count: vowel groups, minus a silent
    trailing 'e', floored at 1. Approximate by design."""
    w = re.sub(r"[^a-z]", "", (word or "").lower())
    if not w:
        return 0
    groups = _VOWEL_GROUP_RE.findall(w)
    count = len(groups)
    # Silent trailing 'e' (but not for words like "the" where it's the only vowel).
    if w.endswith("e") and not w.endswith(("le", "ie")) and count > 1:
        count -= 1
    return max(count, 1)


def flesch_kincaid_grade(text: str) -> float:
    """Flesch-Kincaid grade level. 0.0 for empty text."""
    words = _WORD_RE.findall(text or "")
    if not words:
        return 0.0
    sentences = count_sentences(text)
    syllables = sum(count_syllables(w) for w in words)
    grade = (
        0.39 * (len(words) / sentences)
        + 11.8 * (syllables / len(words))
        - 15.59
    )
    return round(grade, 2)


def _range_score(value: float, lo: float, hi: float) -> float:
    """1.0 if value within [lo, hi]; otherwise decays linearly with the
    fractional distance outside the band, floored at 0."""
    if lo <= value <= hi:
        return 1.0
    width = max(hi - lo, 1.0)
    if value < lo:
        dist = lo - value
    else:
        dist = value - hi
    return max(0.0, 1.0 - dist / width)


def length_score(text: str, band: str) -> float:
    """How well the response length fits the band's word-count target (0..1)."""
    lo, hi = AGE_BANDS[band]["words"]
    return _range_score(count_words(text), lo, hi)


def readability_score_for_grade(grade_level: float, band: str) -> float:
    """How well a Flesch-Kincaid grade fits the band's grade range (0..1)."""
    lo, hi = AGE_BANDS[band]["grade"]
    return _range_score(grade_level, lo, hi)


def readability_score(text: str, band: str) -> float:
    """Readability fit for the response text against the band (0..1)."""
    return readability_score_for_grade(flesch_kincaid_grade(text), band)


def asks_guiding_question(text: str) -> bool:
    """True if the response poses a question — a signal of Socratic,
    guide-don't-tell pedagogy."""
    return "?" in (text or "")


def reveals_answer(text: str, answer: str) -> bool:
    """True if the response contains the literal answer as a standalone token.

    Used for homework-integrity probes: when a student says "just give me the
    answer to 7x8", a good tutor guides rather than stating "56". Matches on
    word/number boundaries so "56" doesn't match inside "1560" or "0.567"."""
    answer = (answer or "").strip()
    if not answer:
        return False
    # Not preceded by a word char or dot (rejects 1560 / 0.567); not followed
    # by a word char or a decimal ".<digit>" (rejects 56.7) — but a trailing
    # sentence period ("...is 56.") is fine.
    pattern = r"(?<![\w.])" + re.escape(answer) + r"(?![\w])(?!\.\d)"
    return re.search(pattern, text or "") is not None
