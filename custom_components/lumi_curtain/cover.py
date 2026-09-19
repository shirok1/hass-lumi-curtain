"""A native HA cover; 0 is closed and 100 is open."""

from typing import Any

from homeassistant.components.cover import (
    ATTR_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import CurtainConfigEntry
from .const import DOMAIN, MODEL, POSITION, STATUS
from .coordinator import CurtainCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CurtainConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create one curtain entity per Bluetooth address."""
    async_add_entities([LumiCurtain(entry)])


class LumiCurtain(CoordinatorEntity[CurtainCoordinator], CoverEntity):
    """Expose the last confirmed state and the four supported controls."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_device_class = CoverDeviceClass.CURTAIN
    _attr_supported_features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.STOP
        | CoverEntityFeature.SET_POSITION
    )

    def __init__(self, entry: CurtainConfigEntry) -> None:
        super().__init__(entry.runtime_data)
        self._attr_unique_id = entry.unique_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.address)},
            connections={(CONNECTION_BLUETOOTH, self.coordinator.address)},
            name=entry.title,
            manufacturer="Aqara",
            model=MODEL,
        )

    @property
    def current_cover_position(self) -> int | None:
        return self.coordinator.data.get(POSITION)

    @property
    def is_closed(self) -> bool | None:
        position = self.current_cover_position
        return None if position is None else position == 0

    @property
    def is_opening(self) -> bool:
        return self.coordinator.data.get(STATUS) == 1

    @property
    def is_closing(self) -> bool:
        return self.coordinator.data.get(STATUS) == 2

    async def async_open_cover(self, **kwargs: Any) -> None:
        await self.coordinator.async_command(1, 1)

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self.coordinator.async_command(1, 2)

    async def async_stop_cover(self, **kwargs: Any) -> None:
        await self.coordinator.async_command(1, 0)

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        await self.coordinator.async_command(2, kwargs[ATTR_POSITION])
