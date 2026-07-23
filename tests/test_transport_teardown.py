"""Teardown-hardening tests for SrneBleTransport.

A hung or failing disconnect must never block the caller — a stuck teardown
would pin the proxy connection slot and the shared semaphore permit and starve
the other packs (the real 2026-07-24 pack-1/3 outage). No mocks: a small fake
BleakClient exercises the real disconnect() logic.
"""
import asyncio

from custom_components.srne_ble import transport as T
from custom_components.srne_ble.transport import SrneBleTransport


class FakeClient:
    def __init__(self, *, disconnect="ok", stop="ok"):
        self._d = disconnect
        self._s = stop
        self.stopped = False
        self.disconnected = False

    async def stop_notify(self, _char):
        if self._s == "raise":
            raise RuntimeError("stop boom")
        if self._s == "hang":
            await asyncio.Event().wait()
        self.stopped = True

    async def disconnect(self):
        if self._d == "raise":
            raise RuntimeError("disconnect boom")
        if self._d == "hang":
            await asyncio.Event().wait()
        self.disconnected = True


def _run(coro):
    return asyncio.run(coro)


def test_disconnect_happy_path_stops_notify_then_disconnects():
    t = SrneBleTransport(object())
    c = FakeClient()
    t._client = c
    _run(t.disconnect())
    assert c.stopped and c.disconnected
    assert t._client is None


def test_disconnect_is_idempotent_when_not_connected():
    t = SrneBleTransport(object())
    t._client = None
    _run(t.disconnect())  # must not raise


def test_disconnect_swallows_client_errors_and_clears_handle():
    t = SrneBleTransport(object())
    t._client = FakeClient(disconnect="raise", stop="raise")
    _run(t.disconnect())  # must not raise
    assert t._client is None


def test_disconnect_is_bounded_when_client_hangs(monkeypatch):
    # With a real hang, an unbounded disconnect would block forever. Bound it
    # tightly and prove the whole teardown returns well under a second.
    monkeypatch.setattr(T, "STOP_NOTIFY_TIMEOUT", 0.05)
    monkeypatch.setattr(T, "DISCONNECT_TIMEOUT", 0.05)
    t = SrneBleTransport(object())
    t._client = FakeClient(disconnect="hang", stop="hang")

    async def go():
        await asyncio.wait_for(t.disconnect(), 1.0)

    _run(go())
    assert t._client is None
