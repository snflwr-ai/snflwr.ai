"""Unit tests for the ConnectionTracker in-flight-request gauge."""
import asyncio

import pytest

from api.connection_tracking import ConnectionTracker, connection_tracker


def test_module_instance_exists():
    assert isinstance(connection_tracker, ConnectionTracker)


@pytest.mark.asyncio
async def test_increment_decrement():
    t = ConnectionTracker()
    assert t.count == 0
    await t.increment()
    await t.increment()
    assert t.count == 2
    await t.decrement()
    assert t.count == 1


@pytest.mark.asyncio
async def test_concurrent_increments_are_consistent():
    t = ConnectionTracker()
    await asyncio.gather(*(t.increment() for _ in range(50)))
    assert t.count == 50
    await asyncio.gather(*(t.decrement() for _ in range(50)))
    assert t.count == 0
