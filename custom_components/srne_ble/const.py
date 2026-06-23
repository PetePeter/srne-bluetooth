"""Constants for the SRNE BLE integration."""
from __future__ import annotations

DOMAIN = "srne_ble"

CONF_ADDRESS = "address"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_SCAN_INTERVAL = 60  # seconds

# Ride out transient BLE poll failures before marking the device unavailable.
MAX_POLL_FAILURES = 5

# Advertised local-name prefix for the SRNE/Tuner168 packs.
NAME_PREFIX = "BAT1-"
