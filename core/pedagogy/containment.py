"""Model-free containment check: did the reply hand over an item we can NAME?

This is a narrow, high-precision layer that runs alongside the LLM confirm and
can only ADD detections. It exists because two classes of assigned item are
derivable from the student's own request with no model at all:

* **spelling** -- "how do you spell library?" names the item outright.
* **an arithmetic result** -- "what is 33 - 15?" and "solve 3x + 7 = 22" name an
  expression we can evaluate exactly.

For those, whether the reply supplies the item is a decidable question about
strings and numbers, and a decidable question should not be asked of a model
that is right 84% of the time.

**Deliberately narrow.** It cannot see a thesis, a critique, a translation or a
summary, because nothing in the request says what the answer IS. Those stay the
confirm's job, and this module must never be described as covering them. Its
value is that where it fires, it is not a judgement -- and one of those classes
is the most glaring failure in the whole product: a child asked to spell a word,
handed the spelling.

**Assembly counts.** The spelling check compares letter sequences with all
punctuation, quoting and spacing removed, because the measured failure was not
the bare word -- it was `"li" + "brar" + "y"`, which a substring search for
`library` does not find. The owner's rubric ruling is that chunks which assemble
to the item with nothing left for the child ARE the item.
"""

from __future__ import annotations

import ast
import operator
import re
from dataclasses import dataclass
from fractions import Fraction

# ---------------------------------------------------------------------------
# What the student asked for
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Target:
    """An item named by the request, and the strings that would give it away."""

    kind: str  # "spelling" | "value"
    label: str  # human-readable, for logs and the nudge
    needles: tuple[str, ...]  # normalised forms; any occurrence is a reveal


# "how do you spell library", "spell 'library' for me", "the spelling of library".
# The word must be a plain alphabetic token: a quoted phrase or a sentence is a
# different task (write/translate), not a spelling item.
_SPELL_RE = re.compile(
    r"""(?:how\s+(?:do|d')\s*(?:you|u|i)\s+spell|spell(?:ing)?\s+(?:of|the\s+word)?|spell)
        \s* ["'“‘]? \s* ([A-Za-z]{3,}) """,
    re.IGNORECASE | re.VERBOSE,
)

# A bare arithmetic expression: digits, + - * / ^, parentheses, decimal points.
# Anchored on a digit at each end so "3 or 4 sentences" and "page 12-15" do not
# parse as sums. Kept intentionally simple -- a wrong target is worse than none.
_ARITH_RE = re.compile(r"\d[\d\s+\-*/^().]*\d")

# "3x + 7 = 22" and "solve for x: 2x-5=11". One unknown, linear, integer-ish.
_LINEAR_RE = re.compile(
    r"(-?\d*)\s*([a-z])\s*([+-]\s*\d+)?\s*=\s*(-?\d+)", re.IGNORECASE
)

_SAFE_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
}


def _eval_arith(text: str) -> Fraction | None:
    """Evaluate a simple arithmetic string exactly, or return None.

    `ast.literal_eval` will not do arithmetic and `eval` is not an option on a
    string that came from a child's message, so the expression is parsed and
    walked with an explicit whitelist. Fraction keeps 1/3 exact, so a reply
    saying "0.333" is not compared against a silently rounded target.
    """
    expr = text.replace("^", "**").strip()
    if not expr or len(expr) > 60:
        return None
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return None

    def walk(node):
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise ValueError("non-numeric constant")
            return Fraction(str(node.value))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            v = walk(node.operand)
            return v if isinstance(node.op, ast.UAdd) else -v
        if isinstance(node, ast.BinOp) and type(node.op) in _SAFE_BINOPS:
            left, right = walk(node.left), walk(node.right)
            if isinstance(node.op, ast.Pow):
                # An exponent big enough to hang the process is not homework.
                if right.denominator != 1 or abs(right) > 8:
                    raise ValueError("exponent out of range")
                return Fraction(left ** int(right))
            if isinstance(node.op, ast.Div) and right == 0:
                raise ValueError("division by zero")
            return _SAFE_BINOPS[type(node.op)](left, right)
        raise ValueError(f"disallowed node {type(node).__name__}")

    try:
        return walk(tree)
    except (ValueError, ZeroDivisionError, OverflowError, TypeError):
        return None


_UNITS = (
    "zero one two three four five six seven eight nine ten eleven twelve "
    "thirteen fourteen fifteen sixteen seventeen eighteen nineteen"
).split()
_TENS = "  twenty thirty forty fifty sixty seventy eighty ninety".split(" ")


