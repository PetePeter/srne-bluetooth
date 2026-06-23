"""Constants for the SRNE BLE integration."""
from __future__ import annotations

DOMAIN = "srne_ble"

CONF_ADDRESS = "address"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_SCAN_INTERVAL = 90  # seconds

# Ride out transient BLE poll failures before marking the device unavailable.
# Generous so a brief slot-starvation streak keeps the last value instead of
# flipping to "unavailable".
MAX_POLL_FAILURES = 8

# A BLE proxy has only a few connection slots and each pack allows one central
# at a time. Use all three proxy slots, but a failing session must release its
# slot fast (see transport fail-fast timeouts) so it can't starve the others.
MAX_CONCURRENT_CONNECTIONS = 3
SEMAPHORE_KEY = "_ble_semaphore"

# Advertised local-name prefix for the SRNE/Tuner168 packs.
NAME_PREFIX = "BAT1-"
