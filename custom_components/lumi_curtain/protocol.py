"""MIOT v2 property codec for HMCN02 curtains."""

import struct

type Properties = dict[tuple[int, int], int]


class ProtocolError(Exception):
    """A malformed, rejected, or unauthenticated device exchange."""


class AuthenticationError(ProtocolError):
    """The device rejected the LTMK confirmation."""


class UnsupportedMTUError(ProtocolError):
    """The connection cannot carry the secure-login public key packet."""


def frame(tid: int, opcode: int, payload: bytes = b"") -> bytes:
    """Encode the v2 length/version header."""
    if len(payload) > 4090:
        raise ProtocolError("MIOT frame is too long")
    return struct.pack("<HHB", 0x2000 | (5 + len(payload)), tid, opcode) + payload


def get_payload(properties: tuple[tuple[int, int], ...]) -> bytes:
    """Encode read tuples, preserving their order."""
    return bytes([len(properties)]) + b"".join(
        struct.pack("<BH", *key) for key in properties
    )


def set_payload(piid: int, value: int) -> bytes:
    """Encode the supported uint8 motor or target-position command."""
    if (
        (piid == 1 and value not in (0, 1, 2))
        or (piid == 2 and not 0 <= value <= 100)
        or piid not in (1, 2)
    ):
        raise ValueError("Invalid curtain command")
    return struct.pack("<BBHHB", 1, 2, piid, 0x1001, value)


def header(data: bytes) -> tuple[int, int]:
    """Validate framing, allowing the device's off-by-one set ACK length."""
    if len(data) < 5:
        raise ProtocolError("Truncated MIOT header")
    control, tid, opcode = struct.unpack_from("<HHB", data)
    if control >> 12 != 2:
        raise ProtocolError("Unsupported MIOT version")
    declared = control & 0xFFF
    if declared != len(data) and not (opcode == 1 and declared == len(data) + 1):
        raise ProtocolError("Invalid MIOT frame length")
    return tid, opcode


def parse_properties(data: bytes) -> Properties:
    """Read successful integer properties; reject errors and partial messages."""
    _, opcode = header(data)
    if opcode not in (1, 3, 4) or len(data) < 6:
        raise ProtocolError("Expected a property response")
    values: Properties = {}
    offset = 6
    try:
        for _ in range(data[5]):
            siid, piid = struct.unpack_from("<BH", data, offset)
            offset += 3
            if opcode != 4:
                (code,) = struct.unpack_from("<H", data, offset)
                offset += 2
                if code:
                    raise ProtocolError(f"Property {siid}.{piid} rejected ({code})")
            if opcode == 1:
                values[siid, piid] = 0
                continue
            (value_header,) = struct.unpack_from("<H", data, offset)
            offset += 2
            kind, length = value_header >> 12, value_header & 0xFFF
            if offset + length > len(data):
                raise ProtocolError("Truncated MIOT value")
            # The supported curtain properties are uint8. Decode other integer
            # reports too; unknown/string properties are safely skipped.
            sizes = {0: 1, 1: 1, 2: 1, 3: 2, 4: 2, 5: 4, 6: 4, 7: 8, 8: 8}
            if kind in sizes:
                if length != sizes[kind]:
                    raise ProtocolError("Invalid MIOT integer size")
                values[siid, piid] = int.from_bytes(
                    data[offset : offset + length],
                    "little",
                    signed=kind in (2, 4, 6, 8),
                )
            offset += length
    except struct.error as err:
        raise ProtocolError("Truncated MIOT property") from err
    if offset != len(data):
        raise ProtocolError("Trailing MIOT property data")
    return values
