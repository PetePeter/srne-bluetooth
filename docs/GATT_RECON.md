# GATT Reconnaissance — SRNE/PowMr Battery BMS

**Date:** 2026-06-23
**Source:** Direct BLE connection from the Windows dev PC (built-in adapter)
**Method:** `bleak` scan + full GATT enumeration + characteristic reads + probe writes

> This supersedes the earlier HA-scanner draft. All handles, UUIDs and values
> below are **verified from a live, strong-signal connection** to a BAT1 unit
> (RSSI ~-74).

## Discovered Devices

A 10 s scan found these relevant devices (all advertising service `0000ffd0`
unless noted):

| Name | MAC (OUI shown) | RSSI | Adv service |
|------|-----------------|------|-------------|
| `BAT1-xxxxxxxx` | `D8:B6:73:xx:xx:xx` | -74 | `0000ffd0` |
| `BAT1-xxxxxxxx` | `84:C6:92:xx:xx:xx` | -74 | `0000ffd0` |
| `BAT1-xxxxxxxx` | `84:C6:92:xx:xx:xx` | -77 | `0000ffd0` |
| `BAT1-xxxxxxxx` | `84:C6:92:xx:xx:xx` | -76 | `0000ffd0` |
| `BAT1-xxxxxxxx` | `84:C6:92:xx:xx:xx` | -81 | `0000ffd0` |
| `D256xxxxxxxx` | `68:79:C4:xx:xx:xx` | -66 | `00000922` (Deye logger) |
| `0000PB40 … agl` | `38:3B:26:xx:xx:xx` | -86 | `0000af00` (PB40) |

(MAC suffixes masked.) Five `BAT1-*` BMS units (OUI `84:C6:92` / `D8:B6:73`) —
consistent with a multi-pack bank, each pack with its own BLE module. The
`D256…` device is the **Deye logger** (service `00000922`), a separate target
documented elsewhere.

## Verified GATT Layout (BAT1)

MTU negotiated to **251 bytes** — full frames fit in a single notification.

### Service `00001800` — Generic Access
| Handle | UUID | Props |
|--------|------|-------|
| `0x0002` | `2a00` Device Name | Read |
| `0x0004` | `2a01` Appearance | Read |
| `0x0006` | `2a04` Preferred Conn Params | Read |

### Service `0000180a` — Device Information
| Handle | UUID | Props | Value (live) |
|--------|------|-------|--------------|
| `0x0009` | `2a23` System ID | R/W | `00 00 00 00 00 00 00 00` |
| `0x000b` | `2a24` Model Number | Read | `TC,R2#4,1,248,S` |
| `0x000d` | `2a25` Serial Number | Read | `2019-11-5` |
| `0x000f` | `2a26` Firmware Rev | Read | `V1.1` |
| `0x0011` | `2a27` Hardware Rev | Read | device MAC (no separators) |
| `0x0013` | `2a28` Software Rev | Read | `V1.1` |
| `0x0015` | `2a29` Manufacturer | Read | `www.tuner168.com` |
| `0x0017` | `2a2a` IEEE Reg Cert | Read | — |
| `0x0019` | `2a50` PnP ID | Read | — |

### Service `0000ffd0` — Primary BMS Comms
| Handle | UUID | Props | Role |
|--------|------|-------|------|
| `0x001c` | `0000ffd1` | Write, Write-no-resp, Read | **Command write** |
| `0x001f` | `0000ffd2` | Notify, Read | **Response notifications** (CCCD `0x0020`) |
| `0x0023` | `0000ffd3` | Write | Unknown write-only channel |
| `0x0026` | `0000ffd4` | Read | Unknown read-only (returned empty) |
| `0x0029` | `0000ffd5` | Read, Write | Unknown bidirectional (returned empty) |

### Service `0000fff0` — Secondary
| Handle | UUID | Props |
|--------|------|-------|
| `0x002d` | `0000fff1` | Notify, Read |

### Service `f000ffd0-0451-4000-b000-000000000000` — Custom Vendor
| Handle | UUID | Props |
|--------|------|-------|
| `0x0032` | `f000ffd1-0451-4000-b000-000000000000` | Write, Write-no-resp |

## Decoded Device Information

| Field | Value | Notes |
|-------|-------|-------|
| Manufacturer | `www.tuner168.com` | Tuner168 — OEM for SRNE/PowMr BLE BMS |
| Model | `TC,R2#4,1,248,S` | Tuner168 model string |
| Mfg date (Serial) | `2019-11-5` | |
| FW / SW | `V1.1` | |
| HW Rev | device MAC | |

## Probe Results — SOLVED

Initial probes (JBD `0xDD`, naive Modbus addr `0x01`/`0xFF` without init,
direct `ffd4`/`ffd5` reads) all returned nothing. The breakthrough was:

1. **Notifications come on `fff1`, not `ffd2`.**
2. **An init/login write to `ffd1` is mandatory** before the module relays
   Modbus traffic — the app's string `$CheckPinCode$ ` (first bytes `24 43 68`).
3. **Framing is Modbus RTU, unit `0xFF`, func `0x03`, CRC-16/Modbus** — and the
   CRC must be present or the frame is silently dropped.

With that, the full realtime register map (`0x0300`) was read live and verified
against Home Assistant. **The protocol is fully documented in
[`PROTOCOL.md`](./PROTOCOL.md)** — that is now the authoritative reference.

The decompiled-app command list that unlocked this is in
`x:\homeassistant\SRNE_BATTERY_BLE_FINDINGS.md`.

## Next Steps

Reverse-engineering is complete. Remaining work is implementation: an ESP32/
ESPHome BLE-central poller near the battery bank (see `PROTOCOL.md` §9), and
bit-decoding the protection/alarm flag registers (`0x0309`–`0x030E`).
