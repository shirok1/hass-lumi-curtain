"""Additional curtain entities reuse the existing coordinator and BLE link."""

from unittest.mock import AsyncMock, patch

from homeassistant.helpers import entity_registry as er

from custom_components.lumi_curtain.spec import (
    FAULT,
    HOLD_STATE,
    MANUAL_START,
    RUN_TIME,
)


async def setup_features(hass, entry):
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return {
        (entity.domain, entity.translation_key): entity.entity_id
        for entity in er.async_entries_for_config_entry(
            er.async_get(hass), entry.entry_id
        )
    }


async def test_feature_states_and_confirmed_manual_setting(
    hass, entry, peripheral, ble
):
    peripheral.properties.update(
        {FAULT: 4, RUN_TIME: 27, HOLD_STATE: 2, MANUAL_START: 0}
    )
    ids = await setup_features(hass, entry)
    assert hass.states.get(ids["sensor", "fault"]).state == "4"
    runtime = hass.states.get(ids["sensor", "run_time"])
    assert runtime.state == "27"
    assert runtime.attributes["unit_of_measurement"] == "s"
    assert runtime.attributes["device_class"] == "duration"
    assert "state_class" not in runtime.attributes
    assert hass.states.get(ids["sensor", "hold_state"]).state == "locking"
    manual_id = ids["switch", "manual_start"]
    assert hass.states.get(manual_id).state == "on"
    coordinator = entry.runtime_data
    with patch.object(coordinator, "async_command", new_callable=AsyncMock) as command:
        await hass.services.async_call(
            "switch", "turn_off", {"entity_id": manual_id}, blocking=True
        )
        command.assert_awaited_once_with(MANUAL_START, 1)
        assert hass.states.get(manual_id).state == "on"
        peripheral.push(4, 1, siid=3)
        await hass.async_block_till_done()
        assert hass.states.get(manual_id).state == "off"
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": manual_id}, blocking=True
        )
        command.assert_awaited_with(MANUAL_START, 0)
    peripheral.push(10, 3, siid=3)
    peripheral.push(4, 255, siid=2)
    await hass.async_block_till_done()
    assert hass.states.get(ids["sensor", "hold_state"]).state == "unlocking"
    assert hass.states.get(ids["sensor", "fault"]).state == "255"
    ble[1].assert_awaited_once()
    await hass.config_entries.async_unload(entry.entry_id)


async def test_feature_buttons_share_link_and_use_correct_command(hass, entry, ble):
    ids = await setup_features(hass, entry)
    coordinator = entry.runtime_data
    with (
        patch.object(coordinator, "async_command", new_callable=AsyncMock) as command,
        patch.object(coordinator, "async_action", new_callable=AsyncMock) as action,
    ):
        await hass.services.async_call(
            "button", "press", {"entity_id": ids["button", "wake_up"]}, blocking=True
        )
        command.assert_awaited_once_with((2, 7), True)
        assert command.await_args is not None
        assert command.await_args.args[1] is True
        await hass.services.async_call(
            "button", "press", {"entity_id": ids["button", "identify"]}, blocking=True
        )
        action.assert_awaited_once_with((4, 1))
    registry = er.async_get(hass)
    identify = registry.async_get(ids["button", "identify"])
    manual = registry.async_get(ids["switch", "manual_start"])
    assert identify is not None and identify.entity_category == "diagnostic"
    assert manual is not None and manual.entity_category == "config"
    ble[1].assert_awaited_once()
    await hass.config_entries.async_unload(entry.entry_id)


async def test_missing_optional_feature_values_do_not_disable_cover(hass, entry, ble):
    ids = await setup_features(hass, entry)
    assert entry.runtime_data.last_update_success
    assert hass.states.get(ids["sensor", "fault"]).state == "unavailable"
    assert hass.states.get(ids["sensor", "run_time"]).state == "unavailable"
    assert hass.states.get(ids["sensor", "hold_state"]).state == "unavailable"
    assert hass.states.get(ids["switch", "manual_start"]).state == "unavailable"
    assert hass.states.get(ids["button", "wake_up"]).state != "unavailable"
    assert hass.states.get(ids["cover", None]).state == "open"
    ble[1].assert_awaited_once()
    await hass.config_entries.async_unload(entry.entry_id)
