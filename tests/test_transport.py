"""Full encrypted conversations against an independently behaving peripheral."""

import asyncio
import struct
from unittest.mock import Mock, patch

import pytest
from bleak.exc import BleakError
from cryptography.hazmat.primitives.ciphers.aead import AESCCM

from custom_components.lumi_curtain.protocol import (
    AuthenticationError,
    ProtocolError,
    UnsupportedMTUError,
    get_payload,
    parse_properties,
    set_payload,
)
from custom_components.lumi_curtain.transport import (
    AUTH,
    NOTIFY,
    OK,
    READY,
    REKEY_AT,
    UPNP,
    WRITE,
    Session,
    nonce,
)

from .conftest import LTMK


@pytest.fixture
async def session(peripheral):
    session = Session(peripheral.client, Mock(), Mock())
    yield session
    await session.close()


@pytest.mark.parametrize("fragmented", [False, True])
async def test_login_control_and_notifications(peripheral, session, fragmented):
    peripheral.fragmented = fragmented
    peripheral.characteristics[WRITE].max_write_without_response_size = 12
    await session.login(LTMK)
    result = await session.request(2, get_payload(((2, 6), (2, 3))))
    assert parse_properties(result) == {(2, 6): 50, (2, 3): 0}
    for piid, value in [(1, 1), (1, 2), (1, 0), (2, 40)]:
        result = await session.request(0, set_payload((2, piid), value))
        assert parse_properties(result) == {(2, piid): 0}
    peripheral.push(6, 80)
    await asyncio.sleep(0)
    session.on_properties.assert_called_with({(2, 6): 80})
    assert peripheral.commands == [(1, 1), (1, 2), (1, 0), (2, 40)]
    assert session._keys == peripheral.keys


async def test_missing_controls_do_not_discard_public_key(peripheral, session):
    peripheral.controls = False
    with patch("custom_components.lumi_curtain.transport.CONTROL_WAIT", 0.001):
        await session.login(LTMK)
        await session.request(0, set_payload((2, 1), 0))
    assert peripheral.commands == [(1, 0)]


async def test_rejected_key(peripheral, session):
    peripheral.reject = True
    with pytest.raises(AuthenticationError):
        await session.login(LTMK)


@pytest.mark.parametrize("capacity", [20, 66, 67])
async def test_small_mtu_and_invalid_key(peripheral, session, capacity):
    with pytest.raises(ValueError):
        await session.login(b"wrong")
    peripheral.characteristics[AUTH].max_write_without_response_size = capacity
    with pytest.raises(UnsupportedMTUError):
        await session.login(LTMK)
    if capacity == 20:
        assert peripheral.client.start_notify.await_count == 4
    else:
        peripheral.client.start_notify.assert_not_called()


async def test_timeout_does_not_resend_motor_command(peripheral, session):
    await session.login(LTMK)
    peripheral.silent = True
    with (
        patch("custom_components.lumi_curtain.transport.TIMEOUT", 0.01),
        pytest.raises(TimeoutError),
    ):
        await session.request(0, set_payload((2, 1), 1))
    assert sum(pt[4] == 0 for pt in peripheral.plaintexts) == 1
    session.on_error.assert_called_once()


async def test_disconnect_unblocks_request(peripheral, session):
    await session.login(LTMK)
    peripheral.silent = True
    task = asyncio.create_task(session.request(2, get_payload(((2, 6),))))
    await asyncio.sleep(0)
    session.fail(BleakError("Disconnected"))
    with pytest.raises(BleakError):
        await task
    with pytest.raises(BleakError):
        await session.request(0xF0)


async def test_cancelled_request_invalidates_session(peripheral, session):
    await session.login(LTMK)
    peripheral.silent = True
    task = asyncio.create_task(session.request(0xF0))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    session.on_error.assert_called_once()


@pytest.mark.parametrize(
    "packet",
    [
        bytes.fromhex("000000000000"),
        bytes.fromhex("000000004100"),
        b"bad",
        bytes.fromhex("00000200ffff") + bytes(10),
    ],
)
async def test_bad_envelopes_and_ciphertext(peripheral, session, packet):
    await session.login(LTMK)
    peripheral.emit(NOTIFY, packet)
    await asyncio.sleep(0)
    session.on_error.assert_called_once()


