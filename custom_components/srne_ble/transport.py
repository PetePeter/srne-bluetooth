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
from bleak_retry_connector import establish_connection

from . import protocol as p

_LOGGER = logging.getLogger(__name__)

# Fail fast: a slow/failing session must release its shared connection slot
# quickly so it can't starve the other packs on the same proxy.
CONNECT_ATTEMPTS = 3     # establish_connection retries transient proxy failures
DEFAULT_TIMEOUT = 8.0    # seconds to await a complete reply
LOGIN_SETTLE = 1.0       # let the module process the login before the first read
READ_RETRIES = 1         # single attempt — give up and free the slot
# Teardown must never hang: a gone pack or a slow proxy can make stop_notify /
# disconnect block forever, which would pin BOTH the proxy connection slot AND
# the shared semaphore permit and starve the other packs. Bound every teardown
# radio call.
STOP_NOTIFY_TIMEOUT = 5.0
DISCONNECT_TIMEOUT = 10.0


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
        # For establish_connection logging; BLEDevice exposes name/address.
        self._name = getattr(ble_device, "name", None) or getattr(
            ble_device, "address", "srne"
        )
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
        # Use bleak-retry-connector — the resilient path HA expects. It routes
        # through the ESP32 proxies, retries transient failures, and handles the
        # reconnect churn that raw BleakClient.connect() does not.
        try:
            self._client = await establish_connection(
                BleakClient,
                self._device,
                self._name,
                max_attempts=CONNECT_ATTEMPTS,
            )
        except Exception as e:  # noqa: BLE001 — connect can raise many bleak errors
            raise SrneBleError(f"connect failed: {e}") from e
        # start_notify + login can raise raw bleak errors. The GATT connection is
        # already open at this point, so on failure we MUST release it — the BMS
        # accepts a single central and stops advertising while held, so a leaked
        # connection wedges this pack (and eats a proxy slot) for minutes. Also
        # re-raise as SrneBleError so the coordinator's grace window rides it out
        # instead of flipping the pack unavailable on one bad poll.
        try:
            await self._client.start_notify(p.NOTIFY_CHAR, self._on_notify)
            # Mandatory module wake/login — without it the BMS never replies. Give
            # it a moment to process before the first read (the proxy link is slow).
            await self._client.write_gatt_char(p.WRITE_CHAR, p.LOGIN, response=False)
            await asyncio.sleep(LOGIN_SETTLE)
        except Exception as e:  # noqa: BLE001 — notify/login can raise many bleak errors
            await self.disconnect()
            raise SrneBleError(f"login failed: {e}") from e

    async def disconnect(self) -> None:
        # Null the handle first so this is idempotent and re-entrant safe, then
        # release the link defensively. Every radio call is timeout-bounded and
        # shielded: a hung teardown must not block the caller (which would keep
        # the proxy slot + semaphore permit held and starve the other packs);
        # shielding lets the disconnect still finish in the background if the
        # surrounding poll is cancelled, rather than abandoning a half-open link.
        client, self._client = self._client, None
        if client is None:
            return
        for coro, timeout in (
            (client.stop_notify(p.NOTIFY_CHAR), STOP_NOTIFY_TIMEOUT),
            (client.disconnect(), DISCONNECT_TIMEOUT),
        ):
            try:
                await asyncio.wait_for(asyncio.shield(coro), timeout)
            except Exception:  # noqa: BLE001 — teardown is best-effort, never raises
                pass

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
