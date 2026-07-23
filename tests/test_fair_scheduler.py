"""Tests for _FairScheduler — the anti-starvation gate.

Guarantees: hard concurrency ceiling, least-recently-served ordering under
contention (so a crowded-out pack is favoured next), FIFO among equal priority,
permit transfer without over-count, and clean handling of cancel/timeout so a
waiter never leaks a slot. No mocks — the real primitive is driven directly.
"""
import asyncio

import pytest

from custom_components.srne_ble.connection_manager import _FairScheduler


def _run(coro):
    return asyncio.run(coro)


def test_uncontended_acquire_is_immediate():
    async def go():
        s = _FairScheduler(2)
        await s.acquire(0.0)
        assert s.held == 1
        await s.acquire(0.0)
        assert s.held == 2

    _run(go())


def test_concurrency_ceiling_blocks_third_until_release():
    async def go():
        s = _FairScheduler(2)
        await s.acquire(0.0)
        await s.acquire(0.0)
        got = []

        async def third():
            await s.acquire(0.0)
            got.append(True)

        t = asyncio.create_task(third())
        await asyncio.sleep(0.02)
        assert got == []          # blocked at the ceiling
        assert s.held == 2        # never 3
        s.release()
        await asyncio.sleep(0.02)
        assert got == [True]
        assert s.held == 2        # permit transferred, not dropped
        await t

    _run(go())


def test_least_recently_served_is_favoured():
    async def go():
        s = _FairScheduler(1)
        await s.acquire(0.0)      # holder takes the only slot
        order = []

        async def w(key):
            await s.acquire(key)
            order.append(key)

        t_hi = asyncio.create_task(w(5.0))   # served more recently -> lower priority
        await asyncio.sleep(0.01)
        t_lo = asyncio.create_task(w(2.0))   # starved longer -> higher priority
        await asyncio.sleep(0.01)
        s.release()
        await asyncio.sleep(0.01)
        assert order == [2.0]     # the more-starved waiter won the freed slot
        s.release()
        await asyncio.gather(t_hi, t_lo)
        assert order == [2.0, 5.0]

    _run(go())


def test_fifo_among_equal_priority():
    async def go():
        s = _FairScheduler(1)
        await s.acquire(0.0)
        order = []

        async def w(tag):
            await s.acquire(1.0)  # equal keys
            order.append(tag)

        a = asyncio.create_task(w("A"))
        await asyncio.sleep(0.01)
        b = asyncio.create_task(w("B"))
        await asyncio.sleep(0.01)
        s.release()
        await asyncio.sleep(0.01)
        s.release()
        await asyncio.gather(a, b)
        assert order == ["A", "B"]   # arrival order preserved on ties

    _run(go())


def test_cancelled_waiter_is_removed_and_leaks_no_slot():
    async def go():
        s = _FairScheduler(1)
        await s.acquire(0.0)

        async def w():
            await s.acquire(1.0)

        t = asyncio.create_task(w())
        await asyncio.sleep(0.01)
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
        assert s._waiters == []   # our place was dropped
        s.release()
        assert s.held == 0        # slot fully returned, not leaked

    _run(go())


def test_timeout_waiter_gives_up_without_holding_a_slot():
    async def go():
        s = _FairScheduler(1)
        await s.acquire(0.0)      # holder never releases
        with pytest.raises(asyncio.TimeoutError):
            await s.acquire(1.0, timeout=0.05)
        assert s.held == 1        # only the holder counts
        assert s._waiters == []   # timed-out waiter cleaned up

    _run(go())
