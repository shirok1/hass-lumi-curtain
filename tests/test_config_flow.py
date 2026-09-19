"""Config flow tests authenticate against the simulated BLE peripheral."""

from unittest.mock import patch

import pytest
from homeassistant.data_entry_flow import FlowResultType

from custom_components.lumi_curtain.const import DOMAIN

from .conftest import ADDRESS, LTMK


async def test_user_setup(hass, peripheral, ble):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] == FlowResultType.FORM
    with patch("custom_components.lumi_curtain.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "address": ADDRESS.lower().replace(":", "-"),
                "ltmk": LTMK.hex().upper(),
                "name": "Study",
            },
        )
        await hass.async_block_till_done()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Study"
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
