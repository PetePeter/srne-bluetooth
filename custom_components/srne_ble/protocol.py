"""Pure wire protocol for the SRNE / Tuner168 "FP-Bat" BLE BMS.

No homeassistant or bleak imports — this module is the byte logic only, so it
can be unit-tested without a radio or HA. See ``docs/PROTOCOL.md`` for the
reverse-engineered reference this implements.

Transport summary:
  - login: write the ASCII string ``$CheckPinCode$ `` to ``ffd1`` once.
  - read:  write Modbus ``FF 03 <addr> <count> <crc16>`` to ``ffd1``.
  - reply: notification on ``fff1`` = ``FF 03 <bytecount> <data…> <crc16>``.
"""
from __future__ import annotations

# --- GATT / transport constants ---------------------------------------------

WRITE_CHAR = "0000ffd1-0000-1000-8000-00805f9b34fb"   # TX (write-no-response)
NOTIFY_CHAR = "0000fff1-0000-1000-8000-00805f9b34fb"  # RX (notify)
SERVICE_UUID = "0000ffd0-0000-1000-8000-00805f9b34fb"

LOGIN = b"$CheckPinCode$ "  # trailing space is required

UNIT = 0xFF          # Modbus slave/unit address
FUNC_READ = 0x03     # read holding registers

# Realtime telemetry block. Read in one shot (40 regs fits one notification and
# is within the firmware's ~0x28 read-size cap).
REALTIME_ADDR = 0x0300
REALTIME_COUNT = 0x28

NUM_CELLS = 16


class ProtocolError(Exception):
    """The reply was malformed, truncated, or failed its CRC check."""


def crc16(frame: bytes) -> bytes:
    """CRC-16/Modbus, returned low byte first (as it goes on the wire)."""
    crc = 0xFFFF
    for byte in frame:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return bytes([crc & 0xFF, (crc >> 8) & 0xFF])


def build_read(address: int, count: int, unit: int = UNIT) -> bytes:
    """Build a Modbus read-holding-registers frame with CRC appended."""
    frame = bytes(
        [unit, FUNC_READ, address >> 8, address & 0xFF, count >> 8, count & 0xFF]
    )
    return frame + crc16(frame)


def parse_response(frame: bytes, *, unit: int = UNIT) -> list[int]:
    """Validate a Modbus read response and return its register words.

    Raises ``ProtocolError`` on any framing, length, or CRC problem.
    """
    if len(frame) < 5:
        raise ProtocolError(f"reply too short: {frame.hex()}")
    if frame[0] != unit or frame[1] != FUNC_READ:
        raise ProtocolError(f"unexpected header: {frame[:2].hex()}")
    byte_count = frame[2]
    expected = 3 + byte_count + 2  # header(3) + data + crc(2)
    if len(frame) != expected:
        raise ProtocolError(
            f"length {len(frame)} != expected {expected} (byte_count={byte_count})"
        )
    if crc16(frame[:-2]) != frame[-2:]:
        raise ProtocolError(f"bad CRC: {frame.hex()}")
    data = frame[3 : 3 + byte_count]
    return [int.from_bytes(data[i : i + 2], "big") for i in range(0, len(data), 2)]


def _s16(value: int) -> int:
    """Interpret an unsigned 16-bit register as signed."""
    return value - 0x10000 if value >= 0x8000 else value


def decode_realtime(words: list[int]) -> dict:
    """Decode the realtime block (registers starting at ``0x0300``).

    ``words[i]`` is register ``0x0300 + i``. Scalings are verified against Home
    Assistant in ``docs/PROTOCOL.md`` §5.
    """
    if len(words) < 37:  # need through reg 0x0324 (index 36)
        raise ProtocolError(f"realtime block too short: {len(words)} regs")
    return {
        "current": _s16(words[0]) * 0.01,        # A, negative = charging
        "voltage": words[1] * 0.01,              # V
        "soc": words[2],                         # %
        "soh": words[3],                         # %
        "remaining_capacity": words[4] * 0.1,    # Ah
        "full_capacity": words[5] * 0.1,         # Ah
        "rated_capacity": words[6] * 0.1,        # Ah
        "cycles": words[7],                      # count
        "cell_voltages": [words[15 + i] / 1000 for i in range(NUM_CELLS)],  # V
        "temp_1": words[0x20] * 0.1,             # °C
        "temp_2": words[0x21] * 0.1,             # °C
        "temp_3": words[0x22] * 0.1,             # °C
        "mos_temp": words[0x23] * 0.1,           # °C
        "ambient_temp": words[0x24] * 0.1,       # °C
    }
