"""SRNE Bluetooth (Local) integration.

HA imports are deferred to function bodies where practical so the package's pure
modules (``protocol``) can be imported without homeassistant for unit tests.
"""
from __future__ import annotations

import logging

from .const import (
    CONF_ADDRESS,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MANAGER_KEY,
    MAX_CONCURRENT_CONNECTIONS,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[str] = ["sensor"]


async def async_setup_entry(hass, entry) -> bool:
    """Set up SRNE BLE from a config entry."""
    from .connection_manager import SrneConnectionManager
    from .coordinator import SrneBleCoordinator

    address = entry.data[CONF_ADDRESS]
    scan_interval = entry.options.get(
        CONF_SCAN_INTERVAL, entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    )
    _LOGGER.info("Setting up SRNE BLE for %s", address)

    domain_data = hass.data.setdefault(DOMAIN, {})
    # One manager shared by every pack caps and lifecycles concurrent BLE
    # sessions (guaranteed slot/permit release, even on a hung disconnect).
    manager = domain_data.get(MANAGER_KEY)
    if manager is None:
        manager = SrneConnectionManager(hass, MAX_CONCURRENT_CONNECTIONS)
        domain_data[MANAGER_KEY] = manager

    coordinator = SrneBleCoordinator(
        hass, address=address, scan_interval=scan_interval, manager=manager
    )
    await coordinator.async_config_entry_first_refresh()

    domain_data[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass, entry) -> None:
    """Reload the entry when options (e.g. scan interval) change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass, entry) -> bool:
    """Unload an SRNE BLE config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
