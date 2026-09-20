"""Real HA setup, cover services, push state, recovery and unload."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from bleak.exc import BleakError
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.lumi_curtain import async_unload_entry
from custom_components.lumi_curtain.const import DOMAIN
from custom_components.lumi_curtain.coordinator import (
    IDLE_INTERVAL,
    MOVING_INTERVAL,
    CurtainCoordinator,
)
from custom_components.lumi_curtain.cover import LumiCurtain
from custom_components.lumi_curtain.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.lumi_curtain.protocol import ProtocolError
from custom_components.lumi_curtain.spec import POSITION, STATUS

from .conftest import ADDRESS, LTMK


async def setup(hass, entry):
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    assert len(entities) == 5
    return next(entity.entity_id for entity in entities if entity.domain == "cover")


async def test_full_cover_lifecycle(hass, entry, peripheral, ble):
    entity_id = await setup(hass, entry)
    state = hass.states.get(entity_id)
    assert state.state == "open"
    assert state.attributes["current_position"] == 50
    assert state.attributes["device_class"] == "curtain"
    for service, fields in [
        ("open_cover", {}),
        ("close_cover", {}),
        ("stop_cover", {}),
        ("set_cover_position", {"position": 25}),
    ]:
        await hass.services.async_call(
            "cover", service, {"entity_id": entity_id, **fields}, blocking=True
        )
    assert peripheral.commands == [(1, 1), (1, 2), (1, 0), (2, 25)]
    peripheral.push(6, 100)
    peripheral.push(3, 1)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "opening"
    assert hass.states.get(entity_id).attributes["current_position"] == 100
    assert entry.runtime_data.update_interval == MOVING_INTERVAL
    peripheral.push(3, 2)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "closing"
    peripheral.push(6, 0)
    peripheral.push(3, 0)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "closed"
    assert entry.runtime_data.update_interval == IDLE_INTERVAL
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["state"] == {
        "2.6": 0,
        "2.3": 0,
        "5.1": 69,
        "5.2": 2,
        "3.16": 2350,
        "3.11": 1,
    }
    assert LTMK.hex() not in str(diagnostics) and ADDRESS not in str(diagnostics)
    coordinator = entry.runtime_data
    await hass.config_entries.async_unload(entry.entry_id)
    assert peripheral.client.disconnect.await_count == 1
    assert coordinator._session is None


@pytest.mark.parametrize("title", ["Lumi Curtain", "Living room"])
async def test_brand_update_preserves_registry_names(hass, entry, ble, title):
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, title=title)
    devices = dr.async_get(hass)
    device = devices.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, ADDRESS)},
        name=title,
    )
    devices.async_update_device(device.id, name_by_user="客厅窗帘")
    entities = er.async_get(hass)
    entity = entities.async_get_or_create(
        "cover",
        DOMAIN,
        ADDRESS,
        suggested_object_id="existing_curtain",
        config_entry=entry,
        device_id=device.id,
    )
    entities.async_update_entity(entity.entity_id, name="落地窗")

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    expected = "Aqara Curtain" if title == "Lumi Curtain" else title
    assert entry.title == expected
    device = devices.async_get(device.id)
    assert isinstance(device, dr.DeviceEntry)
    assert device.name == expected
    assert device.name_by_user == "客厅窗帘"
    assert device.manufacturer == "Aqara"
    assert device.model == "Curtain HMCN02"
    assert device.model_id == "lumi.curtain.hmcn02"
    assert (
        hass.states.get("cover.existing_curtain").attributes["friendly_name"]
        == "落地窗"
    )
    assert len(er.async_entries_for_config_entry(entities, entry.entry_id)) == 5
    await hass.config_entries.async_unload(entry.entry_id)


async def test_disconnect_reconnect_and_old_callback(hass, entry, peripheral, ble):
    entity_id = await setup(hass, entry)
    await peripheral.disconnect()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "unavailable"
    entry.runtime_data._push({POSITION: 88})
    assert hass.states.get(entity_id).state == "unavailable"
    peripheral.tx_counter = 0
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "open"
    assert ble[1].await_count == 2
    entry.runtime_data._handle_disconnect(object())
    assert entry.runtime_data.last_update_success
    await hass.config_entries.async_unload(entry.entry_id)


async def test_counter_rekeys_before_command(hass, entry, peripheral, ble):
    entity_id = await setup(hass, entry)
    old_session = entry.runtime_data._session
    old_session.tx_counter = 65520
    await hass.services.async_call(
        "cover", "stop_cover", {"entity_id": entity_id}, blocking=True
    )
    assert entry.runtime_data._session is not old_session
    assert old_session._keys == b""
    assert ble[1].await_count == 2
    assert peripheral.commands == [(1, 0)]
    await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize(
    "failure", ["unreachable", "auth", "missing", "invalid", "connect"]
)
async def test_setup_failure_cleanup(hass, entry, peripheral, ble, failure):
    if failure == "unreachable":
        ble[0].return_value = None
    elif failure == "auth":
        peripheral.reject = True
    elif failure == "missing":
        peripheral.get_response = bytes.fromhex("0e20010003010206000000011032")
    elif failure == "invalid":
        peripheral.position = 101
    else:
        ble[1].side_effect = BleakError("Unavailable")
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    expected = (
        ConfigEntryState.SETUP_ERROR
        if failure == "auth"
        else ConfigEntryState.SETUP_RETRY
    )
    assert entry.state == expected
    if failure not in ("unreachable", "connect"):
        peripheral.client.disconnect.assert_awaited_once()


async def test_command_rejection_no_retry(hass, entry, peripheral, ble):
    entity_id = await setup(hass, entry)
    peripheral.command_code = 5
    with pytest.raises(HomeAssistantError, match="outcome may be unknown"):
        await hass.services.async_call(
            "cover", "open_cover", {"entity_id": entity_id}, blocking=True
        )
    assert peripheral.commands == [(1, 1)]
    assert entry.runtime_data._session is None
    assert not entry.runtime_data.last_update_success
    await hass.config_entries.async_unload(entry.entry_id)


async def test_reauth_on_command(hass, entry, peripheral, ble):
    await setup(hass, entry)
    entry.runtime_data._session.tx_counter = 65520
    peripheral.reject = True
    with pytest.raises(HomeAssistantError, match="rejected LTMK"):
        await entry.runtime_data.async_command((2, 1), 0)
    await hass.async_block_till_done()
    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    await hass.config_entries.async_unload(entry.entry_id)


async def test_unload_failure_retains_connection(hass, entry, peripheral, ble):
    await setup(hass, entry)
    with patch.object(
        hass.config_entries, "async_unload_platforms", return_value=False
    ):
        assert not await async_unload_entry(hass, entry)
    peripheral.client.disconnect.assert_not_awaited()
    await hass.config_entries.async_unload(entry.entry_id)


async def test_late_push_unknown_position_and_shutdown(hass, entry, peripheral, ble):
    await setup(hass, entry)
    coordinator = entry.runtime_data
    coordinator.data = {}
    assert LumiCurtain(entry).is_closed is None
    coordinator._push({POSITION: -1, STATUS: 3, (3, 20): 42})
    assert coordinator._values[POSITION] == 50
    await hass.config_entries.async_unload(entry.entry_id)
    coordinator._push({POSITION: 99})
    assert coordinator._values[POSITION] == 50
    with pytest.raises(ProtocolError, match="unloading"):
        await coordinator._connect()


async def test_missing_command_ack_and_disconnect_failure(hass, entry, peripheral, ble):
    await setup(hass, entry)
    peripheral.client.disconnect.side_effect = BleakError("Already gone")
    with (
        patch.object(
            entry.runtime_data._session,
            "request",
            return_value=bytes.fromhex("0b20010001010202000000"),
        ),
        pytest.raises(HomeAssistantError),
    ):
        await entry.runtime_data.async_command((2, 1), 0)
    await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize("command", [False, True])
async def test_cancellation_releases_connection(hass, entry, peripheral, ble, command):
    await setup(hass, entry)
    coordinator = entry.runtime_data
    session = coordinator._session
    session.request = AsyncMock(side_effect=asyncio.CancelledError)
    with pytest.raises(asyncio.CancelledError):
        if command:
            await coordinator.async_command((2, 1), 1)
        else:
            await coordinator._async_update_data()
    assert coordinator._session is None
    await hass.config_entries.async_unload(entry.entry_id)


async def test_setup_forward_failure_cleanup(hass, entry, peripheral, ble):
    entry.add_to_hass(hass)
    with patch.object(
        hass.config_entries,
        "async_forward_entry_setups",
        side_effect=RuntimeError("Platform error"),
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
    peripheral.client.disconnect.assert_awaited_once()


async def test_no_entry_auth_failure(hass, peripheral, ble):
    coordinator = CurtainCoordinator(hass, ADDRESS, LTMK.hex())
    peripheral.reject = True
    with pytest.raises(HomeAssistantError):
        await coordinator.async_command((2, 1), 0)
    await coordinator.async_shutdown()


async def test_newer_push_wins_over_read_response(hass, entry, peripheral, ble):
    entity_id = await setup(hass, entry)
    original = peripheral.respond

    def respond_then_push(request):
        original(request)
        if request[4] == 2:
            peripheral.push(6, 87)

    peripheral.respond = respond_then_push
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes["current_position"] == 87
    await hass.config_entries.async_unload(entry.entry_id)
