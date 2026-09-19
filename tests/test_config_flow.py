"""Config flow tests authenticate against the simulated BLE peripheral."""

from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.translation import async_get_translations

from custom_components.lumi_curtain.const import DOMAIN

from .conftest import ADDRESS, LTMK


async def test_user_setup(hass, peripheral, ble):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] == FlowResultType.FORM
    assert set(result["data_schema"].schema) == {"address", "ltmk"}
    with patch("custom_components.lumi_curtain.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "address": ADDRESS.lower().replace(":", "-"),
                "ltmk": LTMK.hex().upper(),
            },
        )
        await hass.async_block_till_done()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Aqara Curtain"
    assert result["data"] == {"address": ADDRESS, "ltmk": LTMK.hex()}
    assert result["result"].unique_id == ADDRESS
    peripheral.client.disconnect.assert_awaited_once()


@pytest.mark.parametrize(
    ("address", "ltmk", "failure", "errors"),
    [
        ("not-a-mac", LTMK.hex(), None, {"address": "invalid_address"}),
        (ADDRESS, "short", None, {"base": "invalid_ltmk"}),
        (ADDRESS, "g" * 64, None, {"base": "invalid_ltmk"}),
        (ADDRESS, LTMK.hex(), "auth", {"base": "invalid_auth"}),
        (ADDRESS, LTMK.hex(), "unreachable", {"base": "cannot_connect"}),
    ],
)
async def test_validation_errors(hass, peripheral, ble, address, ltmk, failure, errors):
    if failure == "auth":
        peripheral.reject = True
    if failure == "unreachable":
        ble[0].return_value = None
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}, data={"address": address, "ltmk": ltmk}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == errors


async def test_duplicate(hass, entry, ble):
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "user"},
        data={"address": ADDRESS, "ltmk": LTMK.hex()},
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    ble[1].assert_not_called()


async def test_reauthentication(hass, entry, peripheral, ble):
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reauth", "entry_id": entry.entry_id},
        data=entry.data,
    )
    assert result["step_id"] == "reauth_confirm"
    progress = hass.config_entries.flow.async_progress_by_handler(DOMAIN)[0]
    assert progress["context"]["title_placeholders"]["suffix"] == "DDEEFF"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"ltmk": "bad"}
    )
    assert result["errors"] == {"base": "invalid_ltmk"}
    with patch.object(hass.config_entries, "async_reload", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"ltmk": LTMK.hex()}
        )
        await hass.async_block_till_done()
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"


def discovery(
    name="lumi.curtain.hmcn02", payload=None, connectable=True, source="proxy-1"
):
    from bleak.backends.device import BLEDevice
    from homeassistant.components.bluetooth import BluetoothServiceInfoBleak

    from custom_components.lumi_curtain.const import MIBEACON_UUID

    return BluetoothServiceInfoBleak(
        name=name,
        address=ADDRESS,
        rssi=-50,
        manufacturer_data={},
        service_data={MIBEACON_UUID: payload} if payload is not None else {},
        service_uuids=[],
        source=source,
        device=BLEDevice(ADDRESS, name, {"source": source}),
        advertisement=None,
        connectable=connectable,
        time=0,
        tx_power=None,
    )


