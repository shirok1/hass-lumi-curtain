"""Recovery budgets, live-link reuse, and single-send motor commands."""

import asyncio
from unittest.mock import AsyncMock, Mock, call, patch

import pytest
from bleak.exc import BleakError
from homeassistant.exceptions import HomeAssistantError

from custom_components.lumi_curtain.coordinator import CurtainCoordinator
from custom_components.lumi_curtain.protocol import AuthenticationError, ProtocolError
from custom_components.lumi_curtain.transport import AUTH, NOTIFY, UPNP, WRITE, Session

from .conftest import ADDRESS, LTMK
from .test_config_flow import discovery


async def test_wait_for_connectable_proxy_advertisement(hass, ble):
    coordinator = CurtainCoordinator(hass, ADDRESS, LTMK.hex())
    ble[0].return_value = None
    ble[2].side_effect = None
    advertisement = discovery()
    ble[2].return_value = advertisement
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert ble[1].call_args.args[1] is advertisement.device
    args = ble[2].call_args.args
    assert args[1](advertisement)
    assert args[2] == {"address": ADDRESS, "connectable": True}
    assert args[4] == 20
    await coordinator.async_shutdown()


async def test_connect_third_attempt_and_concurrent_reuse(
    hass, peripheral, ble, retry_sleep
):
    coordinator = CurtainCoordinator(hass, ADDRESS, LTMK.hex())
    connect = ble[1].side_effect
    attempts = 0

    async def fail_twice(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise BleakError("Busy")
        return await connect(*args, **kwargs)

    ble[1].side_effect = fail_twice
    await asyncio.gather(
        coordinator._async_update_data(), coordinator._async_update_data()
    )
    assert ble[1].await_count == 3
    assert all(c.kwargs["max_attempts"] == 1 for c in ble[1].await_args_list)
    assert retry_sleep[0].await_args_list == [call(2), call(4)]
    assert len(peripheral.plaintexts) == 3  # One ping and two reads.
    await coordinator.async_shutdown()


async def test_subscription_retries_only_failed_characteristic(peripheral, retry_sleep):
    session = Session(peripheral.client, Mock(), Mock())
    subscribe = peripheral.subscribe
    failures = 0

    async def fail_twice(uuid, callback):
        nonlocal failures
        if uuid == AUTH and failures < 2:
            failures += 1
            raise BleakError("Subscription busy")
        await subscribe(uuid, callback)

    peripheral.client.start_notify.side_effect = fail_twice
    await session.login(LTMK)
    assert [c.args[0] for c in peripheral.client.start_notify.await_args_list] == [
        UPNP,
        AUTH,
        AUTH,
        AUTH,
        WRITE,
        NOTIFY,
    ]
    assert retry_sleep[1].await_args_list[:2] == [call(2), call(4)]
    await session.close()


async def test_auth_retry_fresh_keys_without_reconnecting(
    hass, peripheral, ble, retry_sleep
):
    coordinator = CurtainCoordinator(hass, ADDRESS, LTMK.hex())
    write = peripheral.write
    keys = []

    async def reject_exchange_twice(uuid, data, response=False):
        if uuid == AUTH and data[:2] == b"\x01\x00" and len(data) == 66:
            keys.append(data[2:])
            if len(keys) < 3:
                # Reject this exchange and leave stale data that must be discarded.
                peripheral.emit(UPNP, bytes.fromhex("e0000000"))
                return
        await write(uuid, data, response)

    peripheral.write = reject_exchange_twice
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert ble[1].await_count == 1
    assert len(set(keys)) == 3
    assert peripheral.client.start_notify.await_count == 4
    assert [c.args[0] for c in retry_sleep[1].await_args_list if c.args[0] >= 2] == [
        2,
        4,
    ]
    await coordinator.async_shutdown()


async def test_disconnect_during_auth_cools_down_and_reconnects(
    hass, peripheral, ble, retry_sleep
):
    coordinator = CurtainCoordinator(hass, ADDRESS, LTMK.hex())
    write = peripheral.write
    dropped = False

    async def drop_once(uuid, data, response=False):
        nonlocal dropped
        if uuid == UPNP and data == b"\xa4" and not dropped:
            dropped = True
            await peripheral.disconnect()
            return
        await write(uuid, data, response)

    peripheral.write = drop_once
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert ble[1].await_count == 2
    retry_sleep[0].assert_awaited_once_with(pytest.approx(20, abs=0.1))
    await coordinator.async_shutdown()


async def test_failed_read_reconnects_once(hass, peripheral, ble, retry_sleep):
    coordinator = CurtainCoordinator(hass, ADDRESS, LTMK.hex())
    respond = peripheral.respond
    reads = 0

    def lose_first_read(request):
        nonlocal reads
        if request[4] == 2:
            reads += 1
            if reads == 1:
                return
        respond(request)

    peripheral.respond = lose_first_read
    with patch("custom_components.lumi_curtain.transport.TIMEOUT", 0.01):
        await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert reads == 2
    assert ble[1].await_count == 2
    retry_sleep[0].assert_awaited_once_with(pytest.approx(20, abs=0.1))
    await coordinator.async_shutdown()


async def test_final_login_failure_preserves_cooldown_for_next_refresh(
    hass, ble, retry_sleep
):
    coordinator = CurtainCoordinator(hass, ADDRESS, LTMK.hex())
    with patch.object(Session, "login", side_effect=ProtocolError("Busy")):
        await coordinator.async_refresh()
    assert not coordinator.last_update_success
    assert ble[1].await_count == 3
    assert retry_sleep[0].await_count == 2

    retry_sleep[0].reset_mock()
    await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert ble[1].await_count == 4
    retry_sleep[0].assert_awaited_once_with(pytest.approx(20, abs=0.1))
    await coordinator.async_shutdown()


@pytest.mark.parametrize("phase", ["subscription", "authentication"])
async def test_login_retry_exhaustion(peripheral, retry_sleep, phase):
    session = Session(peripheral.client, Mock(), Mock())
    if phase == "subscription":
        peripheral.client.start_notify.side_effect = BleakError("Busy")
        operation = peripheral.client.start_notify
    else:
        session._authenticate = AsyncMock(side_effect=ProtocolError("Busy"))
        operation = session._authenticate
    with pytest.raises((BleakError, ProtocolError)):
        await session.login(LTMK)
    assert operation.await_count == 3
    assert retry_sleep[1].await_args_list == [call(2), call(4)]
    await session.close()


@pytest.mark.parametrize("phase", ["subscription", "authentication"])
async def test_login_timeout_is_bounded(peripheral, phase):
    session = Session(peripheral.client, Mock(), Mock())

    async def hang(*_args):
        await asyncio.Future()

    if phase == "subscription":
        peripheral.client.start_notify.side_effect = hang
        timeout = "SUBSCRIBE_TIMEOUT"
    else:
        session._authenticate = AsyncMock(side_effect=hang)
        timeout = "LOGIN_TIMEOUT"
    with (
        patch(f"custom_components.lumi_curtain.transport.{timeout}", 0.001),
        pytest.raises(TimeoutError),
    ):
        await session.login(LTMK)
    assert session._reader is None
    await session.close()


async def test_explicit_key_rejection_never_retries(peripheral, retry_sleep):
    peripheral.reject = True
    session = Session(peripheral.client, Mock(), Mock())
    with pytest.raises(AuthenticationError):
        await session.login(LTMK)
    assert not [c for c in retry_sleep[1].await_args_list if c.args[0] >= 2]
    await session.close()


async def test_motor_timeout_never_replays(hass, peripheral, ble):
    coordinator = CurtainCoordinator(hass, ADDRESS, LTMK.hex())
    await coordinator.async_refresh()
    peripheral.silent = True
    with (
        patch("custom_components.lumi_curtain.transport.TIMEOUT", 0.01),
        pytest.raises(HomeAssistantError),
    ):
        await coordinator.async_command((2, 1), 1)
    assert sum(pt[4] == 0 for pt in peripheral.plaintexts) == 1
    assert ble[1].await_count == 1
    await coordinator.async_shutdown()


async def test_cancel_auth_backoff_releases_link(hass, peripheral, ble, retry_sleep):
    coordinator = CurtainCoordinator(hass, ADDRESS, LTMK.hex())
    retry_sleep[1].side_effect = asyncio.CancelledError
    with (
        patch.object(Session, "_authenticate", side_effect=ProtocolError("Busy")),
        pytest.raises(asyncio.CancelledError),
    ):
        await coordinator._async_update_data()
    retry_sleep[1].assert_awaited_once_with(2)
    assert coordinator._session is None
    peripheral.client.disconnect.assert_awaited_once()
    await coordinator.async_shutdown()


async def test_connection_attempt_timeouts(hass, ble, retry_sleep):
    coordinator = CurtainCoordinator(hass, ADDRESS, LTMK.hex())

    async def hang(*_args, **_kwargs):
        await asyncio.Future()

    ble[1].side_effect = hang
    with patch("custom_components.lumi_curtain.coordinator.CONNECT_TIMEOUT", 0.001):
        await coordinator.async_refresh()
    assert not coordinator.last_update_success
    assert ble[1].await_count == 3
    assert retry_sleep[0].await_args_list == [call(2), call(4)]
    await coordinator.async_shutdown()


@pytest.mark.parametrize("budget", ["CONNECT_BUDGET", "UPDATE_BUDGET"])
async def test_total_budget_cancels_login_and_cleans_up(hass, peripheral, ble, budget):
    coordinator = CurtainCoordinator(hass, ADDRESS, LTMK.hex())

    async def hang(*_args):
        await asyncio.Future()

    with (
        patch.object(Session, "login", side_effect=hang),
        patch(f"custom_components.lumi_curtain.coordinator.{budget}", 0.01),
    ):
        await coordinator.async_refresh()
    assert not coordinator.last_update_success
    assert coordinator._session is None
    peripheral.client.disconnect.assert_awaited_once()
    await coordinator.async_shutdown()


async def test_shutdown_flag_prevents_next_connection(hass, ble, retry_sleep):
    coordinator = CurtainCoordinator(hass, ADDRESS, LTMK.hex())
    ble[1].side_effect = BleakError("Busy")

    async def stop_during_backoff(_delay):
        coordinator._stopping = True

    retry_sleep[0].side_effect = stop_during_backoff
    await coordinator.async_refresh()
    assert not coordinator.last_update_success
    assert ble[1].await_count == 1
    await coordinator.async_shutdown()
