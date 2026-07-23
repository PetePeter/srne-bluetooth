"""Constants for the SRNE BLE integration."""
from __future__ import annotations

DOMAIN = "srne_ble"

CONF_ADDRESS = "address"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_SCAN_INTERVAL = 90  # seconds

# Ride out transient BLE poll failures before marking the device unavailable.
# The availability grace window MUST cover the worst-case rotation: if the
# hardware locks up all but one proxy slot, five packs share that single slot
# fair-queued, so a pack may wait a few cycles for its turn. A pack merely
# waiting in the fair queue does NOT count as a failure (the poll just runs
# long and then succeeds), so it stays on its last value. Failures are only
# counted when the slot wait itself times out (SLOT_WAIT_TIMEOUT) or a read
# fails. At 90s/cycle this keeps the last value ~30 min before flagging
# unavailable — far longer than any rotation window, so a pack that is simply
# awaiting its turn never flaps to unavailable; only a genuinely dead pack does.
MAX_POLL_FAILURES = 20

# A BLE proxy has only a few connection slots and each pack allows one central
# at a time. Use all three proxy slots. The connection manager schedules access
# fair-queued (least-recently-served first), so even if the hardware locks some
# slots, the remaining slot(s) are shared by every pack in turn — nobody starves.
MAX_CONCURRENT_CONNECTIONS = 3

# How long a poll will wait in the fair queue for a slot before giving up. Sized
# to cover a worst-case full rotation (all-but-one slot locked, five packs
# serialized on one slot). A pack waiting up to this long stays available via the
# grace window above; only a timeout here counts as a (still-ridden-out) failure.
SLOT_WAIT_TIMEOUT = 180.0

MANAGER_KEY = "_ble_manager"

# Advertised local-name prefix for the SRNE/Tuner168 packs.
NAME_PREFIX = "BAT1-"