@pytest.mark.parametrize(
    ("name", "payload"),
    [
        ("lumi.curtain.hmcn02", None),
        ("Mijia mesh demo", bytes.fromhex("9053390c01ffeeddccbbaa")),
        ("", bytes.fromhex("9853390c01ffeeddccbbaa")),
        ("", bytes.fromhex("8053390c01")),
        (ADDRESS, bytes.fromhex("b054390c01ffeeddccbbaa080e00")),
    ],
)
async def test_bluetooth_discovery(hass, peripheral, ble, name, payload):
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "bluetooth"},
        data=discovery(name, payload),
    )
    assert result["step_id"] == "bluetooth_confirm"
    assert result["description_placeholders"] == {"address": ADDRESS}
    assert set(result["data_schema"].schema) == {"ltmk"}
    ble[1].assert_not_called()
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"ltmk": "invalid"},
    )
    assert result["step_id"] == "bluetooth_confirm"
    assert result["errors"] == {"base": "invalid_ltmk"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"ltmk": LTMK.hex()},
    )
    await hass.async_block_till_done()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Aqara Curtain"
    assert result["data"] == {"address": ADDRESS, "ltmk": LTMK.hex()}
    assert result["result"].unique_id == ADDRESS
    entry = result["result"]
    assert entry.state == ConfigEntryState.LOADED
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert len(devices) == 1
    # HA's completion page uses this registered device for naming and area selection.
    assert devices[0].name == "Aqara Curtain"
    assert devices[0].primary_config_entry == entry.entry_id
    await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize(
    ("name", "payload", "connectable", "reason"),
    [
        ("lumi.curtain.hmcn02", None, False, "not_connectable"),
        ("Mijia mesh demo", None, True, "not_supported"),
        ("lumi.curtain.hmcn05", None, True, "not_supported"),
        ("Other device", bytes.fromhex("9053380c01"), True, "not_supported"),
        ("Other device", bytes.fromhex("9053390c"), True, "not_supported"),
        ("Other device", bytes.fromhex("9001390c01"), True, "not_supported"),
        # A name must not override a different product ID in the advertisement.
        ("lumi.curtain.hmcn02", bytes.fromhex("9053380c01"), True, "not_supported"),
        ("lumi.curtain.hmcn02", b"", True, "not_supported"),
        # An embedded MAC must identify the broadcaster, not another device.
        ("Mijia mesh demo", bytes.fromhex("9053390c01"), True, "not_supported"),
        (
            "Mijia mesh demo",
            bytes.fromhex("9053390c01060504030201"),
            True,
            "not_supported",
        ),
    ],
)
async def test_discovery_rejects_other_devices(
    hass, ble, name, payload, connectable, reason
):
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "bluetooth"},
        data=discovery(name, payload, connectable),
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == reason
    ble[1].assert_not_called()


async def test_discovery_deduplicates_proxy_sources(hass, entry, ble):
    first = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "bluetooth"},
        data=discovery(source="proxy-1"),
    )
    second = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "bluetooth"},
        data=discovery(source="proxy-2"),
    )
    assert first["type"] == FlowResultType.FORM
    assert second["type"] == FlowResultType.ABORT
    assert second["reason"] == "already_in_progress"
    hass.config_entries.flow.async_abort(first["flow_id"])
    entry.add_to_hass(hass)
    third = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "bluetooth"},
        data=discovery(),
    )
    assert third["reason"] == "already_configured"
    ble[1].assert_not_called()


async def test_proxy_small_mtu_error(hass, peripheral, ble):
    from custom_components.lumi_curtain.transport import AUTH

    peripheral.characteristics[AUTH].max_write_without_response_size = 20
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "bluetooth"},
        data=discovery(),
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"ltmk": LTMK.hex()},
    )
    assert result["errors"] == {"base": "unsupported_mtu"}
    peripheral.client.disconnect.assert_awaited_once()


@pytest.mark.parametrize(
    ("language", "label", "title", "flow_title"),
    [
        ("zh-Hans", "LTMK（32 字节）", "连接 Aqara 窗帘", "Aqara 窗帘 DDEEFF"),  # noqa: RUF001
        (
            "en",
            "LTMK (32 bytes)",
            "Connect your Aqara curtain",
            "Aqara Curtain DDEEFF",
        ),
    ],
)
async def test_discovery_translations(hass, language, label, title, flow_title):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "bluetooth"}, data=discovery()
    )
    translations = await async_get_translations(hass, language, "config", {DOMAIN})
    prefix = f"component.{result['handler']}.config.step.{result['step_id']}"
    assert translations[f"{prefix}.data.ltmk"] == label
    assert translations[f"{prefix}.title"] == title
    assert ADDRESS in translations[f"{prefix}.description"].format(
        **result["description_placeholders"]
    )
    progress = hass.config_entries.flow.async_progress_by_handler(DOMAIN)[0]
    assert (
        translations[f"component.{DOMAIN}.config.flow_title"].format(
            **progress["context"]["title_placeholders"]
        )
        == flow_title
    )


@pytest.mark.parametrize("source", ["user", "bluetooth", "reauth"])
async def test_failed_connection_retains_credentials(hass, entry, ble, source):
    ble[0].return_value = None
    context = {"source": source}
    if source == "reauth":
        entry.add_to_hass(hass)
        context["entry_id"] = entry.entry_id
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context=context, data=discovery() if source == "bluetooth" else None
    )
    user_input = {"ltmk": LTMK.hex()}
    if source == "user":
        user_input["address"] = ADDRESS
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input
    )
    assert result["errors"] == {"base": "cannot_connect"}
    fields = {field.schema: field for field in result["data_schema"].schema}
    for key, value in user_input.items():
        assert fields[key].description["suggested_value"] == value
    assert result["data_schema"].schema[fields["ltmk"]].config["type"] == "password"
