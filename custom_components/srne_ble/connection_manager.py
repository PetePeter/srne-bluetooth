"""Shared BLE connection manager for the SRNE integration.

Owns the connect -> use -> release lifecycle for every pack so no coordinator
re-implements it. A single shared semaphore caps concurrent sessions across all
packs (a BLE proxy has only a few connection slots), and the ``session`` context
GUARANTEES the slot + permit are freed on every exit path — success, error,
cancellation, or a hung disconnect (teardown is timeout-bounded in the
transport). The ``active`` registry exposes what is in flight for diagnostics.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Callable

from .transport import SrneBleError, SrneBleTransport

_LOGGER = logging.getLogger(__name__)


class SrneConnectionManager:
    """Caps and lifecycles BLE sessions shared across all SRNE packs.

    ``transport_factory`` and ``device_resolver`` are injectable so the manager
    can be unit-tested without a radio or Home Assistant. In production the
    resolver defers to ``async_ble_device_from_address``.
    """

    def __init__(
        self,
        hass,
        max_connections: int,
        *,
        transport_factory: Callable[[object], SrneBleTransport] = SrneBleTransport,
        device_resolver: Callable[[object, str], object] | None = None,
    ) -> None:
        self._hass = hass
        self._sem = asyncio.Semaphore(max_connections)
        self._transport_factory = transport_factory
        self._device_resolver = device_resolver
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
        """Yield a connected transport for ``address``, freeing it on exit.

        The semaphore permit is held for the whole session and released when the
        block exits; the transport is always disconnected in ``finally`` even if
        connect raised or the body errored/was cancelled.
        """
        async with self._sem:
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
