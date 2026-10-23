"""OEM value/action wire vectors and encrypted, non-replayed feature controls."""

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.lumi_curtain.protocol import (
    ProtocolError,
    action_payload,
    frame,
    parse_action_result,
    parse_properties,
    set_payload,
)

from .test_features import setup_features


def test_bool_string_and_action_wire_vectors():
    assert frame(1, 0, set_payload((2, 7), True)).hex() == "0c2001000001020700010001"
    assert frame(2, 5, action_payload((4, 1))).hex() == "0820020005040100"
    # A UTF-8 string followed by a uint8: length counts bytes, not characters.
    assert parse_properties(
        bytes.fromhex("182001000302010100000003a0e5b8950206000000011063")
    ) == {(1, 1): "帕", (2, 6): 99}
    parse_action_result(bytes.fromhex("07200200060000"))
    parse_action_result(bytes.fromhex("0820020006000000"))


@pytest.mark.parametrize(
    "raw",
    [
        "062001000600",
        "07200100060100",
        "0820010006000001",
        "092001000600000000",
        "07200100010000",
    ],
)
def test_rejected_or_malformed_action_result(raw):
    with pytest.raises(ProtocolError):
        parse_action_result(bytes.fromhex(raw))


def test_invalid_utf8_metadata_does_not_hide_valid_state():
    assert parse_properties(
        frame(1, 3, bytes.fromhex("02010100000001a0ff0206000000011063"))
    ) == {(2, 6): 99}


async def test_encrypted_feature_controls_and_action_rejection(
    hass, entry, peripheral, ble
):
    await setup_features(hass, entry)
    coordinator = entry.runtime_data
    await coordinator.async_command((2, 7), True)
    assert peripheral.property_writes[-1] == ((2, 7), 1, 1)
    await coordinator.async_command((3, 4), 1)
    assert peripheral.property_writes[-1] == ((3, 4), 1, 0x1001)
    await coordinator.async_refresh()
    assert coordinator.data[3, 4] == 1
    await coordinator.async_action((4, 1))
    assert peripheral.actions == [(4, 1)]
    assert peripheral.commands == []
    with pytest.raises(ValueError):
        await coordinator.async_command((2, 7), 1)
    with pytest.raises(ValueError):
        await coordinator.async_action((2, 1))
    peripheral.action_code = 7
    with pytest.raises(HomeAssistantError):
        await coordinator.async_action((4, 1))
    assert peripheral.actions == [(4, 1), (4, 1)]
    assert not coordinator.last_update_success
    await hass.config_entries.async_unload(entry.entry_id)
