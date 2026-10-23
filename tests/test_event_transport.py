"""Events share encrypted transport but never complete property requests."""

import asyncio
from unittest.mock import Mock

import pytest

from custom_components.lumi_curtain.protocol import frame
from custom_components.lumi_curtain.transport import Session

from .conftest import LTMK


@pytest.mark.parametrize("fragmented", [False, True])
async def test_encrypted_events_preserve_occurrences(peripheral, fragmented):
    on_event = Mock()
    on_properties, on_error = Mock(), Mock()
    session = Session(peripheral.client, on_properties, on_error, on_event)
    try:
        await session.login(LTMK)
        peripheral.fragmented = fragmented
        event = frame(23, 7, bytes.fromhex("03060000"))
        peripheral.send_plaintext(event)
        peripheral.send_plaintext(event)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert on_event.call_count == 2
        on_event.assert_called_with((3, 6, {}))
        on_properties.assert_not_called()
        on_error.assert_not_called()
    finally:
        await session.close()


async def test_same_tid_event_does_not_complete_pending_request(peripheral):
    on_event = Mock()
    on_properties, on_error = Mock(), Mock()
    session = Session(peripheral.client, on_properties, on_error, on_event)
    try:
        await session.login(LTMK)
        future = asyncio.get_running_loop().create_future()
        session._pending = (23, 3, future)
        peripheral.send_plaintext(frame(23, 7, bytes.fromhex("02010000")))
        await asyncio.sleep(0)
        assert not future.done()
        on_event.assert_called_once_with((2, 1, {}))
        session._pending = None
        future.cancel()
    finally:
        await session.close()


async def test_event_without_callback_and_malformed_event(peripheral):
    on_error = Mock()
    session = Session(peripheral.client, Mock(), on_error)
    try:
        await session.login(LTMK)
        peripheral.send_plaintext(frame(1, 7, bytes.fromhex("02010000")))
        await asyncio.sleep(0)
        on_error.assert_not_called()
        peripheral.send_plaintext(frame(2, 7, bytes.fromhex("02010001")))
        await asyncio.sleep(0)
        on_error.assert_called_once()
    finally:
        await session.close()