async def test_replay_and_counter_exhaustion(peripheral, session):
    await session.login(LTMK)
    assert not session.needs_rekey
    session.tx_counter = 65536
    assert session.needs_rekey
    with pytest.raises(ProtocolError, match="exhausted"):
        await session.request(0xF0)
    peripheral.tx_counter = 0
    peripheral.push(6, 50)
    await asyncio.sleep(0)
    session.on_error.assert_called_once()
    await session.close()
    with pytest.raises(ProtocolError, match="closed"):
        await session.request(0xF0)


async def test_same_tid_notification_does_not_complete_request(peripheral, session):
    await session.login(LTMK)
    peripheral.silent = True
    task = asyncio.create_task(session.request(2, get_payload(((2, 6),))))
    await asyncio.sleep(0)
    peripheral.push(6, 90, tid=session._tid)
    await asyncio.sleep(0)
    assert not task.done()
    peripheral.respond(peripheral.plaintexts[-1])
    assert parse_properties(await task)[(2, 6)] == 50


async def test_fragment_sequence_and_partial_timeout(peripheral, session):
    await session.login(LTMK)
    peripheral.emit(NOTIFY, bytes.fromhex("000000000100"))
    peripheral.emit(NOTIFY, bytes.fromhex("0200aabb"))
    await asyncio.sleep(0)
    session.on_error.assert_called_once()


async def test_queue_overflow_and_auth_error(peripheral, session):
    for _ in range(129):
        session._notification(AUTH, bytearray(b"x"))
    session.on_error.assert_called_once()
    session._notification(AUTH, bytearray(b"ignored"))
    with pytest.raises(ProtocolError):
        await session._recv(AUTH)
    with pytest.raises(ProtocolError):
        await session._write(AUTH, b"x")


async def test_auth_error_wakes_waiters(session):
    task = asyncio.create_task(session._recv(AUTH))
    await asyncio.sleep(0)
    session._notification(UPNP, bytearray.fromhex("e0000000"))
    with pytest.raises(ProtocolError):
        await task


async def test_gatt_write_modes(peripheral, session):
    peripheral.characteristics[AUTH].properties = ["write"]
    assert session._capacity(AUTH) == 244
    await session._write(AUTH, b"x")
    assert peripheral.client.write_gatt_char.call_args.kwargs["response"] is True
    peripheral.characteristics[AUTH].properties = []
    with pytest.raises(ProtocolError, match="not writable"):
        await session._write(AUTH, b"x")
    del peripheral.characteristics[AUTH]
    with pytest.raises(ProtocolError, match="Missing"):
        await session._write(AUTH, b"x")
    with pytest.raises(ProtocolError, match="Missing"):
        session._capacity(AUTH)


@pytest.mark.parametrize("phase", ["ready", "chunk", "response"])
async def test_disconnect_at_each_request_phase(peripheral, session, phase):
    """A lost link wakes both the writer and any queued caller immediately."""
    await session.login(LTMK)
    peripheral.silent = True
    write = peripheral.client.write_gatt_char.side_effect
    reached = asyncio.Event()

    async def lose_link(uuid, data, response=False):
        if uuid == WRITE:
            is_envelope = data[:4] == bytes(4)
            if (phase == "ready" and is_envelope) or (
                phase == "chunk" and not is_envelope
            ):
                session.fail(BleakError("Disconnected"))
                reached.set()
                return
        await write(uuid, data, response)
        if phase == "response" and uuid == WRITE and data[:4] != bytes(4):
            session.fail(BleakError("Disconnected"))
            reached.set()

    peripheral.client.write_gatt_char.side_effect = lose_link
    first = asyncio.create_task(session.request(2, get_payload(((2, 6),))))
    await reached.wait()
    second = asyncio.create_task(session.request(0xF0))
    async with asyncio.timeout(0.2):
        results = await asyncio.gather(first, second, return_exceptions=True)
    assert all(isinstance(result, BleakError) for result in results)
    assert not session.is_ready
    session.on_error.assert_called_once()


