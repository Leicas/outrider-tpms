"""Config flow for the Outrider TPMS integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS

from .const import CONF_LOCAL_NAME, DOMAIN, LOCAL_NAME_FRONT, LOCAL_NAME_REAR


def _is_outrider(discovery: BluetoothServiceInfoBleak) -> bool:
    name = discovery.name or discovery.advertisement.local_name or ""
    return name.startswith("Outrider")


class OutriderTpmsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Outrider TPMS."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovery: BluetoothServiceInfoBleak | None = None
        self._discovered: dict[str, BluetoothServiceInfoBleak] = {}

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a Bluetooth discovery."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        if not _is_outrider(discovery_info):
            return self.async_abort(reason="not_supported")
        self._discovery = discovery_info
        self.context["title_placeholders"] = {"name": discovery_info.name or "Outrider"}
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm a Bluetooth-discovered Outrider."""
        assert self._discovery is not None
        if user_input is not None:
            return self._create_entry(self._discovery)

        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={
                "name": self._discovery.name or "Outrider",
                "address": self._discovery.address,
            },
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manual step — list currently discovered Outriders, let the user pick one."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            discovery = self._discovered[address]
            return self._create_entry(discovery)

        current_addresses = self._async_current_ids()
        self._discovered = {}
        for info in async_discovered_service_info(self.hass, connectable=True):
            if info.address in current_addresses:
                continue
            if _is_outrider(info):
                self._discovered[info.address] = info

        if not self._discovered:
            return self.async_abort(reason="no_devices_found")

        choices = {
            addr: f"{info.name or 'Outrider'} ({addr})"
            for addr, info in self._discovered.items()
        }
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_ADDRESS): vol.In(choices)}),
        )

    def _create_entry(self, discovery: BluetoothServiceInfoBleak) -> ConfigFlowResult:
        name = discovery.name or discovery.advertisement.local_name or "Outrider"
        title = self._title_for(name)
        return self.async_create_entry(
            title=title,
            data={
                CONF_ADDRESS: discovery.address,
                CONF_LOCAL_NAME: name,
            },
        )

    @staticmethod
    def _title_for(local_name: str) -> str:
        if local_name == LOCAL_NAME_FRONT:
            return "Outrider Front"
        if local_name == LOCAL_NAME_REAR:
            return "Outrider Rear"
        return local_name
