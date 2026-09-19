"""Bluetooth discovery, manual setup and credential replacement."""

import re
from typing import Any

import voluptuous as vol
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS, CONF_NAME
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import CONF_LTMK, DEFAULT_NAME, DOMAIN, MIBEACON_UUID, MODEL, PRODUCT_ID
from .coordinator import CurtainCoordinator
from .protocol import UnsupportedMTUError


class LumiCurtainConfigFlow(ConfigFlow, domain=DOMAIN):
    """Validate credentials by logging in and reading the curtain."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered_address = ""

    async def async_step_bluetooth(
        self,
        discovery_info: BluetoothServiceInfoBleak,
    ) -> ConfigFlowResult:
        """Identify the curtain without connecting or requiring a local adapter."""
        if not discovery_info.connectable:
            return self.async_abort(reason="not_connectable")
        beacon = discovery_info.service_data.get(MIBEACON_UUID, b"")
        matches_product = (
            len(beacon) >= 5
            and beacon[1] >> 4 >= 2
            and int.from_bytes(beacon[2:4], "little") == PRODUCT_ID
        )
        if discovery_info.name != MODEL and not matches_product:
            return self.async_abort(reason="not_supported")
        self._discovered_address = discovery_info.address.upper()
        await self.async_set_unique_id(self._discovered_address)
        self._abort_if_unique_id_configured()
        self.context["title_placeholders"] = {
            "name": f"{DEFAULT_NAME} {self._discovered_address[-5:]}",
        }
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Ask for the LTMK before opening an active connection."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._abort_if_unique_id_configured()
            data = {
                CONF_ADDRESS: self._discovered_address,
                CONF_LTMK: user_input[CONF_LTMK].strip().lower(),
            }
            if error := await self._validate(data):
                errors["base"] = error
            else:
                return self.async_create_entry(
                    title=user_input.get(CONF_NAME, "").strip() or DEFAULT_NAME,
                    data=data,
                )
        return self.async_show_form(
            step_id="bluetooth_confirm",
            errors=errors,
            description_placeholders={"address": self._discovered_address},
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_LTMK): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                    vol.Optional(CONF_NAME, default=DEFAULT_NAME): str,
                }
            ),
        )

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            address = user_input[CONF_ADDRESS].strip().upper().replace("-", ":")
            if not re.fullmatch(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}", address):
                errors[CONF_ADDRESS] = "invalid_address"
            else:
                await self.async_set_unique_id(address)
                self._abort_if_unique_id_configured()
                data = {
                    CONF_ADDRESS: address,
                    CONF_LTMK: user_input[CONF_LTMK].strip().lower(),
                }
                error = await self._validate(data)
                if error:
                    errors["base"] = error
                else:
                    return self.async_create_entry(
                        title=user_input.get(CONF_NAME, "").strip() or DEFAULT_NAME,
                        data=data,
                    )
        return self.async_show_form(
            step_id="user",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): str,
                    vol.Required(CONF_LTMK): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                    vol.Optional(CONF_NAME, default=DEFAULT_NAME): str,
                }
            ),
        )

    async def _validate(self, data: dict[str, Any]) -> str | None:
        if not re.fullmatch(r"[0-9a-f]{64}", data[CONF_LTMK]):
            return "invalid_ltmk"
        coordinator = CurtainCoordinator(self.hass, data[CONF_ADDRESS], data[CONF_LTMK])
        try:
            await coordinator.async_refresh()
            if isinstance(coordinator.last_exception, ConfigEntryAuthFailed):
                return "invalid_auth"
            if coordinator.last_exception and isinstance(
                coordinator.last_exception.__cause__, UnsupportedMTUError
            ):
                return "unsupported_mtu"
            if not coordinator.last_update_success:
                return "cannot_connect"
        finally:
            await coordinator.async_shutdown()
        return None

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**entry.data, CONF_LTMK: user_input[CONF_LTMK].strip().lower()}
            error = await self._validate(data)
            if error:
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(entry, data_updates=data)
        return self.async_show_form(
            step_id="reauth_confirm",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_LTMK): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
        )