async def test_disconnect_during_fragmented_notification(peripheral, session):
    """A partial encrypted response must not leave its waiter stuck until timeout."""
    await session.login(LTMK)
    peripheral.silent = True
    task = asyncio.create_task(session.request(2, get_payload(((2, 6),))))
    await asyncio.sleep(0)
    peripheral.emit(NOTIFY, bytes.fromhex("000000000200"))
    peripheral.emit(NOTIFY, bytes.fromhex("01000100aabb"))
    await asyncio.sleep(0)
    session.fail(BleakError("Disconnected"))
    async with asyncio.timeout(0.2):
        with pytest.raises(BleakError, match="Disconnected"):
            await task
    session.on_properties.assert_not_called()
    session.on_error.assert_called_once()


async def test_login_waits_for_delayed_device_ready(peripheral, session):
    """The peripheral gates its public-key parcel until READY, as Mi Home does."""
    write = peripheral.write
    ready = False
    delayed = None

    def allow_public_key():
        nonlocal ready
        ready = True
        peripheral.emit(AUTH, READY)

    async def gated_auth(uuid, data, response=False):
        nonlocal delayed
        if uuid == AUTH and data == bytes.fromhex("000000030100"):
            peripheral.auth_code = 3
            delayed = asyncio.get_running_loop().call_later(0.65, allow_public_key)
            return
        if uuid == AUTH and len(data) == 66 and not ready:
            # The peer discards premature data; fail immediately for a tight repro.
            session.fail(ProtocolError("Public key sent before device READY"))
            peripheral.client.is_connected = False
            return
        await write(uuid, data, response)

    peripheral.client.write_gatt_char.side_effect = gated_auth
    try:
        await session.login(LTMK)
        assert session.is_ready
    finally:
        if delayed:
            delayed.cancel()


@pytest.mark.parametrize(
    ("counter", "suffix"),
    [(0x7FFF, "ff7f0000"), (0x8000, "00800000"), (0xFFFF, "ffff0000")],
)
def test_nonce_uses_device_sdk_uint32_counter(counter, suffix):
    assert nonce(b"abcd", counter) == b"abcd" + bytes(4) + bytes.fromhex(suffix)


@pytest.mark.parametrize("counter", [0x7FFF, 0x8000, 0x8001, 0xFFFF])
async def test_encrypted_request_at_signed_counter_boundary(
    peripheral, session, counter
):
    """Decode outbound parcels with the device SDK uint32 nonce reference."""
    await session.login(LTMK)
    session.tx_counter = counter
    original_write = peripheral.write
    decoded = []

    async def read_encrypted_parcel(uuid, data, response=False):
        if uuid == WRITE and data[:2] == b"\x01\x00":
            wire_counter = int.from_bytes(data[2:4], "little")
            assert wire_counter == counter
            # Device SDK keeps bit 15 in the ordinary low word.
            peer_nonce = peripheral.keys[36:40] + bytes(4)
            peer_nonce += struct.pack("<I", wire_counter)
            plaintext = AESCCM(peripheral.keys[16:32], tag_length=4).decrypt(
                peer_nonce, data[4:], None
            )
            decoded.append(plaintext)
            peripheral.control(WRITE, OK)
            peripheral.respond(plaintext)
            return
        await original_write(uuid, data, response)

    peripheral.client.write_gatt_char.side_effect = read_encrypted_parcel
    result = await session.request(0xF0)
    assert decoded[0][4] == result[4] == 0xF0
    session.on_error.assert_not_called()


async def test_incoming_pushes_cross_signed_counter_boundary(peripheral, session):
    """Pushes cross bit 15 without needing a new session."""
    await session.login(LTMK)
    session.rx_counter = 0x7FFE
    for counter, position in [(0x7FFF, 80), (0x8000, 81), (0x8001, 82)]:
        peer_nonce = peripheral.keys[32:36] + bytes(4)
        peer_nonce += struct.pack("<I", counter)
        plaintext = bytes.fromhex("0c20f40104010206000110") + bytes([position])
        ciphertext = AESCCM(peripheral.keys[:16], tag_length=4).encrypt(
            peer_nonce, plaintext, None
        )
        peripheral.emit(
            NOTIFY, bytes.fromhex("00000200") + struct.pack("<H", counter) + ciphertext
        )
        await asyncio.sleep(0)
        session.on_properties.assert_called_with({(2, 6): position})
        assert session.rx_counter == counter
        session.on_error.assert_not_called()
    assert not session.needs_rekey


