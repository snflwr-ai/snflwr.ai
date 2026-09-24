"""Canned messages the proxy serves in place of a tutor turn.

These lived in `api/routes/ollama_proxy/chat.py`. They moved here for the reason
already recorded in that file, when the classifier system override made the same
journey:

    "importing an API ROUTE from scripts/ made mypy resolve scripts/ under two
     module names"

`scripts/latency_bar.py` must know every canned reply, so it imports
`core.canned_replies`, which needs these three. That gave `scripts/` a
transitive edge to an API route and broke `mypy scripts/` with
"Source file found twice under different module names: backup_database and
scripts.backup_database" -- while every test passed, because the error is a
module-resolution problem and not a type error.

A message a child reads is not a routing concern, so this is where it belongs.
`chat.py` re-exports all three, so every existing `from ...chat import
_BUSY_MESSAGE` keeps working.
"""

from __future__ import annotations

# Admission control sheds a turn rather than queueing it invisibly. The model
# was fine; the queue was not. So over-capacity turns say so, plainly, and are
# not recorded in the history ledger.
BUSY_MESSAGE = (
    "Lots of learners are asking questions right now, so I could not get to "
    "yours. Please send it again in a moment."
)

# A turn that ran out of time must SAY so. Measured 2026-09-19: with a co-tenant
# holding the GPU, the tutor fell back to CPU, the read timeout fired at 5
# minutes, httpx.ReadTimeout escaped the stream generator unhandled, and the
# child was left looking at an EMPTY bubble with no error -- the stream simply
# ended. Only httpx.ConnectError was caught; a timeout is not a connect error.
TIMEOUT_MESSAGE = (
    "That one took me too long to work out. Please send your question again."
)

# Hardware below the quality floor does not tutor with a smaller model: e4b and
# 12b never met the tutoring bars (4-13 wrong replies per 121 against a bar of 6,
# or acceptable correctness only by stonewalling 38 times), so the honest answer
# is that this machine cannot run the tutor.
UNSUPPORTED_MESSAGE = (
    "The tutor is not available on this computer right now. Ask a grown-up to "
    "check the snflwr.ai setup guide for the hardware it needs."
)
