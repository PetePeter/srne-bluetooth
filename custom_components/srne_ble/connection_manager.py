"""Shared BLE connection manager for the SRNE integration.

Owns the connect -> use -> release lifecycle for every pack so no coordinator
re-implements it. Access is FAIR-QUEUED through ``_FairScheduler``: up to
``max_connections`` packs connect at once, and whenever a slot frees it is
granted to the pack that has gone longest without a turn (least-recently-served
first). So even if the hardware wedges some proxy slots, the remaining slot(s)
are shared by every pack in rotation — no pack can be starved by the others.

Every ``session`` GUARANTEES the slot + permit are freed on every exit path
(success, error, cancellation, or a hung disconnect — teardown is
timeout-bounded in the transport). The ``active`` registry exposes what is in
flight for diagnostics.
"""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Callable

from .const import SLOT_WAIT_TIMEOUT
from .transport import SrneBleError, SrneBleTransport

_LOGGER = logging.getLogger(__name__)


class _FairScheduler:
    """Concurrency gate for up to ``size`` holders with anti-starvation ordering.

    A free slot is taken immediately when uncontended. Under contention the next
    freed slot is transferred to the waiter with the smallest ``key`` (the pack
    that has waited/gone-unserved longest), with FIFO order among equal keys.
    The permit is transferred directly to the woken waiter (the held count never
    dips between release and re-acquire), so ``size`` is a hard ceiling.
    """

    def __init__(self, size: int) -> None:
        self._size = size
        self._held = 0
        self._waiters: list[list] = []  # [key, future]

    @property
    def held(self) -> int:
        return self._held

    async def acquire(self, key: float, timeout: float | None = None) -> None:
        loop = asyncio.get_running_loop()
        if self._held < self._size and not self._waiters:
            self._held += 1
            return
        fut = loop.create_future()
        entry = [key, fut]
        self._waiters.append(entry)
        try:
            await (fut if timeout is None else asyncio.wait_for(fut, timeout))
        except BaseException:
            # Timed out or cancelled. If the permit had already been transferred
            # to us (fut resolved) but we can't take it, pass it on so it isn't
            # lost; otherwise just drop our place in the queue.
            if fut.done() and not fut.cancelled():
                self.release()
            elif entry in self._waiters:
                self._waiters.remove(entry)
            raise
        # Permit transferred to us: held already accounts for this slot.

    def release(self) -> None:
        if self._waiters:
            idx = min(range(len(self._waiters)), key=lambda i: self._waiters[i][0])
            _, fut = self._waiters.pop(idx)
            if not fut.done():
                fut.set_result(None)  # transfer the permit; held unchanged
            else:
                # Rare race: waiter already resolved/cancelled — keep the permit
                # moving rather than leaking it.
                self.release()
        else:
            self._held -= 1


class SrneConnectionManager:
    """Caps and fair-schedules BLE sessions shared across all SRNE packs.

    ``transport_factory``, ``device_resolver`` and ``clock`` are injectable so
    the manager can be unit-tested without a radio or Home Assistant. In
    production the resolver defers to ``async_ble_device_from_address``.
    """

    def __init__(
        self,
        hass,
        max_connections: int,
        *,
        transport_factory: Callable[[object], SrneBleTransport] = SrneBleTransport,
        device_resolver: Callable[[object, str], object] | None = None,
        clock: Callable[[], float] = time.monotonic,
        slot_wait_timeout: float = SLOT_WAIT_TIMEOUT,
    ) -> None:
        self._hass = hass
        self._sched = _FairScheduler(max_connections)
        self._transport_factory = transport_factory
        self._device_resolver = device_resolver
        self._clock = clock
        self._slot_wait_timeout = slot_wait_timeout
        # address -> monotonic time it last took a turn (fairness priority).
        self._last_served: dict[str, float] = {}
        # address -> phase ("connecting" | "open"), for diagnostics/observability.
        self._active: dict[str, str] = {}

    @property
    def active(self) -> dict[str, str]:
        """Snapshot of in-flight sessions (address -> phase)."""
        return dict(self._active)

    def _resolve(self, address: str):
        if self._device_resolver is not None:
            return self._device_resolver(self._hass, address)
        from homeassistant.components.bluetooth import async_ble_device_from_address

        return async_ble_device_from_address(self._hass, address, connectable=True)

    @asynccontextmanager
    async def session(self, address: str):
        """Yield a connected transport for ``address``, fair-queued and freed.

        Blocks (without holding a slot) until this pack wins its fair turn, up to
        ``slot_wait_timeout``. The slot is held for the whole session and always
        released; the transport is always disconnected even if connect raised or
        the body errored/was cancelled. Every turn — success or failure — updates
        this pack's fairness timestamp so the others get their turn next.
        """
        # Never-served packs sort first (key 0.0) so a newly-down pack is favoured.
        key = self._last_served.get(address, 0.0)
        try:
            await self._sched.acquire(key, self._slot_wait_timeout)
        except asyncio.TimeoutError as err:
            raise SrneBleError(
                f"{address}: no free BLE slot within {self._slot_wait_timeout:.0f}s"
            ) from err
        try:
            device = self._resolve(address)
            if device is None:
                raise SrneBleError(f"{address} not in range")
            transport = self._transport_factory(device)
            self._active[address] = "connecting"
            try:
                await transport.connect()
                self._active[address] = "open"
                yield transport
            finally:
                self._active.pop(address, None)
                await transport.disconnect()
        finally:
            self._last_served[address] = self._clock()
            self._sched.release()