@pytest.mark.parametrize("counter", [0x7F00, 0x7FFF, 0x8000, 0x8001, 0xFFFE])
async def test_signed_counter_boundary_does_not_rekey(session, counter):
    session.tx_counter = session.rx_counter = counter
    assert not session.needs_rekey


@pytest.mark.parametrize(
    ("direction", "counter"), [("tx_counter", 0x10000), ("rx_counter", 0xFFFF)]
)
async def test_rekey_before_wire_counter_wrap(session, direction, counter):
    assert REKEY_AT == 0x10000
    setattr(session, direction, counter)
    assert session.needs_rekey


@pytest.mark.parametrize("reported_capacity", [20, 244])
async def test_greeting_settles_before_secure_login(
    peripheral, session, retry_sleep, reported_capacity
):
    """OEM greeting proves AUTH capacity and settles before CMD 0x50."""
    for uuid in (AUTH, WRITE):
        characteristic = peripheral.characteristics[uuid]
        characteristic.max_write_without_response_size = reported_capacity
    original = peripheral.write
    settled = False
    echoed = False
    original_pause = retry_sleep[1].side_effect

    async def observe_pause(delay):
        nonlocal settled
        if echoed and delay >= 0.1:
            settled = True
        await original_pause(delay)

    async def emulate_greeting(uuid, data, response=False):
        nonlocal echoed
        if uuid == UPNP and data == b"\xa4":
            peripheral.emit(AUTH, bytes.fromhex("00000400") + bytes([18]) * 16)
            peripheral.emit(AUTH, bytes.fromhex("00000401") + bytes([242]) * 240)
            return
        if uuid == AUTH and data[:4] == bytes.fromhex("00000501"):
            echoed = True
        if uuid == UPNP and data == bytes.fromhex("50000000"):
            assert settled, "Secure login sent before greeting settled"
        if uuid == WRITE and data[:4] == bytes(4):
            assert data[4:] == b"\x01\x00", "Peer expects one state-read parcel"
        await original(uuid, data, response)

    retry_sleep[1].side_effect = observe_pause
    peripheral.client.write_gatt_char.side_effect = emulate_greeting
    await session.login(LTMK)
    assert session.is_ready
    assert echoed
    result = await session.request(
        2, get_payload(((2, 6), (2, 3), (5, 1), (5, 2), (3, 16), (3, 11)))
    )
    assert parse_properties(result)[(2, 6)] == 50
    assert session._capacity(WRITE) == 244
    fresh_session = Session(peripheral.client, Mock(), Mock())
    assert fresh_session._capacity(WRITE) == reported_capacity
    peripheral.characteristics[WRITE].max_write_without_response_size = 12
    assert session._capacity(WRITE) == 12


@pytest.mark.parametrize(
    "challenge",
    [
        bytes.fromhex("00000401") + bytes([242]) * 239,
        bytes.fromhex("00000401") + bytes([241]) * 240,
        bytes.fromhex("00000402") + bytes([242]) * 240,
        bytes.fromhex("00000401") + bytes([242]) * 239 + b"\x00",
        bytes.fromhex("00000401") + bytes([18]) * 16,
    ],
)
async def test_unknown_capacity_needs_valid_large_greeting(
    peripheral, session, challenge
):
    peripheral.characteristics[AUTH].max_write_without_response_size = 20
    original = peripheral.write

    async def greet(uuid, data, response=False):
        if uuid == UPNP and data == b"\xa4":
            peripheral.emit(AUTH, bytes.fromhex("00000400") + bytes([18]) * 16)
            peripheral.emit(AUTH, challenge)
            return
        await original(uuid, data, response)

    peripheral.client.write_gatt_char.side_effect = greet
    with pytest.raises(UnsupportedMTUError):
        await session.login(LTMK)
    assert not any(
        c.args[0] == UPNP and c.args[1] == bytes.fromhex("50000000")
        for c in peripheral.client.write_gatt_char.await_args_list
    )
