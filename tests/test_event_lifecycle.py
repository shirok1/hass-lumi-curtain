"""Encrypted device events preserve coordinator and HA entity lifetimes."""

import struct
from datetime import timedelta

from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.lumi_curtain.protocol import frame

from .conftest import ADDRESS
from .test_integration import setup


def event_entity(hass, entry, key):
    return next(
        entity
        for entity in er.async_entries_for_config_entry(
            er.async_get(hass), entry.entry_id
        )
        if entity.unique_id == f"{ADDRESS}_{key}"
    )


def emit_event(peripheral, siid, eiid):
    peripheral.send_plaintext(frame(501, 7, struct.pack("<BHB", siid, eiid, 0)))


async def test_polling_and_property_push_do_not_repeat_events(
    hass, entry, peripheral, ble
):
    await setup(hass, entry)
    entity = event_entity(hass, entry, "manual_operation")
    emit_event(peripheral, 2, 1)
    await hass.async_block_till_done()
    occurred = hass.states.get(entity.entity_id)
    assert occurred.state != "unknown"

    await entry.runtime_data.async_refresh()
    peripheral.push(6, 73)
    await hass.async_block_till_done()

    after = hass.states.get(entity.entity_id)
    assert after.state == occurred.state
    assert after.attributes == occurred.attributes
    assert after.last_changed == occurred.last_changed
    await hass.config_entries.async_unload(entry.entry_id)


async def test_disable_and_reenable_event_replaces_its_listener(
    hass, entry, peripheral, ble
):
    await setup(hass, entry)
    coordinator = entry.runtime_data
    registry = er.async_get(hass)
    entity = event_entity(hass, entry, "manual_operation")
    assert len(coordinator._event_listeners) == 3
    registry.async_update_entity(
        entity.entity_id, disabled_by=er.RegistryEntryDisabler.USER
    )
    await hass.async_block_till_done()
    assert len(coordinator._event_listeners) == 2
    peripheral.client.disconnect.assert_not_awaited()

    registry.async_update_entity(entity.entity_id, disabled_by=None)
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert not coordinator._event_listeners
    replacement = entry.runtime_data
    assert len(replacement._event_listeners) == 3
    await hass.config_entries.async_unload(entry.entry_id)
    assert not replacement._event_listeners


async def test_last_enabled_event_owns_the_connection(hass, entry, peripheral, ble):
    await setup(hass, entry)
    coordinator = entry.runtime_data
    registry = er.async_get(hass)
    entity = event_entity(hass, entry, "manual_operation")
    for registered in er.async_entries_for_config_entry(registry, entry.entry_id):
        if registered.entity_id != entity.entity_id:
            registry.async_update_entity(
                registered.entity_id, disabled_by=er.RegistryEntryDisabler.USER
            )
    await hass.async_block_till_done()
    assert len(coordinator._event_listeners) == 1
    assert not coordinator._stopping
    peripheral.client.disconnect.assert_not_awaited()
    emit_event(peripheral, 2, 2)
    await hass.async_block_till_done()
    assert hass.states.get(entity.entity_id).state != "unknown"

    registry.async_update_entity(
        entity.entity_id, disabled_by=er.RegistryEntryDisabler.USER
    )
    await hass.async_block_till_done()
    assert coordinator._stopping
    assert coordinator._session is None
    assert not coordinator._event_listeners
    peripheral.client.disconnect.assert_awaited_once()
    await hass.config_entries.async_unload(entry.entry_id)


async def test_factory_reset_event_does_not_remove_keys_or_entry(
    hass, entry, peripheral, ble
):
    await setup(hass, entry)
    coordinator = entry.runtime_data
    session = coordinator._session
    keys = session._keys
    entry_data = dict(entry.data)
    entity = event_entity(hass, entry, "device_event")

    emit_event(peripheral, 3, 3)
    await hass.async_block_till_done()

    assert hass.states.get(entity.entity_id).state != "unknown"
    assert hass.config_entries.async_get_entry(entry.entry_id) is entry
    assert entry.data == entry_data
    assert coordinator._session is session
    assert session._keys == keys
    assert coordinator.last_update_success
    peripheral.client.disconnect.assert_not_awaited()
    await hass.config_entries.async_unload(entry.entry_id)
