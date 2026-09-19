"""HA-owned Bluetooth connection and push-first curtain state."""

import asyncio
import logging
from datetime import timedelta

import bleak_retry_connector
from bleak import BleakClient
from bleak.exc import BleakError
from bleak_retry_connector import establish_connection
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, POSITION, READ_PROPERTIES, STATUS
from .protocol import (
    AuthenticationError,
    Properties,
    ProtocolError,
    get_payload,
    parse_properties,
    set_payload,
)
from .transport import Session

_LOGGER = logging.getLogger(__name__)
IDLE_INTERVAL = timedelta(seconds=30)
MOVING_INTERVAL = timedelta(seconds=2)


class CurtainCoordinator(DataUpdateCoordinator[Properties]):
    """Serialize connect/read/write and retain a single live BLE session."""

    def __init__(
        self,
        hass: HomeAssistant,
        address: str,
        ltmk: str,
        entry: ConfigEntry | None = None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=IDLE_INTERVAL,
        )
        self.address = address
        self._ltmk = bytes.fromhex(ltmk)
        self._client: BleakClient | None = None
        self._session: Session | None = None
        self._lock = asyncio.Lock()
        self._ready = False
        self._stopping = False
        self._values: Properties = {}

    @callback
    def _disconnected(self, client: BleakClient) -> None:
        self.hass.loop.call_soon_threadsafe(self._handle_disconnect, client)

    @callback
    def _handle_disconnect(self, client: BleakClient) -> None:
        if client is self._client and self._session:
            self._session.fail(BleakError("Curtain disconnected"))

    @callback
    def _session_error(self, error: Exception) -> None:
        self._ready = False
        self.update_interval = IDLE_INTERVAL
        self.async_set_update_error(UpdateFailed(str(error)))

    @callback
    def _push(self, values: Properties) -> None:
        if self._stopping:
            return
        self._merge(values)
        if self._ready:
            self.async_set_updated_data(dict(self._values))

    def _merge(self, values: Properties) -> None:
        for key, value in values.items():
            if (key == POSITION and 0 <= value <= 100) or (
                key == STATUS and value in (0, 1, 2)
            ):
                self._values[key] = value
        self.update_interval = (
            MOVING_INTERVAL if self._values.get(STATUS) in (1, 2) else IDLE_INTERVAL
        )

    async def _connect(self) -> Session:
        if self._stopping:
            raise ProtocolError("Integration is unloading")
        if (
            self._session
            and self._session.is_ready
            and self._client
            and self._client.is_connected
            and not self._session.needs_rekey
        ):
            return self._session
        await self._disconnect()
        device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if device is None:
            raise BleakError("Curtain is not advertising on a connectable adapter")
        self._client = await establish_connection(
            # Resolve after HA Bluetooth setup installs its proxy-aware client.
            bleak_retry_connector.BleakClientWithServiceCache,
            device,
            self.address,
            disconnected_callback=self._disconnected,
            ble_device_callback=lambda: (
                bluetooth.async_ble_device_from_address(
                    self.hass, self.address, connectable=True
                )
                or device
            ),
        )
        self._session = Session(self._client, self._push, self._session_error)
        async with asyncio.timeout(45):
            await self._session.login(self._ltmk)
        self._values = {}
        return self._session

    async def _disconnect(self) -> None:
        self._ready = False
        session, self._session = self._session, None
        client, self._client = self._client, None
        if session:
            # Intentional teardown must not publish an availability error.
            session.on_error = lambda _error: None
            await session.close()
        if client:
            try:
                await client.disconnect()
            except BleakError:
                _LOGGER.debug("BLE disconnect failed", exc_info=True)

    async def _async_update_data(self) -> Properties:
        async with self._lock:
            try:
                session = await self._connect()
                response = await session.request(2, get_payload(READ_PROPERTIES))
                values = parse_properties(response)
                if not all(key in values for key in READ_PROPERTIES):
                    raise ProtocolError("Missing curtain state properties")
                if not 0 <= values[POSITION] <= 100 or values[STATUS] not in (0, 1, 2):
                    raise ProtocolError("Invalid curtain state")
                self._ready = True
                return dict(self._values)
            except AuthenticationError as err:
                await self._disconnect()
                raise ConfigEntryAuthFailed("Device rejected LTMK") from err
            except (ProtocolError, BleakError, TimeoutError) as err:
                await self._disconnect()
                self.update_interval = IDLE_INTERVAL
                raise UpdateFailed(str(err)) from err
            except asyncio.CancelledError:
                await self._disconnect()
                raise

    async def async_command(self, piid: int, value: int) -> None:
        """Send once and require a property ACK, without optimistic position."""
        payload = set_payload(piid, value)
        async with self._lock:
            try:
                session = await self._connect()
                response = parse_properties(await session.request(0, payload))
                if (2, piid) not in response:
                    raise ProtocolError("Missing command acknowledgement")
            except AuthenticationError as err:
                await self._disconnect()
                if self.config_entry:
                    self.config_entry.async_start_reauth(self.hass)
                raise HomeAssistantError("Device rejected LTMK") from err
            except (ProtocolError, BleakError, TimeoutError) as err:
                self._session_error(err)
                await self._disconnect()
                raise HomeAssistantError(
                    "Curtain command was not confirmed; its outcome may be unknown"
                ) from err
            except asyncio.CancelledError:
                await self._disconnect()
                raise
        # Poll in two seconds until the first read establishes the actual state.
        self.update_interval = MOVING_INTERVAL
        await self.async_request_refresh()

    async def async_shutdown(self) -> None:
        """Stop HA refresh timers and release the BLE connection."""
        self._stopping = True
        await super().async_shutdown()
        async with self._lock:
            await self._disconnect()
