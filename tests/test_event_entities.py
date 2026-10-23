"""All MIoT event IDs reach their own HA event family on the shared link."""

from unittest.mock import patch

from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.translation import async_get_translations
from homeassistant.util import dt as dt_util

from custom_components.lumi_curtain.const import DOMAIN

from .conftest import ADDRESS
from .test_features import setup_features


async def test_all_event_types_and_parameters(hass, entry, ble):
    ids = await setup_features(hass, entry)
    coordinator = entry.runtime_data
    registry = er.async_get(hass)
    events = {
        key: entity_id for (domain, key), entity_id in ids.items() if domain == "event"
    }
    assert set(events) == {"manual_operation", "battery_alert", "device_event"}
    for key, entity_id in events.items():
        assert hass.states.get(entity_id).state == "unknown"
        entity = registry.async_get(entity_id)
        assert entity is not None
        assert entity.unique_id == f"{ADDRESS}_{key}"
        assert entity.disabled_by is None
        expected_category = None if key == "manual_operation" else "diagnostic"
        assert entity.entity_category == expected_category
    expected = {
        (2, 1): ("manual_operation", "opened"),
        (2, 2): ("manual_operation", "closed"),
        (5, 1): ("battery_alert", "low_battery"),
        (3, 4): ("battery_alert", "critical_battery"),
        (3, 5): ("battery_alert", "low_battery_automation"),
        (3, 7): ("battery_alert", "discharge_temperature_normal"),
        (3, 8): ("battery_alert", "discharge_overtemperature"),
        (3, 9): ("battery_alert", "discharge_undertemperature"),
        (3, 10): ("battery_alert", "charge_overtemperature"),
        (3, 11): ("battery_alert", "charge_undertemperature"),
        (3, 3): ("device_event", "factory_reset"),
        (3, 6): ("device_event", "error"),
    }
    for (siid, eiid), (key, event_type) in expected.items():
        previous = {
            k: hass.states.get(entity_id).state for k, entity_id in events.items()
        }
        coordinator._event_received((siid, eiid, {1: 9, 2: "detail"}))
        state = hass.states.get(events[key])
        assert state.state not in ("unknown", "unavailable")
        assert state.attributes["event_type"] == event_type
        assert state.attributes["siid"] == siid
        assert state.attributes["eiid"] == eiid
        assert state.attributes["parameters"] == {1: 9, 2: "detail"}
        assert "ciphertext" not in state.attributes
        assert all(
            hass.states.get(entity_id).state == previous[k]
            for k, entity_id in events.items()
            if k != key
        )
    before = {key: hass.states.get(entity_id) for key, entity_id in events.items()}
    coordinator._event_received((99, 99, {}))
    assert all(hass.states.get(events[key]) == state for key, state in before.items())
    assert len(coordinator._event_listeners) == 3
    ble[1].assert_awaited_once()
    await hass.config_entries.async_unload(entry.entry_id)
    assert not coordinator._event_listeners


async def test_repeated_identical_event_is_a_new_occurrence(hass, entry, ble):
    ids = await setup_features(hass, entry)
    entity_id = ids["event", "manual_operation"]
    now = dt_util.utcnow()
    with patch("homeassistant.components.event.dt_util.utcnow", return_value=now):
        entry.runtime_data._event_received((2, 1, {}))
        first = hass.states.get(entity_id)
        entry.runtime_data._event_received((2, 1, {}))
        second = hass.states.get(entity_id)
    assert first.state != second.state
    assert first.attributes["event_type"] == second.attributes["event_type"] == "opened"
    ble[1].assert_awaited_once()
    await hass.config_entries.async_unload(entry.entry_id)


async def test_event_translations(hass):
    translations = await async_get_translations(hass, "zh-Hans", "entity", {DOMAIN})
    prefix = f"component.{DOMAIN}.entity.event."
    assert translations[prefix + "manual_operation.name"] == "手动操作"
    assert (
        translations[
            prefix + "battery_alert.state_attributes.event_type.state.low_battery"
        ]
        == "电量低"
    )
