"""Protocol test vectors and malformed inputs."""

import pytest

from custom_components.lumi_curtain.protocol import (
    ProtocolError,
    frame,
    get_payload,
    header,
    parse_properties,
    set_payload,
)


@pytest.mark.parametrize(
    ("actual", "expected"),
    [
        (frame(2, 0xF0), "05200200f0"),
        (frame(9, 2, get_payload(((2, 6),))), "092009000201020600"),
        (frame(10, 0, set_payload((2, 1), 1)), "0c200a000001020100011001"),
        (frame(11, 0, set_payload((2, 1), 2)), "0c200b000001020100011002"),
        (frame(10, 0, set_payload((2, 1), 0)), "0c200a000001020100011000"),
        (frame(12, 0, set_payload((2, 2), 50)), "0c200c000001020200011032"),
    ],
)
def test_request_vectors(actual, expected):
    assert actual.hex() == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "362003000306030500000001100102030000000110000206000000011064030800000001100d0317000000011000030a000000011001",
            {(3, 5): 1, (2, 3): 0, (2, 6): 100, (3, 8): 13, (3, 23): 0, (3, 10): 1},
        ),
        ("16200400030205010000000110450204000000011000", {(5, 1): 69, (2, 4): 0}),
        ("0c2007000401020600011064", {(2, 6): 100}),
        ("0b20050001010314000000", {(3, 20): 0}),
        ("0c20060001010201000000", {(2, 1): 0}),
    ],
)
def test_response_vectors(raw, expected):
    assert parse_properties(bytes.fromhex(raw)) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "05200200f0",
        "092001000401020600",
        "0c2001000401020600021064",
        "0c200100040102060002106400",
        "0c2001000401020600013064",
        "0d200100040102060001106400",
        "0b20010001010201000100",
        "0b10010001010201000000",
        "0e20010001010201000000",
    ],
)
def test_malformed_frames(raw):
    with pytest.raises(ProtocolError):
        parse_properties(bytes.fromhex(raw))


def test_unknown_property_type_and_signed_value():
    assert parse_properties(frame(1, 4, bytes.fromhex("0102060001b041"))) == {}
    assert parse_properties(frame(1, 4, bytes.fromhex("010206000120ff"))) == {
        (2, 6): -1
    }
    with pytest.raises(ProtocolError):
        frame(1, 1, bytes(4091))
    assert header(frame(65535, 0xF0)) == (65535, 0xF0)


@pytest.mark.parametrize("value", [-1, 256])
def test_uint8_write_rejects_out_of_range_values(value):
    with pytest.raises(ValueError):
        set_payload((2, 1), value)


def test_partial_read_and_signed_temperature():
    # A failed read has only its key and status; the next tuple starts immediately.
    payload = bytes.fromhex("0305010001000310000000024038ff030b000000011002")
    assert parse_properties(frame(1, 3, payload)) == {(3, 16): -200, (3, 11): 2}
