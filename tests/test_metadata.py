"""Optional device information and diagnostics privacy across BLE sessions."""

from homeassistant.helpers import device_registry as dr

from custom_components.lumi_curtain.const import DOMAIN
from custom_components.lumi_curtain.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import ADDRESS
from .test_integration import setup

METADATA = {
    (1, 1): "Synthetic manufacturer",
    (1, 2): "Synthetic curtain model",
    (1, 3): "SYNTHETIC-SERIAL-DO-NOT-EXPORT",
    (1, 4): "1.2.3-test",
}


def device_for(hass, entry):
    return dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, ADDRESS), entry.entry_id
    )


async def test_metadata_updates_device_without_entering_diagnostics(
    hass, entry, peripheral, ble
):
    peripheral.properties.update(METADATA)
    entity_id = await setup(hass, entry)
    coordinator = entry.runtime_data
    device = device_for(hass, entry)
    assert device is not None
    assert device.manufacturer == METADATA[1, 1]
    assert device.model == METADATA[1, 2]
    assert device.serial_number == METADATA[1, 3]
    assert device.sw_version == METADATA[1, 4]
    assert coordinator.device_properties == METADATA
    assert not (METADATA.keys() & coordinator.data.keys())
    assert hass.states.get(entity_id).state == "open"

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert not any(value in str(diagnostics) for value in METADATA.values())

    # Defend the diagnostics boundary even if a future caller mixes metadata in.
    coordinator.data = {**coordinator.data, **METADATA}
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert not any(value in str(diagnostics) for value in METADATA.values())
    await hass.config_entries.async_unload(entry.entry_id)


async def test_missing_metadata_does_not_make_cover_unavailable(
    hass, entry, peripheral, ble
):
    entity_id = await setup(hass, entry)
    coordinator = entry.runtime_data
    assert coordinator.device_properties == {}
    assert coordinator.last_update_success
    assert hass.states.get(entity_id).state == "open"
    device = device_for(hass, entry)
    assert device is not None
    assert device.manufacturer == "Aqara"
    assert device.model == "Curtain HMCN02"
    await hass.config_entries.async_unload(entry.entry_id)


async def test_unsupported_metadata_types_do_not_make_cover_unavailable(
    hass, entry, peripheral, ble
):
    peripheral.properties.update(dict.fromkeys(METADATA, 1))
    entity_id = await setup(hass, entry)
    assert entry.runtime_data.device_properties == {}
    assert entry.runtime_data.last_update_success
    assert hass.states.get(entity_id).state == "open"
    await hass.config_entries.async_unload(entry.entry_id)


async def test_reconnect_refreshes_device_metadata(hass, entry, peripheral, ble):
    peripheral.properties.update(METADATA)
    await setup(hass, entry)
    coordinator = entry.runtime_data
    updated = {**METADATA, (1, 2): "Updated curtain model", (1, 4): "2.0.0-test"}
    await peripheral.disconnect()
    await hass.async_block_till_done()
    peripheral.properties.update(updated)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    device = device_for(hass, entry)
    assert device is not None
    assert coordinator.device_properties == updated
    assert device.model == updated[1, 2]
    assert device.sw_version == updated[1, 4]
    assert ble[1].await_count == 2
    await hass.config_entries.async_unload(entry.entry_id)
