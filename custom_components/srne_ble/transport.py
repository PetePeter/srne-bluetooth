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

# Fail fast: a slow/failing session must release its shared connection slot
# quickly so it can't starve the other packs on the same proxy.
CONNECT_TIMEOUT = 8.0    # seconds to establish the GATT connection
DEFAULT_TIMEOUT = 5.0    # seconds to await a complete reply
LOGIN_SETTLE = 1.0       # let the module process the login before the first read
READ_RETRIES = 1         # single attempt — give up and free the slot


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
        self._client = BleakClient(self._device, timeout=CONNECT_TIMEOUT)
        try:
            await asyncio.wait_for(self._client.connect(), CONNECT_TIMEOUT)
        except Exception as e:  # noqa: BLE001 — connect can raise many bleak errors
            raise SrneBleError(f"connect failed: {e}") from e
        await self._client.start_notify(p.NOTIFY_CHAR, self._on_notify)
        # Mandatory module wake/login — without it the BMS never replies. Give
        # it a moment to process before the first read (the proxy link is slow).
        await self._client.write_gatt_char(p.WRITE_CHAR, p.LOGIN, response=False)
        await asyncio.sleep(LOGIN_SETTLE)

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
        frame = p.build_read(address, count)
        last_err: Exception | None = None
        for _ in range(READ_RETRIES):
            self._buf = bytearray()
            self._reply = self._loop.create_future()
            await self._client.write_gatt_char(p.WRITE_CHAR, frame, response=False)
            try:
                reply = await asyncio.wait_for(self._reply, self._timeout)
                return p.parse_response(reply)
            except (asyncio.TimeoutError, p.ProtocolError) as e:
                last_err = e
            finally:
                self._reply = None
        raise SrneBleError(f"read 0x{address:04X} failed: {last_err}")

    async def read_realtime(self) -> list[int]:
        return await self.read(p.REALTIME_ADDR, p.REALTIME_COUNT)
