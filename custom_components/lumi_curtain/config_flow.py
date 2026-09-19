"""Manual address + LTMK setup and credential replacement."""

import re
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS, CONF_NAME
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import CONF_LTMK, DOMAIN
from .coordinator import CurtainCoordinator


class LumiCurtainConfigFlow(ConfigFlow, domain=DOMAIN):
    """Validate credentials by logging in and reading the curtain."""

    VERSION = 1

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
                        title=user_input.get(CONF_NAME, "").strip() or "Lumi Curtain",
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
                    vol.Optional(CONF_NAME, default="Lumi Curtain"): str,
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
