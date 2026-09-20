"""Shared device identity and connection lifetime for curtain entities."""

from typing import TYPE_CHECKING

from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MODEL
from .coordinator import CurtainCoordinator

if TYPE_CHECKING:
    from . import CurtainConfigEntry


class CurtainEntity(CoordinatorEntity[CurtainCoordinator]):
    """Share one device and session until the last enabled entity is removed."""

    _attr_has_entity_name = True

    def __init__(self, entry: CurtainConfigEntry, key: str | None = None) -> None:
        unique_id = f"{entry.unique_id}_{key}" if key else entry.unique_id
        super().__init__(entry.runtime_data, context=unique_id)
        self._attr_unique_id = unique_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.address)},
            connections={(CONNECTION_BLUETOOTH, self.coordinator.address)},
            name=entry.title,
            manufacturer="Aqara",
            model="Curtain HMCN02",
            model_id=MODEL,
        )

    async def async_will_remove_from_hass(self) -> None:
        """HA has already removed this entity's coordinator listener."""
        await super().async_will_remove_from_hass()
        if not any(self.coordinator.async_contexts()):
            await self.coordinator.async_shutdown()
