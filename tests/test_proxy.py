"""Exercise HA's real remote Bluetooth routing with a simulated GATT backend."""

from unittest.mock import AsyncMock, MagicMock, patch

from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.service import BleakGATTService, BleakGATTServiceCollection
from bluetooth_data_tools import monotonic_time_coarse
from habluetooth import BaseHaRemoteScanner, HaBluetoothConnector
from homeassistant.components import bluetooth
from homeassistant.helpers import entity_registry as er

from custom_components.lumi_curtain.const import DOMAIN, MIBEACON_UUID, MODEL
from custom_components.lumi_curtain.transport import AUTH, NOTIFY, UPNP, VENDOR, WRITE

from .conftest import ADDRESS


async def test_cover_through_remote_proxy(hass, enable_bluetooth, entry, peripheral):
    services = BleakGATTServiceCollection()
    service = BleakGATTService(None, 1, MIBEACON_UUID)
    services.add_service(service)
    for handle, uuid in enumerate((UPNP, AUTH, WRITE, NOTIFY, VENDOR), 2):
        services.add_characteristic(
            BleakGATTCharacteristic(
                None,
                handle,
                uuid,
                ["write-without-response", "notify"],
                lambda: 244,
                service,
            )
        )
    backends = []

    def create_backend(device, **kwargs):
        backend = MagicMock()
        backend.services = services
        backend.mtu_size = 247
        backend.is_connected = False
        backend.address = device.address
        assert device.details["source"] == "AA:BB:CC:00:00:01"

        async def connect(pair, **kwargs):
            assert pair is False
            backend.is_connected = True
            peripheral.tx_counter = 0

        async def disconnect():
            backend.is_connected = False
            kwargs["disconnected_callback"]()

        async def start_notify(characteristic, callback, **kwargs):
            await peripheral.subscribe(
                characteristic.uuid, lambda _char, data: callback(data)
            )

        async def write(characteristic, data, response):
            await peripheral.write(characteristic.uuid, data, response)

        backend.connect = AsyncMock(side_effect=connect)
        backend.disconnect = AsyncMock(side_effect=disconnect)
        backend.start_notify = AsyncMock(side_effect=start_notify)
        backend.write_gatt_char = AsyncMock(side_effect=write)
        backends.append(backend)
        return backend

    factory = MagicMock(side_effect=create_backend)
    scanner = BaseHaRemoteScanner(
        "AA:BB:CC:00:00:01",
        "Living room proxy",
        HaBluetoothConnector(factory, "AA:BB:CC:00:00:01", lambda: True),
        connectable=True,
    )
    unregister = bluetooth.async_register_scanner(hass, scanner)
    unsetup = scanner.async_setup()
    try:
        scanner._async_on_advertisement(
            ADDRESS,
            -50,
            MODEL,
            [],
            {},
            {},
            None,
            {},
            monotonic_time_coarse(),
        )
        device = bluetooth.async_ble_device_from_address(
            hass, ADDRESS, connectable=True
        )
        assert device is not None
        assert device.details["source"] == scanner.source
        await hass.async_block_till_done(wait_background_tasks=True)
        flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        assert len(flows) == 1
        assert flows[0]["step_id"] == "bluetooth_confirm"
        assert not backends
        hass.config_entries.flow.async_abort(flows[0]["flow_id"])
        entry.add_to_hass(hass)
        # No local adapter backend or direct Bleak connection can satisfy this test.
        with patch(
            "habluetooth.wrappers.get_platform_client_backend_type",
            side_effect=AssertionError("Local Bluetooth used"),
        ):
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()
            entity = next(
                entity
                for entity in er.async_entries_for_config_entry(
                    er.async_get(hass), entry.entry_id
                )
                if entity.domain == "cover"
            )
            await hass.services.async_call(
                "cover",
                "set_cover_position",
                {"entity_id": entity.entity_id, "position": 25},
                blocking=True,
            )
            assert peripheral.commands == [(2, 25)]
            peripheral.push(6, 25)
            await hass.async_block_till_done()
            assert (
                hass.states.get(entity.entity_id).attributes["current_position"] == 25
            )
            await backends[-1].disconnect()
            await hass.async_block_till_done()
            assert hass.states.get(entity.entity_id).state == "unavailable"
            await entry.runtime_data.async_refresh()
            await hass.async_block_till_done()
            assert factory.call_count == 2
            assert hass.states.get(entity.entity_id).state == "open"
            await hass.config_entries.async_unload(entry.entry_id)
            assert not backends[-1].is_connected
    finally:
        unsetup()
        unregister()
