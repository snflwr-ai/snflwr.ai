"""Regenerate _NEVER_REPAIR in core/pedagogy/trigger.py.

The trigger normalizes student text before matching so that a six-year-old's
"anser" and a teenager's "can u" reach the same patterns as correct spelling.
That fuzzy repair will also destroy real words close to the key vocabulary
("work" -> "word"), so every such word is stoplisted. Run this after changing
_KEY_WORDS or the match threshold, and paste the output into the module.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from core.pedagogy.trigger import _KEY_WORDS, _SHORTHAND, _repair  # noqa: E402

DICT = pathlib.Path("/usr/share/dict/words")


def main() -> int:
    if not DICT.exists():
        print(f"no word list at {DICT}; install wamerican", file=sys.stderr)
        return 1
    words = sorted(
        {
            line.strip().lower()
            for line in DICT.read_text(errors="ignore").splitlines()
            if line.strip().isalpha()
        }
    )
    stop = []
    for word in words:
        if len(word) < 3 or word in _KEY_WORDS or word in _SHORTHAND:
            continue
        repaired = _repair(word)
        if repaired == word:
            continue
        # An inflection collapsing onto its own stem is the point, not a bug.
        if word.startswith(repaired) or repaired.startswith(word):
            continue
        stop.append(word)
    print(" ".join(stop))
    print(f"\n# {len(stop)} words", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