def number_words(n: int) -> list[str]:
    """Word forms for an integer, for the word-form blind spot.

    The measured miss was not a digit: it was "four" for 2+2 and "three fifths".
    A digit-only matcher shares the blind spot that let 11/11 real reveals
    through the removed regex gate, so any numeric target carries its words too.
    """
    if n < 0:
        return [f"negative {w}" for w in number_words(-n)] + [
            f"minus {w}" for w in number_words(-n)
        ]
    if n < 20:
        return [_UNITS[n]]
    if n < 100:
        tens, unit = divmod(n, 10)
        base = _TENS[tens]
        if not unit:
            return [base]
        return [f"{base}-{_UNITS[unit]}", f"{base} {_UNITS[unit]}"]
    if n < 1000:
        hundreds, rest = divmod(n, 100)
        head = f"{_UNITS[hundreds]} hundred"
        if not rest:
            return [head]
        return [f"{head} {tail}" for tail in number_words(rest)] + [
            f"{head} and {tail}" for tail in number_words(rest)
        ]
    return []  # beyond the range this is worth guessing at


def _value_needles(value: Fraction) -> tuple[str, ...]:
    out: list[str] = []
    if value.denominator == 1:
        n = int(value)
        out.append(str(n))
        out.extend(number_words(n))
    else:
        out.append(f"{value.numerator}/{value.denominator}")
        as_float = float(value)
        # Two ways a model writes a non-integer, both worth catching.
        out.append(f"{as_float:.2f}".rstrip("0").rstrip("."))
        out.append(f"{as_float:.3f}".rstrip("0").rstrip("."))
    return tuple(dict.fromkeys(o for o in out if o))


def extract_targets(student_request: str) -> list[Target]:
    """Items the request NAMES. Empty when nothing is decidable -- the norm."""
    text = student_request or ""
    targets: list[Target] = []

    for word in _SPELL_RE.findall(text):
        lowered = word.lower()
        # "spell it", "spell out the steps" name no word.
        if lowered in {"it", "that", "this", "out", "them", "the", "word", "one"}:
            continue
        targets.append(Target("spelling", lowered, (re.sub(r"[^a-z]", "", lowered),)))

    for expr in _ARITH_RE.findall(text):
        if not any(op in expr for op in "+-*/^"):
            continue  # a bare number is not a question
        value = _eval_arith(expr)
        if value is None:
            continue
        targets.append(
            Target("value", f"{expr.strip()} = {value}", _value_needles(value))
        )

    for coef, var, const, rhs in _LINEAR_RE.findall(text):
        a = (
            Fraction(coef)
            if coef not in ("", "-", "+")
            else Fraction(-1 if coef == "-" else 1)
        )
        if a == 0:
            continue
        b = Fraction(re.sub(r"\s+", "", const) or 0)
        solved = (Fraction(rhs) - b) / a
        targets.append(Target("value", f"{var} = {solved}", _value_needles(solved)))

    # Same item named twice (an expression the linear matcher also read) would
    # double-count in any yield figure, so collapse on the needles.
    seen, unique = set(), []
    for t in targets:
        if t.needles in seen:
            continue
        seen.add(t.needles)
        unique.append(t)
    return unique


# ---------------------------------------------------------------------------
# Did the reply hand it over
# ---------------------------------------------------------------------------

_LETTERS_ONLY = re.compile(r"[^a-z]")


def _normalise_for_spelling(text: str) -> str:
    """Letters only, lowercased.

    This is the whole point of the spelling check: `"li" + "brar" + "y"`,
    `l-i-b-r-a-r-y` and `L I B R A R Y` all collapse to `library`, and a
    substring search for the plain word finds none of them.
    """
    return _LETTERS_ONLY.sub("", (text or "").lower())


def _number_boundary_hit(haystack: str, needle: str) -> bool:
    """Match a number without matching it inside another number.

    "7" must not fire on "1972" and "15" must not fire on "150", but a SENTENCE
    may legitimately end in the answer: "That gives 3/5." A naive
    `(?![\\d.])` lookahead rejected exactly that, so the dot is excluded only
    when a digit follows it (a decimal), not when it is punctuation.
    """
    pattern = rf"(?<!\d)(?<!\d\.){re.escape(needle)}(?!\d)(?!\.\d)"
    return re.search(pattern, haystack) is not None


def contains_target(reply: str, targets: list[Target]) -> Target | None:
    """The first target the reply hands over, or None.

    Returns the target rather than a bool so the caller can name the item in a
    log line or a nudge without re-deriving it.
    """
    if not reply or not targets:
        return None
    lowered = reply.lower()
    letters = _normalise_for_spelling(reply)

    for target in targets:
        for needle in target.needles:
            if target.kind == "spelling":
                if needle and needle in letters:
                    return target
            elif needle.replace(".", "").replace("/", "").isdigit() or "/" in needle:
                if _number_boundary_hit(lowered, needle):
                    return target
            elif needle in lowered:
                return target
    return None


def reveals_by_containment(student_request: str, reply: str) -> Target | None:
    """Convenience wrapper: extract, then check. None means "cannot decide"."""
    return contains_target(reply, extract_targets(student_request))
