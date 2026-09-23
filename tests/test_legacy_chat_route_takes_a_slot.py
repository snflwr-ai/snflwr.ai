"""The legacy /api/chat/send route must be admitted like any other turn.

`core/inference/admission.py` enforces `slots=N` for the BOX, not the process:
a per-process `asyncio.Semaphore` sits in front of a shared SQLite reservation
table, because eight worker processes each honouring `slots=1` admitted eight
concurrent turns onto a card that holds one model (the same shape as the login
rate limit that advertised 5/min and admitted ~20 across four workers, #272).

The child path holds a slot via `Depends(admission.inference_slot)`. This route
did not, so a turn through it was INVISIBLE to that count -- competing for the
one card the tutor fits on without being admitted. Measured 2026-09-17: at 20
concurrent turns on one card, 40 of 60 replies became the canned fallback purely
from queueing, so an uncounted turn degrades other children's answers.

Fixed 2026-09-23 by taking the slot inside the handler. Inside, not as a route
dependency, because as a dependency `EngineOverloaded` becomes `InferenceBusy`
and the app handler answers with an OLLAMA-shaped body -- not this route's
`ChatResponse` contract.
"""

import ast
import pathlib

ROUTE = pathlib.Path(__file__).resolve().parent.parent / "api" / "routes" / "chat.py"
SRC = ROUTE.read_text()


def test_the_model_call_is_inside_an_admission_slot():
    """The ollama call must be lexically inside a `turn()` context."""
    tree = ast.parse(SRC)

    def call_is_guarded(node: ast.AST) -> bool:
        """True if an ollama_client.chat call sits under an async with turn()."""
        for withstmt in ast.walk(node):
            if not isinstance(withstmt, ast.AsyncWith):
                continue
            guards = ast.dump(ast.Module(body=list(withstmt.items), type_ignores=[]))
            if "turn" not in guards:
                continue
            for inner in ast.walk(withstmt):
                if (
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr == "chat"
                    and isinstance(inner.func.value, ast.Name)
                    and inner.func.value.id == "ollama_client"
                ):
                    return True
        return False

    assert call_is_guarded(tree), (
        "ollama_client.chat in api/routes/chat.py is not inside an "
        "`async with ...turn():` block, so this route takes no admission slot "
        "and its turns are invisible to the box-wide slot count"
    )


def test_overload_returns_the_routes_own_response_shape():
    """On EngineOverloaded it must answer ChatResponse, not an Ollama body."""
    assert "except EngineOverloaded:" in SRC
    # The handler must construct ChatResponse on that path, not fall through
    # to the app-level InferenceBusy handler (which emits an Ollama shape).
    idx = SRC.index("except EngineOverloaded:")
    tail = SRC[idx : idx + 900]
    assert "ChatResponse(" in tail, "overload path does not return a ChatResponse"
    assert "_BUSY_MESSAGE" in tail, "overload path does not use the shared busy text"


def test_the_busy_text_is_imported_not_duplicated():
    """A second copy of a canned string is how #306's sentinel drift happened."""
    assert "from api.routes.ollama_proxy.chat import _BUSY_MESSAGE" in SRC
    assert "Lots of learners" not in SRC, (
        "the busy message is re-typed here; import it so it cannot drift"
    )


def test_the_shared_slot_table_is_what_makes_slots_box_wide():
    """Guard the mechanism itself: a semaphore alone is per process."""
    adm = (ROUTE.parent.parent.parent / "core" / "inference" / "admission.py").read_text()
    assert "sqlite3" in adm, "the shared reservation store is gone"
    assert "CREATE TABLE IF NOT EXISTS inflight" in adm
    assert "asyncio.Semaphore" in adm, "the per-process fair-queueing layer is gone"
