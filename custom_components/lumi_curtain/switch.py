"""Manual pull-to-start configuration on the shared curtain session."""

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CurtainConfigEntry
from .entity import CurtainEntity
from .spec import MANUAL_START


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CurtainConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the device's persistent manual-start setting."""
    async_add_entities([ManualStartSwitch(entry)])


class ManualStartSwitch(CurtainEntity, SwitchEntity):
    """Expose the inverted MIoT enable/disable enumeration."""

    _attr_translation_key = "manual_start"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: CurtainConfigEntry) -> None:
        super().__init__(entry, "manual_start")

    @property
    def available(self) -> bool:
        return super().available and MANUAL_START in self.coordinator.data

    @property
    def is_on(self) -> bool | None:
        value = self.coordinator.data.get(MANUAL_START)
        return None if value is None else value == 0

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_command(MANUAL_START, 0)

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_command(MANUAL_START, 1)
