"""MIoT v2 property and event codec, independent of service definitions."""

import struct
from contextlib import suppress

type Properties = dict[tuple[int, int], int]
type PropertyValues = dict[tuple[int, int], int | str]
type Event = tuple[int, int, dict[int, int | str]]


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


def set_payload(key: tuple[int, int], value: int | bool) -> bytes:
    """Encode one bool or uint8 property write."""
    if not 0 <= value <= 255:
        raise ValueError("Value is not uint8")
    return struct.pack(
        "<BBHHB", 1, *key, 0x0001 if isinstance(value, bool) else 0x1001, value
    )


def action_payload(key: tuple[int, int]) -> bytes:
    """Encode an action with no input parameters."""
    return bytes([*key, 0])


def parse_action_result(data: bytes) -> None:
    """Require success and no output values for an output-free action."""
    _, opcode = header(data)
    if opcode != 6 or len(data) not in (7, 8):
        raise ProtocolError("Invalid action acknowledgement")
    code = int.from_bytes(data[5:7], "little")
    if code:
        raise ProtocolError(f"Action rejected ({code})")
    if len(data) == 8 and data[7] != 0:
        raise ProtocolError("Unexpected action output")


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


def parse_properties(data: bytes) -> PropertyValues:
    """Read successful integers; reject failed writes and malformed messages."""
    _, opcode = header(data)
    if opcode not in (1, 3, 4) or len(data) < 6:
        raise ProtocolError("Expected a property response")
    values: PropertyValues = {}
    offset = 6
    try:
        for _ in range(data[5]):
            siid, piid = struct.unpack_from("<BH", data, offset)
            offset += 3
            if opcode != 4:
                (code,) = struct.unpack_from("<H", data, offset)
                offset += 2
                if code:
                    if opcode == 3:
                        # Failed reads contain no value. Other properties in
                        # this response can still be useful to the caller.
                        continue
                    raise ProtocolError(f"Property {siid}.{piid} rejected ({code})")
            if opcode == 1:
                values[siid, piid] = 0
                continue
            value, offset = _parse_value(data, offset)
            if value is not None:
                values[siid, piid] = value
    except struct.error as err:
        raise ProtocolError("Truncated MIOT property") from err
    if offset != len(data):
        raise ProtocolError("Trailing MIOT property data")
    return values


def _parse_value(data: bytes, offset: int) -> tuple[int | str | None, int]:
    """Consume a typed value, skipping unsupported types by declared length."""
    (value_header,) = struct.unpack_from("<H", data, offset)
    offset += 2
    kind, length = value_header >> 12, value_header & 0xFFF
    end = offset + length
    if end > len(data):
        raise ProtocolError("Truncated MIOT value")
    sizes = {0: 1, 1: 1, 2: 1, 3: 2, 4: 2, 5: 4, 6: 4, 7: 8, 8: 8}
    if kind in sizes:
        if length != sizes[kind]:
            raise ProtocolError("Invalid MIOT integer size")
        return int.from_bytes(
            data[offset:end], "little", signed=kind in (2, 4, 6, 8)
        ), end
    if kind == 10:
        with suppress(UnicodeDecodeError):
            return data[offset:end].decode("utf-8"), end
    return None, end


def parse_event(data: bytes) -> Event:
    """Decode one v2 event without interpreting its service definitions."""
    _, opcode = header(data)
    if opcode != 7 or len(data) < 9:
        raise ProtocolError("Expected a MIOT event")
    siid, eiid, count = struct.unpack_from("<BHB", data, 5)
    values: dict[int, int | str] = {}
    offset = 9
    try:
        for _ in range(count):
            (piid,) = struct.unpack_from("<H", data, offset)
            value, offset = _parse_value(data, offset + 2)
            if value is not None:
                values[piid] = value
    except struct.error as err:
        raise ProtocolError("Truncated MIOT event") from err
    if offset != len(data):
        raise ProtocolError("Trailing MIOT event data")
    return siid, eiid, values
