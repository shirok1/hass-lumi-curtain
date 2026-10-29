"""Bluetooth discovery, manual setup and credential replacement."""

import asyncio
import re
from html import escape
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from . import xiaomi_cloud as cloud
from .const import CONF_LTMK, DEFAULT_NAME, DOMAIN, MIBEACON_UUID, MODEL, PRODUCT_ID
from .coordinator import CurtainCoordinator
from .protocol import UnsupportedMTUError

LTMK_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_LTMK): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)


class LumiCurtainConfigFlow(ConfigFlow, domain=DOMAIN):
    """Validate credentials by logging in and reading the curtain."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered_address = ""
        self._region = "cn"
        self._cloud_name = DEFAULT_NAME
        self._cloud_session: aiohttp.ClientSession | None = None
        self._cloud_task: asyncio.Task | None = None
        self._qr: cloud.QRLogin | None = None
        self._auth: cloud.CloudAuth | None = None
        self._devices: dict[str, dict[str, Any]] = {}
        self._cloud_data: dict[str, str] = {}
        self._cloud_error = "cloud_failed"

    async def async_step_bluetooth(
        self,
        discovery_info: BluetoothServiceInfoBleak,
    ) -> ConfigFlowResult:
        """Identify the curtain without connecting or requiring a local adapter."""
        if not discovery_info.connectable:
            return self.async_abort(reason="not_connectable")
        beacon = discovery_info.service_data.get(MIBEACON_UUID)
        if beacon is not None:
            if (
                len(beacon) < 5
                or beacon[1] >> 4 < 2
                or int.from_bytes(beacon[2:4], "little") != PRODUCT_ID
            ):
                return self.async_abort(reason="not_supported")
            # A relayed frame must not identify its broadcaster as the curtain.
            if beacon[0] & 0x10 and (
                len(beacon) < 11
                or beacon[5:11][::-1].hex()
                != discovery_info.address.replace(":", "").lower()
            ):
                return self.async_abort(reason="not_supported")
        elif discovery_info.name != MODEL:
            return self.async_abort(reason="not_supported")
        self._discovered_address = discovery_info.address.upper()
        await self.async_set_unique_id(self._discovered_address)
        self._abort_if_unique_id_configured()
        suffix = self._discovered_address.replace(":", "")[-6:]
        self.context["title_placeholders"] = {
            "name": f"{DEFAULT_NAME} {suffix}",
            "suffix": suffix,
        }
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_manual(
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
                    title=DEFAULT_NAME,
                    data=data,
                )
        return self.async_show_form(
            step_id="bluetooth_manual",
            last_step=True,
            errors=errors,
            data_schema=self.add_suggested_values_to_schema(
                LTMK_SCHEMA,
                user_input,
            ),
        )

    async def async_step_manual(
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
                        title=DEFAULT_NAME,
                        data=data,
                    )
        return self.async_show_form(
            step_id="manual",
            last_step=True,
            errors=errors,
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema({vol.Required(CONF_ADDRESS): str}).extend(
                    LTMK_SCHEMA.schema
                ),
                user_input,
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
        entry = self._get_reauth_entry()
        self.context["title_placeholders"] = {
            **self.context.get("title_placeholders", {}),
            "suffix": entry.data[CONF_ADDRESS].replace(":", "")[-6:],
        }
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_manual(
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
            step_id="reauth_manual",
            last_step=True,
            errors=errors,
            data_schema=self.add_suggested_values_to_schema(
                LTMK_SCHEMA,
                user_input,
            ),
        )

    async def async_step_user(self, user_input=None) -> ConfigFlowResult:
        """Choose automatic cloud import or manual credentials."""
        return self.async_show_menu(step_id="user", menu_options=["cloud", "manual"])

    async def async_step_bluetooth_confirm(self, user_input=None) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="bluetooth_confirm",
            menu_options=["cloud", "bluetooth_manual"],
        )

    async def async_step_reauth_confirm(self, user_input=None) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="reauth_confirm", menu_options=["cloud", "reauth_manual"]
        )

    async def async_step_cloud(self, user_input=None) -> ConfigFlowResult:
        """Start a fresh, private account session for this import only."""
        self._clear_cloud()
        self._cloud_data = {}
        if user_input is not None:
            self._region = user_input["region"]
            self._cloud_session = async_create_clientsession(
                self.hass, auto_cleanup=False, cookie_jar=aiohttp.CookieJar()
            )
            session = self._cloud_session
            try:
                qr = await cloud.start_qr_login(session)
            except cloud.CloudError as err:
                self._cloud_error = err.reason
                self._clear_cloud()
                return await self.async_step_cloud_error()
            if self._cloud_session is not session:
                return self.async_abort(reason="cloud_cancelled")
            self._qr = qr
            return await self.async_step_cloud_qr()
        return self.async_show_form(
            step_id="cloud",
            data_schema=vol.Schema(
                {
                    vol.Required("region", default=self._region): SelectSelector(
                        SelectSelectorConfig(
                            options=list(cloud.SERVERS),
                            translation_key="region",
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
            last_step=False,
        )

    async def _cloud_login(self) -> None:
        assert self._cloud_session is not None and self._qr is not None
        self._auth = await cloud.wait_for_qr_scan(self._cloud_session, self._qr)
        devices = await cloud.list_devices(
            self._cloud_session, self._auth, self._region
        )
        target = self._discovered_address
        if self.source == "reauth":
            target = self._get_reauth_entry().data[CONF_ADDRESS]
        configured = {entry.unique_id for entry in self._async_current_entries()}
        self._devices = {}
        for device in devices:
            address = str(device.get("mac", "")).upper().replace("-", ":")
            did = device.get("did")
            if (
                device.get("model") == MODEL
                and isinstance(did, str)
                and did
                and re.fullmatch(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}", address)
                and (not target or target == address)
                and (self.source == "reauth" or address not in configured)
            ):
                self._devices[did] = {**device, "mac": address}

    async def async_step_cloud_qr(self, user_input=None) -> ConfigFlowResult:
        """Let HA monitor and cancel the bounded QR login task."""
        assert self._qr is not None
        if self._cloud_task is None:
            # Always show progress before a fast task can finish and change steps.
            self._cloud_task = self.hass.async_create_task(
                self._cloud_login(), eager_start=False
            )
        if not self._cloud_task.done():
            login_url = escape(self._qr.login_url or self._qr.qr_image_url, quote=True)
            return self.async_show_progress(
                step_id="cloud_qr",
                progress_action="cloud_login",
                description_placeholders={
                    "link_start": (
                        f'<a href="{login_url}" '
                        'target="_blank" rel="noopener noreferrer">'
                    ),
                    "link_end": "</a>",
                },
                progress_task=self._cloud_task,
            )
        try:
            self._cloud_task.result()
        except (cloud.CloudError, asyncio.CancelledError) as err:
            self._cloud_error = (
                err.reason if isinstance(err, cloud.CloudError) else "cloud_failed"
            )
            self._clear_cloud()
            return self.async_show_progress_done(next_step_id="cloud_error")
        if not self._devices:
            self._cloud_error = "cloud_no_devices"
            self._clear_cloud()
            return self.async_show_progress_done(next_step_id="cloud_error")
        return self.async_show_progress_done(next_step_id="cloud_device")

    async def async_step_cloud_device(self, user_input=None) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            device = self._devices[user_input["device"]]
            address = device["mac"]
            await self.async_set_unique_id(address)
            if self.source != "reauth":
                self._abort_if_unique_id_configured()
            assert self._cloud_session is not None and self._auth is not None
            try:
                key = await cloud.get_ltmk(
                    self._cloud_session, self._auth, self._region, device["did"]
                )
            except cloud.CloudError as err:
                if err.reason == "cloud_auth":
                    self._cloud_error = err.reason
                    self._clear_cloud()
                    return await self.async_step_cloud_error()
                errors["base"] = err.reason
            else:
                if self._cloud_session is None:
                    return self.async_abort(reason="cloud_cancelled")
                self._cloud_name = device.get("name") or DEFAULT_NAME
                self._cloud_data = {CONF_ADDRESS: address, CONF_LTMK: key}
                self._clear_cloud()
                return await self.async_step_cloud_confirm()
        return self.async_show_form(
            step_id="cloud_device",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Required("device"): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                {
                                    "value": did,
                                    "label": (
                                        f"{device.get('name', DEFAULT_NAME)} "
                                        f"({device['mac']})"
                                    ),
                                }
                                for did, device in self._devices.items()
                            ],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
            last_step=False,
        )

    async def async_step_cloud_confirm(self, user_input=None) -> ConfigFlowResult:
        """Pause for Mi Home to release BLE before validating locally."""
        errors = {}
        if user_input is not None:
            if self.source != "reauth":
                self._abort_if_unique_id_configured()
            if error := await self._validate(self._cloud_data):
                errors["base"] = error
            elif self.source == "reauth":
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(), data_updates=dict(self._cloud_data)
                )
            else:
                return self.async_create_entry(
                    title=DEFAULT_NAME, data=dict(self._cloud_data)
                )
        return self.async_show_form(
            step_id="cloud_confirm",
            data_schema=vol.Schema({}),
            errors=errors,
            description_placeholders={
                "name": self._cloud_name,
                "address": self._cloud_data[CONF_ADDRESS],
            },
            last_step=True,
        )

    async def async_step_cloud_error(self, user_input=None) -> ConfigFlowResult:
        if user_input is not None:
            self._cloud_error = "cloud_failed"
            return await self.async_step_cloud()
        return self.async_show_form(
            step_id="cloud_error",
            data_schema=vol.Schema({}),
            errors={"base": self._cloud_error},
            last_step=False,
        )

    @callback
    def _clear_cloud(self) -> None:
        if self._cloud_task is not None:
            self._cloud_task.cancel()
            self._cloud_task.add_done_callback(_consume_result)
        self._cloud_task = None
        self._auth = None
        self._qr = None
        self._devices = {}
        if self._cloud_session is not None:
            self._cloud_session.cookie_jar.clear()
            self._cloud_session.detach()
            self._cloud_session = None

    @callback
    def async_remove(self) -> None:
        self._clear_cloud()
        self._cloud_data = {}
        super().async_remove()


def _consume_result(task: asyncio.Task) -> None:
    if not task.cancelled():
        task.exception()
