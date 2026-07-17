"""Constants for the SRNE BLE integration."""
from __future__ import annotations

DOMAIN = "srne_ble"

CONF_ADDRESS = "address"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_SCAN_INTERVAL = 90  # seconds

# Ride out transient BLE poll failures before marking the device unavailable.
# Wide window: with five packs rotating through limited proxy slots, a pack can
# lose the connection race for several minutes. At 90s/cycle this keeps the last
# value for ~30 min before flagging unavailable (a genuinely dead pack still
# eventually shows up; transient contention gaps stay hidden).
MAX_POLL_FAILURES = 20

# A BLE proxy has only a few connection slots and each pack allows one central
# at a time. Use all three proxy slots, but a failing session must release its
# slot fast (see transport fail-fast timeouts) so it can't starve the others.
MAX_CONCURRENT_CONNECTIONS = 3
SEMAPHORE_KEY = "_ble_semaphore"

# Advertised local-name prefix for the SRNE/Tuner168 packs.
NAME_PREFIX = "BAT1-"
