"""The login rate limit must hold across API worker processes.

api.server runs 4 workers. With Redis disabled (the home deploy) auth limits fell
back to an in-memory, PER-PROCESS counter, so the documented 5 logins/minute
admitted ~20: on the live box (2026-09-16) 26 rapid failed logins returned 401s
and 429s interleaved, 10 password guesses processed before every worker's
private counter filled.
"""

import multiprocessing as mp
from pathlib import Path

import pytest

WORKERS = 4
ATTEMPTS_EACH = 5
LIMIT = 5


def _attempts(db_path, results_path: str) -> None:
    from utils.rate_limiter import LocalRateLimiter

    limiter = LocalRateLimiter(db_path=db_path)
    allowed = sum(
        1
        for _ in range(ATTEMPTS_EACH)
        if limiter.check_rate_limit("203.0.113.9", LIMIT, 60, "auth")[0]
    )
    with open(results_path, "a") as f:
        f.write(f"{allowed}\n")


def _run(tmp_path: Path, db_path):
    results = tmp_path / "allowed.txt"
    ctx = mp.get_context("spawn")
    procs = [
        ctx.Process(target=_attempts, args=(db_path, str(results)))
        for _ in range(WORKERS)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
        assert p.exitcode == 0
    return sum(int(x) for x in results.read_text().split())


def test_shared_store_enforces_one_limit_across_processes(tmp_path):
    assert _run(tmp_path, str(tmp_path / "rate_limits.db")) == LIMIT


def test_per_process_fallback_multiplies_the_limit(tmp_path):
    """The control: the defect this file exists for. If this ever equals LIMIT, the
    test above no longer proves the shared store is what enforces it."""
    assert _run(tmp_path, None) == LIMIT * WORKERS


def test_shared_store_reports_retry_after_when_blocked(tmp_path):
    from utils.rate_limiter import LocalRateLimiter

    limiter = LocalRateLimiter(db_path=str(tmp_path / "rl.db"))
    for _ in range(LIMIT):
        assert limiter.check_rate_limit("ip", LIMIT, 60, "auth")[0]
    allowed, info = limiter.check_rate_limit("ip", LIMIT, 60, "auth")
    assert allowed is False
    assert info["backend"] == "sqlite"
    assert info["remaining"] == 0
    assert 0 < info["retry_after"] <= 60


def test_unwritable_store_degrades_to_per_process_not_to_no_limit(tmp_path):
    from utils.rate_limiter import LocalRateLimiter

    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    limiter = LocalRateLimiter(db_path=str(blocker / "rl.db"))
    results = [limiter.check_rate_limit("ip", 2, 60, "auth")[0] for _ in range(3)]
    assert results == [True, True, False]


@pytest.mark.parametrize(
    "env,expected_suffix",
    [("memory", None), ("/srv/custom.db", "/srv/custom.db")],
)
def test_shared_db_path_overrides(monkeypatch, env, expected_suffix):
    from utils import rate_limiter

    monkeypatch.setenv("SNFLWR_RATE_LIMIT_DB", env)
    assert rate_limiter._shared_db_path() == expected_suffix


def test_shared_db_path_defaults_next_to_app_data(monkeypatch):
    from utils import rate_limiter

    monkeypatch.delenv("SNFLWR_RATE_LIMIT_DB", raising=False)
    path = rate_limiter._shared_db_path()
    assert path is not None and path.endswith("rate_limits.db")
