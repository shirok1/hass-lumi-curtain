"""Native HA target resolution dispatches wake-up to the selected curtains."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.service import _load_services_file
from homeassistant.loader import async_get_integration
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.lumi_curtain.const import DOMAIN
from custom_components.lumi_curtain.spec import WAKE_UP

from .conftest import LTMK
from .test_features import setup_features


async def test_wake_up_service_sends_native_command(hass, entry, peripheral, ble):
    ids = await setup_features(hass, entry)
    await hass.services.async_call(
        DOMAIN, "wake_up", target={"entity_id": ids["cover", None]}, blocking=True
    )
    assert peripheral.property_writes == [((2, 7), 1, 1)]
    ble[1].assert_awaited_once()
    integration = await async_get_integration(hass, DOMAIN)
    descriptions = await hass.async_add_executor_job(_load_services_file, integration)
    assert descriptions["wake_up"]["target"]["entity"] == [
        {"integration": DOMAIN, "domain": ["cover"]}
    ]
    await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize("target_kind", ["entity_id", "device_id", "area_id"])
async def test_wake_up_multiple_targets(hass, entry, ble, target_kind):
    other = MockConfigEntry(
        domain=DOMAIN,
        title="Bedroom",
        unique_id="11:22:33:44:55:66",
        data={"address": "11:22:33:44:55:66", "ltmk": LTMK.hex()},
    )
    first = await setup_features(hass, entry)
    second = await setup_features(hass, other)
    entity_ids = [first["cover", None], second["cover", None]]
    registry = er.async_get(hass)
    device_ids = []
    for entity_id in entity_ids:
        entity = registry.async_get(entity_id)
        assert entity is not None and entity.device_id is not None
        device_ids.append(entity.device_id)
    area = ar.async_get(hass).async_create("Curtains")
    for device_id in device_ids:
        dr.async_get(hass).async_update_device(device_id, area_id=area.id)
    targets = {"entity_id": entity_ids, "device_id": device_ids, "area_id": area.id}
    with (
        patch.object(
            entry.runtime_data, "async_command", new_callable=AsyncMock
        ) as one,
        patch.object(
            other.runtime_data, "async_command", new_callable=AsyncMock
        ) as two,
    ):
        await hass.services.async_call(
            DOMAIN, "wake_up", target={target_kind: targets[target_kind]}, blocking=True
        )
        one.assert_awaited_once_with(WAKE_UP, True)
        two.assert_awaited_once_with(WAKE_UP, True)
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.config_entries.async_unload(other.entry_id)
