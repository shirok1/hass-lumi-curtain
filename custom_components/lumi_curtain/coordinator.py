"""HA-owned Bluetooth connection and push-first curtain state."""

import asyncio
import logging
from asyncio import sleep
from collections.abc import Callable
from contextlib import suppress
from datetime import timedelta

import bleak_retry_connector
from bleak import BleakClient
from bleak.exc import BleakError
from bleak_retry_connector import establish_connection
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN
from .protocol import (
    AuthenticationError,
    Event,
    Properties,
    PropertyValues,
    ProtocolError,
    UnsupportedMTUError,
    action_payload,
    get_payload,
    parse_action_result,
    parse_properties,
    set_payload,
)
from .spec import (
    DEVICE_PROPERTIES,
    IDENTIFY_ACTION,
    READ_PROPERTIES,
    READ_RANGES,
    STATE_PROPERTIES,
    STATUS,
    WAKE_UP,
    WRITE_RANGES,
)
from .transport import RETRY_DELAYS, Session

_LOGGER = logging.getLogger(__name__)
IDLE_INTERVAL = timedelta(seconds=30)
MOVING_INTERVAL = timedelta(seconds=2)
CONNECT_TIMEOUT = 20
CONNECT_BUDGET = 120
UPDATE_BUDGET = 150
AUTH_COOLDOWN = 20


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
        self._active_task: asyncio.Task | None = None
        self._ready = False
        self._stopping = False
        self._reconnect_after = 0.0
        self._values: Properties = {}
        self.device_properties: dict[tuple[int, int], str] = {}
        self._metadata_read = False
        self._event_listeners: set[Callable[[Event], None]] = set()

    @callback
    def async_subscribe_events(
        self, listener: Callable[[Event], None]
    ) -> Callable[[], None]:
        """Subscribe to future occurrences only, independent of state refreshes."""
        self._event_listeners.add(listener)
        return lambda: self._event_listeners.discard(listener)

    @callback
    def _event_received(self, event: Event) -> None:
        if self._stopping:
            return
        for listener in tuple(self._event_listeners):
            listener(event)

    @callback
    def _disconnected(self, client: BleakClient) -> None:
        self.hass.loop.call_soon_threadsafe(self._handle_disconnect, client)

    @callback
    def _handle_disconnect(self, client: BleakClient) -> None:
        if client is self._client:
            self._reconnect_after = self.hass.loop.time() + AUTH_COOLDOWN
            if self._session:
                self._session.fail(BleakError("Curtain disconnected"))

    @callback
    def _session_error(self, error: Exception) -> None:
        self._ready = False
        self.update_interval = IDLE_INTERVAL
        self.async_set_update_error(UpdateFailed(str(error)))

    @callback
    def _push(self, values: PropertyValues) -> None:
        if self._stopping:
            return
        self._merge(values)
        if self._ready:
            self.async_set_updated_data(dict(self._values))

    def _merge(self, values: PropertyValues) -> None:
        for key, value in values.items():
            if key in DEVICE_PROPERTIES and isinstance(value, str) and value:
                self.device_properties[key] = value
            elif isinstance(value, int) and value in READ_RANGES.get(key, ()):
                self._values[key] = value
        if self.config_entry and any(key in DEVICE_PROPERTIES for key in values):
            registry = dr.async_get(self.hass)
            if device := registry.async_get_device(
                identifiers={(DOMAIN, self.address)}
            ):
                metadata = self.device_metadata
                registry.async_update_device(
                    device.id,
                    manufacturer=metadata.get("manufacturer", device.manufacturer),
                    model=metadata.get("model", device.model),
                    serial_number=metadata.get("serial_number", device.serial_number),
                    sw_version=metadata.get("sw_version", device.sw_version),
                )
        self.update_interval = (
            MOVING_INTERVAL if self._values.get(STATUS) in (1, 2) else IDLE_INTERVAL
        )

    @property
    def device_metadata(self) -> dict[str, str]:
        """Known device identity, separate from published operating state."""
        return {
            field: self.device_properties[key]
            for key, field in zip(
                DEVICE_PROPERTIES,
                ("manufacturer", "model", "serial_number", "sw_version"),
                strict=True,
            )
            if key in self.device_properties
        }

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
        async with asyncio.timeout(CONNECT_BUDGET):
            for attempt in range(3):
                if self._stopping:
                    raise ProtocolError("Integration is unloading")
                await self._disconnect()
                if (delay := self._reconnect_after - self.hass.loop.time()) > 0:
                    _LOGGER.debug("Waiting %.1f s before reconnecting", delay)
                    await sleep(delay)
                try:
                    async with asyncio.timeout(CONNECT_TIMEOUT):
                        device = bluetooth.async_ble_device_from_address(
                            self.hass, self.address, connectable=True
                        )
                        if device is None:
                            advertisement = (
                                await bluetooth.async_process_advertisements(
                                    self.hass,
                                    lambda info: info.connectable,
                                    {"address": self.address, "connectable": True},
                                    bluetooth.BluetoothScanningMode.PASSIVE,
                                    CONNECT_TIMEOUT,
                                )
                            )
                            device = advertisement.device
                        self._client = await establish_connection(
                            bleak_retry_connector.BleakClientWithServiceCache,
                            device,
                            self.address,
                            max_attempts=1,
                            disconnected_callback=self._disconnected,
                            ble_device_callback=lambda device=device: (
                                bluetooth.async_ble_device_from_address(
                                    self.hass, self.address, connectable=True
                                )
                                or device
                            ),
                        )
                    self._session = Session(
                        self._client,
                        self._push,
                        self._session_error,
                        self._event_received,
                    )
                    await self._session.login(self._ltmk)
                    self._values = {}
                    self._metadata_read = False
                    return self._session
                except AuthenticationError, UnsupportedMTUError:
                    raise
                except ProtocolError, BleakError, TimeoutError:
                    had_client = self._client is not None
                    await self._disconnect()
                    if attempt == 2 or self._stopping:
                        raise
                    if not had_client:
                        delay = RETRY_DELAYS[attempt % 2]
                        _LOGGER.debug(
                            "Connection setup failed; retrying after %s s", delay
                        )
                        await sleep(delay)
        raise ProtocolError("Connection attempts exhausted")

    async def _disconnect(self) -> None:
        self._ready = False
        # Keep ownership until teardown finishes so cancellation can retry cleanup.
        session = self._session
        client = self._client
        was_connected = client is not None and client.is_connected
        if session:
            # Intentional teardown must not publish an availability error.
            session.on_error = lambda _error: None
            await session.close()
            self._session = None
        if client:
            try:
                await client.disconnect()
            except BleakError:
                _LOGGER.debug("BLE disconnect failed", exc_info=True)
            if was_connected:
                self._reconnect_after = self.hass.loop.time() + AUTH_COOLDOWN
            self._client = None

    async def _async_update_data(self) -> Properties:
        async with self._lock:
            self._active_task = asyncio.current_task()
            try:
                async with asyncio.timeout(UPDATE_BUDGET):
                    for attempt in range(2):
                        session = await self._connect()
                        try:
                            requested = READ_PROPERTIES + (
                                () if self._metadata_read else DEVICE_PROPERTIES
                            )
                            values: PropertyValues = {}
                            for offset in range(0, len(requested), 6):
                                response = await session.request(
                                    2, get_payload(requested[offset : offset + 6])
                                )
                                values.update(parse_properties(response))
                            if not all(key in values for key in STATE_PROPERTIES):
                                raise ProtocolError("Missing curtain state properties")
                            if any(
                                values[key] not in READ_RANGES[key]
                                for key in STATE_PROPERTIES
                            ):
                                raise ProtocolError("Invalid curtain state")
                        except AuthenticationError, UnsupportedMTUError:
                            raise
                        except ProtocolError, BleakError, TimeoutError:
                            if attempt or self._stopping:
                                raise
                            await self._disconnect()
                            continue
                        self._ready = True
                        self._metadata_read = True
                        return dict(self._values)
                raise ProtocolError("State read attempts exhausted")
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
            finally:
                self._active_task = None

    async def async_command(self, key: tuple[int, int], value: int | bool) -> None:
        """Send once and require a property ACK, without optimistic position."""
        if value not in WRITE_RANGES.get(key, ()):
            raise ValueError("Invalid curtain command")
        if key == WAKE_UP and value is not True:
            raise ValueError("Wake-up requires a boolean trigger")
        payload = set_payload(key, value)
        await self._async_control(0, payload, key)

    async def async_action(self, key: tuple[int, int]) -> None:
        """Invoke the supported output-free action once."""
        if key != IDENTIFY_ACTION:
            raise ValueError("Unsupported curtain action")
        await self._async_control(5, action_payload(key))

    async def _async_control(
        self, opcode: int, payload: bytes, key: tuple[int, int] | None = None
    ) -> None:
        """Serialize controls and never replay an ambiguous result."""
        async with self._lock:
            self._active_task = asyncio.current_task()
            try:
                session = await self._connect()
                response = await session.request(opcode, payload)
                if opcode == 5:
                    parse_action_result(response)
                elif key not in parse_properties(response):
                    raise ProtocolError("Missing command acknowledgement")
            except AuthenticationError as err:
                self._session_error(err)
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
            finally:
                self._active_task = None
        # Poll in two seconds until the first read establishes the actual state.
        self.update_interval = MOVING_INTERVAL
        await self.async_request_refresh()

    async def async_shutdown(self) -> None:
        """Stop HA refresh timers and release the BLE connection."""
        self._stopping = True
        self._event_listeners.clear()
        await super().async_shutdown()
        if (task := self._active_task) and task is not asyncio.current_task():
            self._active_task = None
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        async with self._lock:
            await self._disconnect()
