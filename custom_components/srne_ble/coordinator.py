"""DataUpdateCoordinator for the SRNE BLE integration.

Polls the realtime block every cycle and decodes it. Pure byte logic lives in
``protocol``; the radio session lives in ``transport``. Transient BLE failures
are ridden out up to ``MAX_POLL_FAILURES`` before the entities go unavailable.
"""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import timedelta

from homeassistant.components.bluetooth import async_ble_device_from_address
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import protocol as p
from .const import DOMAIN, MAX_POLL_FAILURES
from .transport import SrneBleError, SrneBleTransport

_LOGGER = logging.getLogger(__name__)


class SrneBleCoordinator(DataUpdateCoordinator):
    """Polls one SRNE pack's realtime telemetry over BLE."""

    def __init__(
        self, hass, address: str, scan_interval: int, semaphore: asyncio.Semaphore
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )
        self._address = address
        self._consecutive_failures = 0
        # Shared across all packs — caps concurrent BLE sessions on the proxy.
        self._semaphore = semaphore
        # Desync this pack's first poll so the five don't stampede at startup.
        self._startup_jitter: float | None = random.uniform(0, scan_interval)

    @property
    def address(self) -> str:
        return self._address

    async def _async_update_data(self) -> dict:
        if self._startup_jitter:
            await asyncio.sleep(self._startup_jitter)
            self._startup_jitter = None
        try:
            async with self._semaphore:
                ble_device = async_ble_device_from_address(
                    self.hass, self._address, connectable=True
                )
                if ble_device is None:
                    raise SrneBleError(f"{self._address} not in range")
                async with SrneBleTransport(ble_device) as transport:
                    words = await transport.read_realtime()
            data = p.decode_realtime(words)
        except (SrneBleError, p.ProtocolError) as err:
            self._consecutive_failures += 1
            if self._consecutive_failures <= MAX_POLL_FAILURES and self.data:
                _LOGGER.debug(
                    "poll failed (%s/%s), keeping last values: %s",
                    self._consecutive_failures,
                    MAX_POLL_FAILURES,
                    err,
                )
                return self.data
            raise UpdateFailed(str(err)) from err
        self._consecutive_failures = 0
        return data
