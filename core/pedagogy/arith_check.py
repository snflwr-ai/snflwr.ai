"""Deterministic check of the arithmetic a student SHOWS on a check-my-work turn.

WHY
---
Measured on the live build 2026-09-25 (blind double-graded, kappa 0.868): on
check-my-work turns where the child's answer was WRONG, the tutor told the child
it was CORRECT in 5 of 30 -- "8 / 4 = 4", "84 / 12 = 6", "72 + 6 = 76",
"9/12 - 2/12 = 6/12", "21 + 15 = 35". More often than it revealed an answer (4 of
30), and no enforcement ran on any of those turns. Every one is an expression a
calculator settles exactly, so this needs no model call.

WHAT IT MAY SAY -- AND WHAT IT MUST NEVER SAY
---------------------------------------------
It reports only a step whose ARITHMETIC is wrong. It never reports "the
arithmetic is correct": wrong answers with correct arithmetic and a wrong METHOD
("29 + 5 = 34" where the child should subtract) are common, and a note calling
that arithmetic correct would push the tutor to affirm them. A checker that can
only see arithmetic must not speak about correctness beyond it.

The note never contains the correct value, so it cannot cause a reveal.

UNPARSEABLE IS NOT "FINE"
-------------------------
Text with no evaluable step returns no findings and produces no note -- the
same as today's behaviour, never a verdict.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from fractions import Fraction
from typing import List, Optional

__all__ = ["Step", "check_shown_arithmetic", "wrong_steps", "build_note"]


@dataclass(frozen=True)
class Step:
    expr: str  # the left-hand expression, as written (normalised spacing)
    claimed: str  # the value the student wrote after "="
    correct: bool  # whether expr evaluates to claimed (with rounding allowance)


# -- normalisation ------------------------------------------------------------

_MINUS = str.maketrans(
    {"−": "-", "–": "-", "—": "-", "÷": "/", "×": "*", "⋅": "*", "·": "*"}
)
# `x` is multiplication ONLY between two numbers ("7x8", "50 x 0.3"); next to a
# letter or an operator it is a variable ("4x + 5"), and a step containing a
# variable is not evaluable.
_X_MUL = re.compile(r"(?<=[\d)])\s*[xX]\s*(?=[\d(])")
_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}\b)")
_MONEY = re.compile(r"\$\s*")
_MIXED = re.compile(r"(?<![\d/.])(\d+)\s+(\d+)/(\d+)(?![\d/]|\.\d)")
# A word that continues an expression leftwards in English: "1/2 OF 3/4 = ...".
# When one sits immediately before the left side, the run was cut mid-expression
# and what remains is a fragment, so the step is skipped rather than misjudged.
_VERBAL_OP_BEFORE = re.compile(r"\b(?:of|times|plus|minus|by|over)\s*$", re.IGNORECASE)

# A run of characters that can belong to an arithmetic expression.
_EXPR_CHARS = r"[0-9.+\-*/()%\s]"
# Numbers, operators and brackets only. Anything else ends the run.
_TOKEN = re.compile(r"\s*(\d+(?:\.\d+)?%?|[+\-*/()])")


def _normalise(text: str) -> str:
    t = text.translate(_MINUS)
    t = _MONEY.sub("", t)
    t = _THOUSANDS.sub("", t)
    t = _X_MUL.sub(" * ", t)
    # A mixed number is ONE value: "3 5/4" is 3 + 5/4, not 3 followed by 5/4.
    # Read as two tokens it made correct work look wrong ("3 5/4 = 4 1/4").
    t = _MIXED.sub(r"(\1 + \2/\3)", t)
    return t


# -- a tiny exact evaluator (no eval()) ---------------------------------------


class _Parser:
    def __init__(self, s: str):
        self.toks: List[str] = []
        pos = 0
        s = s.strip()
        while pos < len(s):
            m = _TOKEN.match(s, pos)
            if not m:
                raise ValueError("bad token")
            self.toks.append(m.group(1))
            pos = m.end()
            while pos < len(s) and s[pos].isspace():
                pos += 1
        self.i = 0

    def _peek(self) -> Optional[str]:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def _take(self) -> str:
        t = self.toks[self.i]
        self.i += 1
        return t

    def parse(self) -> Fraction:
        v = self._expr()
        if self._peek() is not None:
            raise ValueError("trailing tokens")
        return v

    def _expr(self) -> Fraction:
        v = self._term()
        while self._peek() in ("+", "-"):
            op = self._take()
            r = self._term()
            v = v + r if op == "+" else v - r
        return v

    def _term(self) -> Fraction:
        v = self._factor()
        while self._peek() in ("*", "/"):
            op = self._take()
            r = self._factor()
            if op == "/":
                if r == 0:
                    raise ValueError("division by zero")
                v = v / r
            else:
                v = v * r
        return v

    def _factor(self) -> Fraction:
        t = self._peek()
        if t is None:
            raise ValueError("unexpected end")
        if t == "-":
            self._take()
            return -self._factor()
        if t == "(":
            self._take()
            v = self._expr()
            if self._peek() != ")":
                raise ValueError("unbalanced")
            self._take()
            return v
        if re.fullmatch(r"\d+(?:\.\d+)?%?", t):
            self._take()
            if t.endswith("%"):
                return Fraction(t[:-1]) / 100
            return Fraction(t)
        raise ValueError("unexpected token")


def _evaluate(s: str) -> Optional[Fraction]:
    try:
        return _Parser(s).parse()
    except (ValueError, ZeroDivisionError, IndexError):
        return None


def _has_operator(s: str) -> bool:
    # A leading minus alone ("-20") is a number, not a computation.
    return bool(re.search(r"(?<=[\d)%\s])\s*[+\-*/]\s*(?=[\d(])", s.strip()))


def _decimals(s: str) -> Optional[int]:
    """Decimal places of a plain written number, or None if it is not one."""
    m = re.fullmatch(r"\s*-?\d+(?:\.(\d+))?\s*", s)
    if not m:
        return None
    return len(m.group(1) or "")


def _matches(value: Fraction, claimed_text: str, claimed: Fraction) -> bool:
    if value == claimed:
        return True
    # A written decimal is accepted when it is `value` rounded to the places the
    # student wrote (10/3 = 3.33). Only for NON-integers written with at least
    # one decimal place: "12" for 12.22 is not accepted, because whether that
    # rounding was allowed is exactly the question the message does not settle.
    places = _decimals(claimed_text)
    if places and value.denominator != 1:
        q = Fraction(1, 10**places)
        lo, hi = claimed - q / 2, claimed + q / 2
        return lo <= value <= hi
    return False


# -- extraction ---------------------------------------------------------------

_RUN = re.compile(_EXPR_CHARS + r"+")


def _operand_run_before(text: str, end: int) -> str:
    """The maximal arithmetic run ending at `end` (the "=" position)."""
    i = end
    while i > 0 and re.match(_EXPR_CHARS, text[i - 1]):
        i -= 1
    return text[i:end]


def _operand_run_after(text: str, start: int) -> str:
    m = _RUN.match(text, start)
    return m.group(0) if m else ""


def _trim_left(run: str) -> str:
    """Drop leading junk until the run parses (e.g. "2 so 20 - 3" -> "20 - 3").

    Shrinks from the LEFT only, token by token, and keeps the longest suffix
    that is a complete expression containing an operator.
    """
    run = run.strip()
    parts = re.split(r"(\s+)", run)
    for k in range(len(parts)):
        cand = "".join(parts[k:]).strip()
        if cand and _has_operator(cand) and _evaluate(cand) is not None:
            return cand
    return ""


def _trim_right_value(run: str) -> str:
    """The value right after "=": the longest leading chunk that parses."""
    run = run.strip()
    parts = re.split(r"(\s+)", run)
    for k in range(len(parts), 0, -1):
        cand = "".join(parts[:k]).strip()
        # A sentence-ending period is not a decimal point: "= 73. is that right"
        # was skipped entirely until this stripped it, so a wrong final step
        # went unchecked.
        for c in (cand, cand.rstrip(".")):
            if c and _evaluate(c) is not None:
                return c
    return ""


def check_shown_arithmetic(user_text: str) -> List[Step]:
    """Every evaluable `<expression> = <value>` the student wrote, checked.

    Chains ("8 / 4 = 2 = 2.0") are checked link by link. A left side with no
    operator ("x = 5", "so 7 cm") is not a computation and is skipped.
    """
    if not user_text or "=" not in user_text:
        return []
    t = _normalise(user_text)
    steps: List[Step] = []
    for m in re.finditer(r"=", t):
        run = _operand_run_before(t, m.start())
        lhs = _trim_left(run)
        if not lhs:
            continue
        lhs_start = (
            m.start() - len(run) + run.index(lhs.split()[0]) if lhs.split() else 0
        )
        if _VERBAL_OP_BEFORE.search(t[:lhs_start]):
            continue
        rhs = _trim_right_value(_operand_run_after(t, m.end()))
        if not rhs:
            continue
        v, c = _evaluate(lhs), _evaluate(rhs)
        if v is None or c is None:
            continue
        steps.append(
            Step(
                re.sub(r"\s+", " ", lhs), re.sub(r"\s+", " ", rhs), _matches(v, rhs, c)
            )
        )
    return steps


def wrong_steps(user_text: str) -> List[Step]:
    return [s for s in check_shown_arithmetic(user_text) if not s.correct]


def build_note(user_text: str) -> Optional[str]:
    """A system note for the tutor, or None. NEVER contains a correct value."""
    bad = wrong_steps(user_text)
    if not bad:
        return None
    s = bad[0]
    return (
        f"The student's step `{s.expr} = {s.claimed}` has an arithmetic error. "
        "Tell them which step to recheck, without stating the correct value. "
        "(Only the arithmetic of the shown steps was checked, not whether they "
        "are the right steps.)"
    )
