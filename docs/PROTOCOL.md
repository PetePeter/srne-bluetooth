# SRNE / Tuner168 "FP-Bat" BLE Protocol — Authoritative Reference

**Status (2026-06-23): fully reverse-engineered and verified live.** The access
sequence, framing, and the realtime register map were captured from a live BAT1
pack (`D8:B6:73:C2:80:1C`) from a Windows PC using `bleak`, and every field's
scaling was **cross-checked against the same pack's values in Home Assistant**
(which integrates the packs over Tuya Local).

This supersedes all earlier hypotheses. The previously-assumed JBD `0xDD`
framing was **wrong**; the device speaks **Modbus-over-BLE**.

## 1. Device identity

| Property | Value |
|----------|-------|
| BLE advertised name | `BAT1-<MAC suffix>` (note: trailing spaces in the name) |
| Adv service | `0000ffd0-0000-1000-8000-00805f9b34fb` |
| Adv payload | `02 01 06 03 02 d0 ff 07 ff <mac bytes>` |
| GATT manufacturer | `www.tuner168.com` |
| GATT model | `TC,R2#4,1,248,S` |
| Modbus identity (reg 0x0010+) | model `FP-Bat`, type `LFP-B`, FW `1.1.6`, "Smart LiBattery Energy Storage" |
| BMS chip firmware | 天邦达 (Tianbangda) `V1.1.6` |
| Cells | 16S LiFePO4, ~207 Ah |

Only **one BLE central at a time** — the SRNE app and any other client cannot
both be connected to the same pack.

## 2. GATT transport (verified handles)

| Role | UUID | Handle | Properties |
|------|------|--------|------------|
| **TX** (write commands) | `0000ffd1` | `0x001c` | Write / Write-no-response |
| **RX** (notifications)  | `0000fff1` | `0x002d` | Notify (CCCD `0x002e`) |

Notifications are on **`fff1`** (service `fff0`), *not* `ffd2`. MTU negotiates
to 251, so a full 40-register response fits in a single notification.

Other characteristics exist (`ffd2`, `ffd3`, `ffd4`, `ffd5`, vendor
`f000ffd1`, OTA service `02f00000-…-fe00`) — not needed for telemetry. See
[`GATT_RECON.md`](./GATT_RECON.md) for the complete dump.

## 3. Connection & access sequence

```mermaid
sequenceDiagram
    participant H as Host (BLE central)
    participant B as BAT1 pack (Tuner168 module)
    H->>B: connect (LE), set MTU ~247
    H->>B: enable notify on fff1 (write CCCD 0x0100)
    H->>B: write "$CheckPinCode$ " to ffd1  (app login string)
    Note over H,B: identity/realtime reads work after the<br/>module is woken; no PIN needed on these packs
    H->>B: write Modbus read frame to ffd1
    B-->>H: notify on fff1: FF 03 <bytecount> <data…> <crc16>
```

- The app's login write is the ASCII string **`$CheckPinCode$ `** (note the
  trailing space). Its first three bytes are `24 43 68` (`$Ch`), which is what
  showed up as a "wake" in the truncated HCI snoop.
- On these packs no PIN is required; reads succeed after connect. If a pack
  requires a PIN, the app sends `$pincode$:<6-digit>` and watches for
  `ffff010108xx` status codes (see §7).
- The init/login write **is required** before the module relays Modbus traffic —
  without it the module is silent (this is why early probes failed).

## 4. Framing — Modbus RTU over BLE

Standard Modbus RTU carried as the GATT payload:

```
<unit> <func> <addr_hi> <addr_lo> <count_hi> <count_lo> <crc_lo> <crc_hi>
```

- **Unit / slave address = `0xFF`** (broadcast/common — the app calls this the
  `common` header type).
- **Function = `0x03`** (read holding registers). Function `0x04` is **not**
  supported.
- **CRC-16/Modbus**, appended low-byte-first. *Frames without a valid CRC are
  silently ignored.*
- Response: `FF 03 <bytecount> <data…> <crc_lo> <crc_hi>`, big-endian register
  values, `bytecount = 2 × registers`.
