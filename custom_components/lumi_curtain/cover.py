"""A native HA cover; 0 is closed and 100 is open."""

from typing import Any

from homeassistant.components.cover import (
    ATTR_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CurtainConfigEntry
from .entity import CurtainEntity
from .spec import MOTOR_CONTROL, POSITION, STATUS, TARGET_POSITION


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CurtainConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create one curtain entity per Bluetooth address."""
    async_add_entities([LumiCurtain(entry)])


class LumiCurtain(CurtainEntity, CoverEntity):
    """Expose the last confirmed state and the four supported controls."""

    _attr_name = None
    _attr_device_class = CoverDeviceClass.CURTAIN
    _attr_supported_features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.STOP
        | CoverEntityFeature.SET_POSITION
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
        await self.coordinator.async_command(MOTOR_CONTROL, 1)

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self.coordinator.async_command(MOTOR_CONTROL, 2)

    async def async_stop_cover(self, **kwargs: Any) -> None:
        await self.coordinator.async_command(MOTOR_CONTROL, 0)

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        await self.coordinator.async_command(TARGET_POSITION, kwargs[ATTR_POSITION])
