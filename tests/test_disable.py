"""HA disable/enable lifecycle must release the curtain's BLE slot."""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from bleak.exc import BleakError
from homeassistant.config_entries import ConfigEntryDisabler
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.lumi_curtain.const import DOMAIN

from .conftest import ADDRESS
from .test_integration import setup


@pytest.mark.parametrize("target", ["entry", "device", "entity"])
async def test_disable_reload_enable(hass, entry, peripheral, ble, target):
    entity_id = await setup(hass, entry)
    entities = er.async_get(hass)
    devices = dr.async_get(hass)
    entity = entities.async_get(entity_id)
    assert entity is not None
    device_id = entity.device_id
    assert device_id is not None
    coordinator = entry.runtime_data
    client = peripheral.client

    if target == "entry":
        assert await hass.config_entries.async_set_disabled_by(
            entry.entry_id, ConfigEntryDisabler.USER
        )
    elif target == "device":
        devices.async_update_device(device_id, disabled_by=dr.DeviceEntryDisabler.USER)
    else:
        entities.async_update_entity(
            entity_id, disabled_by=er.RegistryEntryDisabler.USER
        )
    await hass.async_block_till_done()
    client.disconnect.assert_awaited_once()
    assert coordinator._session is None
    assert coordinator._stopping
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert ble[1].await_count == 1

    if target == "entry":
        assert await hass.config_entries.async_set_disabled_by(entry.entry_id, None)
    elif target == "device":
        devices.async_update_device(device_id, disabled_by=None)
    else:
        entities.async_update_entity(entity_id, disabled_by=None)
    await hass.async_block_till_done()
    # HA reloads entries after registry entities are re-enabled.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
    await hass.async_block_till_done()
    assert ble[1].await_count == 2
    assert hass.states.get(entity_id).state == "open"
    await hass.config_entries.async_unload(entry.entry_id)


async def test_disabled_device_without_entities_never_connects(hass, entry, ble):
    entry.add_to_hass(hass)
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, ADDRESS)},
        disabled_by=dr.DeviceEntryDisabler.USER,
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    ble[1].assert_not_awaited()
    assert entry.runtime_data._stopping
    await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize("operation", ["read", "command", "backoff"])
async def test_disable_cancels_active_work(
    hass, entry, peripheral, ble, retry_sleep, operation
):
    await setup(hass, entry)
    coordinator = entry.runtime_data
    client = peripheral.client
    started = asyncio.Event()

    async def hang(*_args):
        started.set()
        await asyncio.Future()

    if operation == "backoff":
        coordinator._session.request = AsyncMock(side_effect=BleakError("Lost link"))
        retry_sleep[0].side_effect = hang
    else:
        coordinator._session.request = AsyncMock(side_effect=hang)
    task = asyncio.create_task(
        coordinator.async_command(1, 0)
        if operation == "command"
        else coordinator._async_update_data()
    )
    try:
        async with asyncio.timeout(1):
            await started.wait()
            assert await hass.config_entries.async_set_disabled_by(
                entry.entry_id, ConfigEntryDisabler.USER
            )
        with pytest.raises(asyncio.CancelledError):
            await task
        client.disconnect.assert_awaited_once()
        assert coordinator._active_task is None
        assert coordinator._session is None
        assert ble[1].await_count == 1
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
