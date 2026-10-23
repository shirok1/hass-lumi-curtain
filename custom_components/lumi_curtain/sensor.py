"""Battery and ambient light properties from the shared MIoT session."""

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CurtainConfigEntry
from .entity import CurtainEntity
from .spec import CHARGING_STATES, HOLD_STATES, LIGHT_STATES, SENSOR_PROPERTIES

SENSORS = (
    SensorEntityDescription(
        key="battery_level",
        translation_key="battery_level",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="charging_state",
        translation_key="charging_state",
        device_class=SensorDeviceClass.ENUM,
        options=list(CHARGING_STATES.values()),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="battery_temperature",
        translation_key="battery_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="ambient_light",
        translation_key="ambient_light",
        device_class=SensorDeviceClass.ENUM,
        options=list(LIGHT_STATES.values()),
    ),
    SensorEntityDescription(
        key="fault",
        translation_key="fault",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="run_time",
        translation_key="run_time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="hold_state",
        translation_key="hold_state",
        device_class=SensorDeviceClass.ENUM,
        options=list(HOLD_STATES.values()),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CurtainConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add read-only sensors to the same device as the cover."""
    async_add_entities(CurtainSensor(entry, description) for description in SENSORS)


class CurtainSensor(CurtainEntity, SensorEntity):
    """Display a confirmed property without opening another connection."""

    def __init__(
        self, entry: CurtainConfigEntry, description: SensorEntityDescription
    ) -> None:
        super().__init__(entry, description.key)
        self.entity_description = description
        self._property = SENSOR_PROPERTIES[description.key]

    @property
    def available(self) -> bool:
        return super().available and self._property in self.coordinator.data

    @property
    def native_value(self) -> int | float | str | None:
        value = self.coordinator.data.get(self._property)
        if value is None:
            return None
        match self.entity_description.key:
            case "battery_temperature":
                return value / 100
            case "charging_state":
                return CHARGING_STATES[value]
            case "ambient_light":
                return LIGHT_STATES[value]
            case "hold_state":
                return HOLD_STATES[value]
        return value
