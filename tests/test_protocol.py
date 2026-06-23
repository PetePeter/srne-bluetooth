"""Unit tests for the SRNE BLE wire protocol — real captured bytes, no mocks.

The golden realtime frame below is built from registers actually read off pack
``D8:B6:73:C2:80:1C`` and cross-checked against Home Assistant (see
``docs/PROTOCOL.md`` §5).
"""
from custom_components.srne_ble import protocol as p


# Registers 0x0300..0x0327 as captured live (current charging, SOC 58, etc.).
GOLDEN_REGS = [
    0xFB2C,  # 0x0300 current  -> -12.36 A (signed)
    0x1461,  # 0x0301 voltage  ->  52.17 V
    0x003A,  # 0x0302 SOC      ->  58 %
    0x0064,  # 0x0303 SOH      ->  100 %
    0x04B1,  # 0x0304 remaining-> 120.1 Ah
    0x0829,  # 0x0305 full     -> 208.9 Ah
    0x0829,  # 0x0306 rated    -> 208.9 Ah
    0x001F,  # 0x0307 cycles   -> 31
    0x0002, 0x0000, 0x0000, 0x0E00, 0x0000, 0x01D8, 0x0000,  # 0x0308..0x030E
    # 0x030F..0x031E — 16 cell voltages (mV)
    0x0CBE, 0x0CB9, 0x0CBA, 0x0CBB, 0x0CBD, 0x0CC0, 0x0CBC, 0x0CBF,
    0x0CBE, 0x0CBE, 0x0CBB, 0x0CBB, 0x0CBC, 0x0CBC, 0x0CC1, 0x0CBC,
    0x00A0,  # 0x031F reserved
    0x0096,  # 0x0320 temp_1   -> 15.0 °C
    0x00A0,  # 0x0321 temp_2   -> 16.0 °C
    0x00A0,  # 0x0322 temp_3   -> 16.0 °C
    0x0082,  # 0x0323 mos_temp -> 13.0 °C
    0x00AA,  # 0x0324 ambient  -> 17.0 °C
    0x0000, 0x0000, 0x0000,  # 0x0325..0x0327
]


def _frame(regs: list[int]) -> bytes:
    """Assemble a valid Modbus read-response frame for the given registers."""
    body = b"".join(r.to_bytes(2, "big") for r in regs)
    header = bytes([p.UNIT, p.FUNC_READ, len(body)])
    return header + body + p.crc16(header + body)


def test_crc16_known_vector():
    # Verified live: FF 03 001B 0001 -> CRC E1 D3 on the wire.
    assert p.crc16(bytes.fromhex("ff03001b0001")) == bytes.fromhex("e1d3")


def test_build_read_realtime_roundtrips_crc():
    frame = p.build_read(p.REALTIME_ADDR, p.REALTIME_COUNT)
    assert frame[:6] == bytes.fromhex("ff03030000 28".replace(" ", ""))
    assert p.crc16(frame[:-2]) == frame[-2:]


def test_parse_response_extracts_registers():
    assert p.parse_response(_frame(GOLDEN_REGS)) == GOLDEN_REGS


def test_parse_response_rejects_bad_crc():
    bad = bytearray(_frame(GOLDEN_REGS))
    bad[-1] ^= 0xFF
    try:
        p.parse_response(bytes(bad))
    except p.ProtocolError:
        return
    raise AssertionError("expected ProtocolError on bad CRC")


def test_parse_response_rejects_truncated():
    try:
        p.parse_response(b"\xff\x03")
    except p.ProtocolError:
        return
    raise AssertionError("expected ProtocolError on short frame")


def test_decode_realtime_matches_ha_verified_values():
    d = p.decode_realtime(GOLDEN_REGS)
    assert round(d["current"], 2) == -12.36
    assert round(d["voltage"], 2) == 52.17
    assert d["soc"] == 58
    assert d["soh"] == 100
    assert round(d["remaining_capacity"], 1) == 120.1
    assert round(d["rated_capacity"], 1) == 208.9
    assert d["cycles"] == 31
    assert len(d["cell_voltages"]) == 16
    assert d["cell_voltages"][0] == 3.262
    assert max(d["cell_voltages"]) == 3.265
    assert d["mos_temp"] == 13.0
    assert d["ambient_temp"] == 17.0


def test_decode_realtime_rejects_short_block():
    try:
        p.decode_realtime([0] * 10)
    except p.ProtocolError:
        return
    raise AssertionError("expected ProtocolError on short block")
