"""Full encrypted conversations against an independently behaving peripheral."""

import asyncio
from unittest.mock import Mock, patch

import pytest
from bleak.exc import BleakError

from custom_components.lumi_curtain.protocol import (
    AuthenticationError,
    ProtocolError,
    UnsupportedMTUError,
    get_payload,
    parse_properties,
    set_payload,
)
from custom_components.lumi_curtain.transport import AUTH, NOTIFY, UPNP, WRITE, Session

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
        result = await session.request(0, set_payload(piid, value))
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
        await session.request(0, set_payload(1, 0))
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
    peripheral.client.start_notify.assert_not_called()


async def test_timeout_does_not_resend_motor_command(peripheral, session):
    await session.login(LTMK)
    peripheral.silent = True
    with (
        patch("custom_components.lumi_curtain.transport.TIMEOUT", 0.01),
        pytest.raises(TimeoutError),
    ):
        await session.request(0, set_payload(1, 1))
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
