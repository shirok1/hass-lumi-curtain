"""Momentary curtain events received over the shared authenticated session."""

from homeassistant.components.event import EventEntity, EventEntityDescription
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CurtainConfigEntry
from .entity import CurtainEntity
from .protocol import Event
from .spec import EVENT_TYPES

EVENTS = tuple(
    EventEntityDescription(
        key=key,
        translation_key=key,
        event_types=list(mapping.values()),
        entity_category=None
        if key == "manual_operation"
        else EntityCategory.DIAGNOSTIC,
    )
    for key, mapping in EVENT_TYPES.items()
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CurtainConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add one entity for each family of device events."""
    async_add_entities(CurtainEvent(entry, description) for description in EVENTS)


class CurtainEvent(CurtainEntity, EventEntity):
    """Publish received events without replaying cached device notifications."""

    def __init__(
        self, entry: CurtainConfigEntry, description: EventEntityDescription
    ) -> None:
        super().__init__(entry, description.key)
        self.entity_description = description
        self._event_mapping = EVENT_TYPES[description.key]

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_subscribe_events(self._event_received)
        )

    @callback
    def _event_received(self, event: Event) -> None:
        siid, eiid, parameters = event
        if event_type := self._event_mapping.get((siid, eiid)):
            self._trigger_event(
                event_type,
                {"siid": siid, "eiid": eiid, "parameters": parameters},
            )
            self.async_write_ha_state()
