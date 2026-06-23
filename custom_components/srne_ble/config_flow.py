"""Config flow for the SRNE BLE integration.

Supports automatic Bluetooth discovery of ``BAT1-*`` packs and a manual
fallback where the user picks from currently-discovered devices.
"""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import CONF_ADDRESS, DOMAIN, NAME_PREFIX


def _is_srne(info: BluetoothServiceInfoBleak) -> bool:
    return bool(info.name) and info.name.strip().startswith(NAME_PREFIX)


class SrneBleConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for SRNE BLE."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered: dict[str, str] = {}  # address -> label
        self._address: str | None = None

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a discovery from the Bluetooth integration."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self._address = discovery_info.address
        self.context["title_placeholders"] = {"name": discovery_info.name.strip()}
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm a single discovered device."""
        assert self._address is not None
        if user_input is not None:
            return self.async_create_entry(
                title=f"SRNE {self._address}",
                data={CONF_ADDRESS: self._address},
            )
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={"address": self._address},
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manual setup — choose from currently-discovered SRNE packs."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=f"SRNE {address}", data={CONF_ADDRESS: address}
            )

        current = self._async_current_ids()
        for info in async_discovered_service_info(self.hass):
            if _is_srne(info) and info.address not in current:
                self._discovered[info.address] = f"{info.name.strip()} ({info.address})"

        if not self._discovered:
            return self.async_abort(reason="no_devices_found")

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(CONF_ADDRESS): vol.In(self._discovered)}
            ),
        )
