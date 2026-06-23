"""Bleak I/O layer for the SRNE BLE BMS.

The only module that touches the radio. Owns the GATT connection, enables
notifications on ``fff1``, performs the ``$CheckPinCode$ `` login, sends Modbus
read frames on ``ffd1``, and reassembles the notification reply. All byte logic
is delegated to the pure ``protocol`` module.

One BLE central per pack at a time.
"""
from __future__ import annotations

import asyncio
import logging

from bleak import BleakClient

from . import protocol as p

_LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10.0  # seconds to await a complete reply


class SrneBleError(Exception):
    """Connection lost, timed out, or a malformed reply."""


class SrneBleTransport:
    """A single read session over GATT.

    Usage:
        async with SrneBleTransport(ble_device) as t:
            words = await t.read_realtime()
    """

    def __init__(self, ble_device, timeout: float = DEFAULT_TIMEOUT):
        self._device = ble_device
        self._timeout = timeout
        self._client: BleakClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._buf = bytearray()
        self._reply: asyncio.Future[bytes] | None = None

    async def __aenter__(self) -> "SrneBleTransport":
        await self.connect()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.disconnect()

    async def connect(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._client = BleakClient(self._device)
        await self._client.connect()
        await self._client.start_notify(p.NOTIFY_CHAR, self._on_notify)
        # Mandatory module wake/login — without it the BMS never replies.
        await self._client.write_gatt_char(p.WRITE_CHAR, p.LOGIN, response=False)

    async def disconnect(self) -> None:
        if self._client is not None:
            try:
                await self._client.stop_notify(p.NOTIFY_CHAR)
            except Exception:  # noqa: BLE001 — best-effort on teardown
                pass
            await self._client.disconnect()
            self._client = None

    def _on_notify(self, _char, data: bytearray) -> None:
        if self._reply is None or self._reply.done():
            return
        self._buf += data
        # Notifications may fragment; resolve as soon as a full frame parses.
        try:
            p.parse_response(bytes(self._buf))
        except p.ProtocolError:
            return  # incomplete or not-yet-valid — keep accumulating
        self._reply.set_result(bytes(self._buf))

    async def read(self, address: int, count: int) -> list[int]:
        if self._client is None or self._loop is None:
            raise SrneBleError("not connected")
        self._buf = bytearray()
        self._reply = self._loop.create_future()
        await self._client.write_gatt_char(
            p.WRITE_CHAR, p.build_read(address, count), response=False
        )
        try:
            frame = await asyncio.wait_for(self._reply, self._timeout)
        except asyncio.TimeoutError as e:
            raise SrneBleError(f"no reply to read 0x{address:04X}") from e
        finally:
            self._reply = None
        try:
            return p.parse_response(frame)
        except p.ProtocolError as e:
            raise SrneBleError(str(e)) from e

    async def read_realtime(self) -> list[int]:
        return await self.read(p.REALTIME_ADDR, p.REALTIME_COUNT)