- **Read size is capped at ~40 registers (`0x28`).** Counts of `0x30`/`0x60`
  (the app's nominal `0x60`) time out on this firmware — read in `≤0x28` chunks.

### CRC-16/Modbus (reference)

```python
def crc16(frame: bytes) -> bytes:
    c = 0xFFFF
    for b in frame:
        c ^= b
        for _ in range(8):
            c = (c >> 1) ^ 0xA001 if c & 1 else c >> 1
    return bytes([c & 0xFF, (c >> 8) & 0xFF])   # low byte first
```

## 5. Realtime register map — block `0x0300`

Read `FF 03 0300 0028 <crc>` (40 registers). All scalings **verified against
Home Assistant** for the same pack.

| Reg | Field | Type | Scale / unit | Example | HA cross-check |
|-----|-------|------|--------------|---------|----------------|
| `0x0300` | Pack current | int16 (signed) | ×0.01 A, **negative = charging** | `0xFB2C` = −12.36 A | `-12.56 A` ✓ |
| `0x0301` | Pack voltage | uint16 | ×0.01 V | `0x1461` = 52.17 V | `52.15 V` ✓ |
| `0x0302` | SOC | uint16 | % | `58` | `57 %` ✓ |
| `0x0303` | SOH | uint16 | % | `100` | `100 %` ✓ |
| `0x0304` | Remaining capacity | uint16 | ×0.1 Ah | `0x04B1` = 120.1 Ah | `122.9 Ah` ✓ |
| `0x0305` | Full-charge capacity | uint16 | ×0.1 Ah | `0x0829` = 208.9 Ah | `207.0 Ah` ✓ |
| `0x0306` | Rated capacity | uint16 | ×0.1 Ah | `0x0829` = 208.9 Ah | `207.0 Ah` ✓ |
| `0x0307` | Cycle count | uint16 | cycles | `31` | — |
| `0x0308` | Temp-sensor count | uint16 | count | `2` | (5 values present) |
| `0x0309`–`0x030E` | Status/flags | uint16 | (see §6) | mostly `0` | — |
| `0x030F`–`0x031E` | **Cell 1–16 voltage** | uint16 | ×0.001 V | `0x0CBE` = 3.262 V | cells match exactly ✓ |
| `0x031F` | (reserved) | uint16 | — | `160` | — |
| `0x0320` | Cell/pack temp 1 | uint16 | ×0.1 °C | `150` = 15.0 °C | `battery_temperature 14.7` ≈ ✓ |
| `0x0321` | Cell/pack temp 2 | uint16 | ×0.1 °C | `160` = 16.0 °C | — |
| `0x0322` | Cell/pack temp 3 | uint16 | ×0.1 °C | `160` = 16.0 °C | — |
| `0x0323` | **MOSFET temp** | uint16 | ×0.1 °C | `130` = 13.0 °C | `mos_temperature 13.0` ✓ |
| `0x0324` | **Ambient temp** | uint16 | ×0.1 °C | `170` = 17.0 °C | `ambient_temperature 17.0` ✓ |
| `0x0325`+ | (zero / unused) | — | — | `0` | — |

> Registers `0x0309`–`0x030E` (charge/discharge MOSFET state, protection/alarm
> bitfields) are not yet bit-decoded — they read mostly `0` on a healthy idle
> pack. To map them, trigger a protection event or compare against HA's
> `binary_sensor` / problem-code entities.

## 6. Identity / config block (`0x0000`–`0x005F`)

Readable without login. ASCII strings, UTF-16-ish (`00`-spaced) in places.

| Read | Returns |
|------|---------|
| `FF 03 0010 0010` | model `FP-Bat` + config bytes |
| `FF 03 0020 0010` | firmware version `1.1.6` |
| `FF 03 0030 0020` | product string `Smart LiBattery Energy Storage` |
| `FF 03 000A 0010` | chemistry/type `LFP-B` |
| `FF 03 001B 0001` | protocol/spec word (`0x0D03`) |

## 7. App command reference (from `com.srne.androidapp` v9.1 decompile)

Captured in `x:\homeassistant\SRNE_BATTERY_BLE_FINDINGS.md`. Header/opcode/type
constants:

```
modbus header type:  common FF | blenet 55 | masterwifi fa | masterserial/ble f7
                     mfrealvalueset fb | other 01
opcode (func):       read 03 | write 06 | masterread/blenetreads 0a
device type:         bms 42 | li 40 | controller 00 | protect 06 | md 31 | mdsaa 34
```

Known command frames (append CRC):

| Frame | Purpose |
|-------|---------|
| `FF0303000060` | BMS realtime data (we read `0x0300` in ≤`0x28` chunks) |
| `FF0304000060` / `FF0304600060` | cell data blocks (time out on FP-Bat — cells are inline in `0x0300`) |
| `FF031000002A` | parallel/main average realtime |
| `FF032007006B` | BMS parameter data |
| `FF0300690014` | BMS43 page pre-read |
| `FF03001B0001` | protocol/spec |

PIN handshake (only if a pack demands it): write `$CheckPinCode$ `, then watch
notifications:

| Response substring | Meaning |
|--------------------|---------|
| `ffff01010800` | success / continue |
| `ffff01010801` | default pairing password `123456` |
| `ffff01010802` | pairing timeout |
| `ffff01010803` | PIN pairing failed |
| `ffff01010804` | custom PIN required → send `$pincode$:<pin>` |

PIN reset uses `FFFF03020A<6-digit-puk-hex><crc>`.

## 8. Minimal working client (verified)

```python
import asyncio
from bleak import BleakScanner, BleakClient

ADDR = "D8:B6:73:C2:80:1C"
FFD1 = "0000ffd1-0000-1000-8000-00805f9b34fb"  # TX
FFF1 = "0000fff1-0000-1000-8000-00805f9b34fb"  # RX (notify)

def crc16(f):
    c = 0xFFFF
    for b in f:
        c ^= b
        for _ in range(8):
            c = (c >> 1) ^ 0xA001 if c & 1 else c >> 1
    return bytes([c & 0xFF, (c >> 8) & 0xFF])

def read_cmd(addr, count):
    f = bytes([0xFF, 0x03, addr >> 8, addr & 0xFF, count >> 8, count & 0xFF])
    return f + crc16(f)

async def main():
    dev = await BleakScanner.find_device_by_address(ADDR, timeout=15)
    buf = []
    async with BleakClient(dev) as c:
        await c.start_notify(FFF1, lambda _h, d: buf.append(bytes(d)))
        await c.write_gatt_char(FFD1, b"$CheckPinCode$ ", response=False)
        await asyncio.sleep(1)
        buf.clear()
        await c.write_gatt_char(FFD1, read_cmd(0x0300, 0x28), response=False)
        await asyncio.sleep(2)
        resp = b"".join(buf)            # FF 03 <len> <data> <crc>
        data = resp[3:3 + resp[2]]
        i16 = lambda p, s=False: int.from_bytes(data[p:p+2], "big", signed=s)
        print("current", i16(0, True) / 100, "A")
        print("voltage", i16(2) / 100, "V")     # reg 0x0301 → byte offset 2
        print("SOC", i16(4), "%")                # reg 0x0302
        print("cells", [i16(30 + 2*n) / 1000 for n in range(16)])  # reg 0x030F

asyncio.run(main())
```

## 9. Next step — ESP32 / ESPHome

The packs are local-only BLE (no cloud) and out of range of the main HA host,
so the plan is an **ESP32 acting as the BLE central** near the battery bank
(custom ESPHome `ble_client` component or external component) that:

1. connects to each `BAT1-*` pack,
2. enables notify on `fff1`, writes `$CheckPinCode$ `,
3. polls `FF 03 0300 0028 <crc>` on a timer,
4. decodes the §5 map and publishes sensors to HA over the native API.

This removes the dependency on Tuya Local / cloud for battery telemetry. The
register map and framing in this document are everything the firmware needs.
