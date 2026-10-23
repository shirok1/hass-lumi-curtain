"""Wake-up and identification commands on the shared curtain session."""

from homeassistant.components.button import (
    ButtonDeviceClass,
    ButtonEntity,
    ButtonEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CurtainConfigEntry
from .entity import CurtainEntity
from .spec import IDENTIFY_ACTION, WAKE_UP

BUTTONS = (
    ButtonEntityDescription(key="wake_up", translation_key="wake_up"),
    ButtonEntityDescription(
        key="identify",
        translation_key="identify",
        device_class=ButtonDeviceClass.IDENTIFY,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CurtainConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add command buttons without creating another BLE connection."""
    async_add_entities(CurtainButton(entry, description) for description in BUTTONS)


class CurtainButton(CurtainEntity, ButtonEntity):
    """Send one confirmed command when pressed."""

    def __init__(
        self, entry: CurtainConfigEntry, description: ButtonEntityDescription
    ) -> None:
        super().__init__(entry, description.key)
        self.entity_description = description

    async def async_press(self) -> None:
        if self.entity_description.key == "identify":
            await self.coordinator.async_action(IDENTIFY_ACTION)
        else:
            await self.coordinator.async_command(WAKE_UP, True)
