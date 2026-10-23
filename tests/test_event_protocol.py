"""OEM v2 event framing and typed arguments."""

import struct

import pytest

from custom_components.lumi_curtain.protocol import ProtocolError, frame, parse_event


def test_event_without_arguments():
    assert parse_event(bytes.fromhex("092034120702010000")) == (2, 1, {})


def test_event_with_typed_arguments_and_wide_ids():
    payload = struct.pack("<BHB", 3, 0x1234, 3)
    payload += struct.pack("<HHh", 0x2345, 0x4002, -20)
    payload += struct.pack("<HH", 1, 0xA003) + "帘".encode()
    payload += struct.pack("<HHB", 2, 0x0001, 1)
    assert parse_event(frame(1, 7, payload)) == (
        3,
        0x1234,
        {0x2345: -20, 1: "帘", 2: 1},
    )


def test_unknown_argument_type_preserves_following_argument():
    payload = struct.pack("<BHBHH", 3, 6, 2, 1, 0xF002) + b"xx"
    payload += struct.pack("<HHB", 2, 0x1001, 42)
    assert parse_event(frame(1, 7, payload)) == (3, 6, {2: 42})


@pytest.mark.parametrize(
    "data",
    [
        b"",
        frame(1, 4, bytes(4)),
        frame(1, 7, bytes(3)),
        frame(1, 7, bytes.fromhex("03010001")),
        frame(1, 7, bytes.fromhex("030100010100")),
        frame(1, 7, bytes.fromhex("030100010100021001")),
        frame(1, 7, bytes.fromhex("030100010100024001")),
        frame(1, 7, bytes.fromhex("03010000ff")),
        bytes.fromhex("0a2001000703010000"),
    ],
)
def test_malformed_event_is_rejected(data):
    with pytest.raises(ProtocolError):
        parse_event(data)
