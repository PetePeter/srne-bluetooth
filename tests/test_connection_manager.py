"""Lifecycle tests for SrneConnectionManager.

Proves the shared slot/permit is always freed — on success, body error, connect
error, a missing device, and under the concurrency cap. No mocks: an injected
fake transport + resolver exercise the real manager. This is the guard against
the leak that wedged packs 1 & 3 (a session that never released its slot).
"""
import asyncio

import pytest

from custom_components.srne_ble.connection_manager import SrneConnectionManager
from custom_components.srne_ble.transport import SrneBleError

_SENTINEL_DEVICE = object()


class FakeTransport:
    def __init__(self, device, *, connect_error=None):
        self.device = device
        self.connect_error = connect_error
        self.connected = False
        self.disconnected = False

    async def connect(self):
        if self.connect_error is not None:
            raise self.connect_error
        self.connected = True

    async def read_realtime(self):
        return [1, 2, 3]

    async def disconnect(self):
        self.disconnected = True


def _mgr(max_conn=1, *, device=_SENTINEL_DEVICE, connect_error=None, created=None):
    def factory(dev):
        t = FakeTransport(dev, connect_error=connect_error)
        if created is not None:
            created.append(t)
        return t

    return SrneConnectionManager(
        hass=None,
        max_connections=max_conn,
        transport_factory=factory,
        device_resolver=lambda _hass, _addr: device,
    )


def _run(coro):
    return asyncio.run(coro)


def test_session_yields_connected_transport_and_frees_everything():
    created = []
    m = _mgr(created=created)

    async def go():
        async with m.session("AA") as t:
            assert t.connected
            assert m.active == {"AA": "open"}
        return t

    t = _run(go())
    assert t.disconnected
    assert m.active == {}
    assert not m._sem.locked()  # permit returned


def test_session_frees_on_body_error():
    created = []
    m = _mgr(created=created)

    async def go():
        with pytest.raises(ValueError):
            async with m.session("AA"):
                raise ValueError("boom")

    _run(go())
    assert created[-1].disconnected
    assert m.active == {}
    assert not m._sem.locked()


def test_session_frees_on_connect_error():
    created = []
    m = _mgr(connect_error=SrneBleError("nope"), created=created)

    async def go():
        with pytest.raises(SrneBleError):
            async with m.session("AA"):
                pass

    _run(go())
    assert created[-1].disconnected  # disconnect still runs in finally
    assert m.active == {}
    assert not m._sem.locked()


def test_missing_device_raises_and_frees_without_building_transport():
    created = []
    m = _mgr(device=None, created=created)

    async def go():
        with pytest.raises(SrneBleError):
            async with m.session("AA"):
                pass

    _run(go())
    assert created == []  # no transport built
    assert m.active == {}
    assert not m._sem.locked()


def test_concurrency_is_capped_to_max_connections():
    m = _mgr(max_conn=1)
    gate = asyncio.Event()
    entered = []

    async def worker(tag):
        async with m.session(tag):
            entered.append(tag)
            await gate.wait()

    async def go():
        a = asyncio.create_task(worker("A"))
        await asyncio.sleep(0.02)
        b = asyncio.create_task(worker("B"))
        await asyncio.sleep(0.02)
        assert entered == ["A"]  # B blocked on the semaphore
        gate.set()
        await asyncio.gather(a, b)

    _run(go())
    assert entered == ["A", "B"]
    assert not m._sem.locked()
