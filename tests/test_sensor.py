"""MIoT reads and notifications reach native HA sensors over one BLE session."""

import struct

import pytest
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.translation import async_get_translations

from custom_components.lumi_curtain.const import DOMAIN
from custom_components.lumi_curtain.coordinator import CurtainCoordinator

from .conftest import ADDRESS, LTMK
from .test_integration import setup


def sensor_ids(hass, entry):
    return {
        entity.translation_key: entity.entity_id
        for entity in er.async_entries_for_config_entry(
            er.async_get(hass), entry.entry_id
        )
        if entity.domain == "sensor"
    }


async def test_sensor_reads_pushes_and_shared_connection(hass, entry, peripheral, ble):
    cover_id = await setup(hass, entry)
    ids = sensor_ids(hass, entry)
    expected = {
        "battery_level": ("69", "battery", "%"),
        "charging_state": ("not_charging", "enum", None),
        "battery_temperature": ("23.5", "temperature", "°C"),
        "ambient_light": ("bright", "enum", None),
    }
    registry = er.async_get(hass)
    cover = registry.async_get(cover_id)
    assert cover is not None
    device_id = cover.device_id
    for key, (value, device_class, unit) in expected.items():
        state = hass.states.get(ids[key])
        assert state.state == value
        assert state.attributes["device_class"] == device_class
        assert state.attributes.get("unit_of_measurement") == unit
        sensor = registry.async_get(ids[key])
        assert sensor is not None
        assert sensor.device_id == device_id
        assert sensor.unique_id == f"{ADDRESS}_{key}"
    assert hass.states.get(ids["ambient_light"]).attributes["options"] == [
        "dark",
        "bright",
        "critical",
    ]
    read = next(pt for pt in peripheral.plaintexts if pt[4] == 2)
    assert read[5] == 6
    assert set(struct.iter_unpack("<BH", read[6:])) == {
        (2, 6),
        (2, 3),
        (5, 1),
        (5, 2),
        (3, 16),
        (3, 11),
    }
    peripheral.push(1, 0, siid=5)
    peripheral.push(2, 1, siid=5)
    peripheral.push(16, -125, siid=3)
    peripheral.push(11, 0, siid=3)
    await hass.async_block_till_done()
    assert hass.states.get(ids["battery_level"]).state == "0"
    assert hass.states.get(ids["charging_state"]).state == "charging"
    assert hass.states.get(ids["battery_temperature"]).state == "-1.25"
    assert hass.states.get(ids["ambient_light"]).state == "dark"
    peripheral.push(2, 3, siid=5)
    peripheral.push(11, 2, siid=3)
    await hass.async_block_till_done()
    assert hass.states.get(ids["charging_state"]).state == "not_chargeable"
    assert hass.states.get(ids["ambient_light"]).state == "critical"
    ble[1].assert_awaited_once()
    await peripheral.disconnect()
    await hass.async_block_till_done()
    assert all(
        hass.states.get(entity_id).state == "unavailable" for entity_id in ids.values()
    )
    # Reconnection must not expose readings retained from the previous session.
    peripheral.properties.clear()
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(cover_id).state == "open"
    assert all(
        hass.states.get(entity_id).state == "unavailable" for entity_id in ids.values()
    )
    await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize("case", ["rejected", "invalid"])
async def test_optional_properties_do_not_block_cover(
    hass, entry, peripheral, ble, case
):
    peripheral.properties = (
        {}
        if case == "rejected"
        else {(5, 1): 101, (5, 2): 0, (3, 16): -5001, (3, 11): 3}
    )
    cover_id = await setup(hass, entry)
    ids = sensor_ids(hass, entry)
    assert hass.states.get(cover_id).state == "open"
    assert all(
        hass.states.get(entity_id).state == "unavailable" for entity_id in ids.values()
    )
    peripheral.push(1, 100, siid=5)
    peripheral.push(16, 10000, siid=3)
    await hass.async_block_till_done()
    assert hass.states.get(ids["battery_level"]).state == "100"
    assert hass.states.get(ids["battery_temperature"]).state == "100.0"
    peripheral.push(1, 255, siid=5)
    peripheral.push(16, 10001, siid=3)
    await hass.async_block_till_done()
    assert hass.states.get(ids["battery_level"]).state == "100"
    assert hass.states.get(ids["battery_temperature"]).state == "100.0"
    ble[1].assert_awaited_once()
    await hass.config_entries.async_unload(entry.entry_id)


async def test_sensor_translations(hass, entry, ble):
    await setup(hass, entry)
    translations = await async_get_translations(hass, "zh-Hans", "entity", {DOMAIN})
    prefix = f"component.{DOMAIN}.entity.sensor"
    assert translations[f"{prefix}.battery_temperature.name"] == "电池表面温度"
    assert translations[f"{prefix}.charging_state.state.not_chargeable"] == "不可充电"
    assert translations[f"{prefix}.ambient_light.state.critical"] == "临界"
    await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize(
    ("key", "value"), [((2, 1), 3), ((2, 2), 101), ((2, 2), -1), ((5, 1), 50)]
)
async def test_service_tree_restricts_writes(hass, ble, key, value):
    coordinator = CurtainCoordinator(hass, ADDRESS, LTMK.hex())
    with pytest.raises(ValueError, match="Invalid curtain command"):
        await coordinator.async_command(key, value)
    ble[1].assert_not_awaited()
    await coordinator.async_shutdown()
