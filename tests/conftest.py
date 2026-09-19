"""A simulated peripheral at the Bleak boundary; real HA and real crypto."""

import asyncio
import struct
import zlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESCCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.lumi_curtain.const import DOMAIN
from custom_components.lumi_curtain.transport import (
    AUTH,
    NOTIFY,
    OK,
    READY,
    UPNP,
    VENDOR,
    WRITE,
)

ADDRESS = "AA:BB:CC:DD:EE:FF"
LTMK = bytes(range(32))  # Synthetic test credential.


@pytest.fixture(autouse=True)
def custom_integrations(enable_custom_integrations):
    """Use the actual custom integration loader."""


@pytest.fixture(autouse=True)
def retry_sleep():
    """Observe backoff without slowing the simulated Bluetooth conversations."""

    async def pause(_delay):
        await asyncio.sleep(0)

    with (
        patch(
            "custom_components.lumi_curtain.coordinator.sleep", side_effect=pause
        ) as connect_sleep,
        patch(
            "custom_components.lumi_curtain.transport.sleep", side_effect=pause
        ) as auth_sleep,
    ):
        yield connect_sleep, auth_sleep


class Peripheral:
    """Emulate the secure handshake and encrypted curtain responses."""

    def __init__(self):
        self.characteristics = {
            uuid: MagicMock(
                max_write_without_response_size=244,
                properties=["write-without-response", "notify"],
            )
            for uuid in (UPNP, AUTH, WRITE, NOTIFY, VENDOR)
        }
        self.new_client()
        self.callbacks = {}
        self.private = ec.generate_private_key(ec.SECP256R1())
        self.public = self.private.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )[1:]
        self.keys = b""
        self.auth_code = 0
        self.parts = []
        self.count = 0
        self.counter = 0
        self.tx_counter = 0
        self.position = 50
        self.status = 0
        self.commands = []
        self.plaintexts = []
        self.controls = True
        self.fragmented = False
        self.silent = False
        self.reject = False
        self.command_code = 0
        self.get_response = None

    def new_client(self, callback=None):
        client = MagicMock()
        client.is_connected = True
        client.mtu_size = 247
        client.services.get_characteristic.side_effect = self.characteristics.get
        client.start_notify = AsyncMock(side_effect=self.subscribe)
        client.write_gatt_char = AsyncMock(side_effect=self.write)

        async def disconnect():
            client.is_connected = False
            if callback:
                callback(client)

        client.disconnect = AsyncMock(side_effect=disconnect)
        self.client = client

    async def subscribe(self, uuid, callback):
        self.callbacks[uuid] = callback

    def emit(self, uuid, value):
        self.callbacks[uuid](None, bytearray(value))

    def control(self, uuid, value):
        if self.controls:
            self.emit(uuid, value)

    async def disconnect(self):
        await self.client.disconnect()

    async def write(self, uuid, data, response=False):
        if uuid == UPNP and data == b"\xa4":
            self.emit(AUTH, bytes.fromhex("00000401aabb"))
            self.emit(AUTH, bytes.fromhex("00000402ccdd"))
        elif uuid == AUTH:
            if len(data) == 6 and data[:3] == bytes(3):
                self.auth_code = data[3]
                self.control(AUTH, READY)
            elif data[:2] == b"\x01\x00":
                if self.auth_code == 3:
                    assert len(data) == 66
                    peer = ec.EllipticCurvePublicKey.from_encoded_point(
                        ec.SECP256R1(), b"\x04" + data[2:]
                    )
                    self.keys = HKDF(
                        algorithm=hashes.SHA256(),
                        length=64,
                        salt=b"miot-mesh-login-salt",
                        info=b"miot-mesh-login-info",
                    ).derive(self.private.exchange(ec.ECDH(), peer) + LTMK)
                    self.control(AUTH, OK)
                    self.emit(AUTH, bytes.fromhex("00000203") + self.public)
                else:
                    if not self.reject:
                        assert AESCCM(self.keys[16:32], tag_length=4).decrypt(
                            bytes(range(16, 28)), data[2:], None
                        ) == struct.pack("<I", zlib.crc32(self.public))
                    self.control(AUTH, OK)
                    self.emit(
                        UPNP, bytes.fromhex("52000000" if self.reject else "51000000")
                    )
        elif uuid == WRITE:
            if data[:4] == bytes(4):
                self.count = int.from_bytes(data[4:6], "little")
                self.parts = []
                self.control(WRITE, READY)
            else:
                index = int.from_bytes(data[:2], "little")
                assert index == len(self.parts) + 1
                if index == 1:
                    self.counter = int.from_bytes(data[2:4], "little")
                self.parts.append(data[4:] if index == 1 else data[2:])
                if index == self.count:
                    self.control(WRITE, OK)
                    plaintext = AESCCM(self.keys[16:32], tag_length=4).decrypt(
                        self.keys[36:40] + bytes(4) + struct.pack("<I", self.counter),
                        b"".join(self.parts),
                        None,
                    )
                    self.plaintexts.append(plaintext)
                    if not self.silent:
                        self.respond(plaintext)

    def respond(self, request):
        tid = request[2:4]
        op = request[4]
        if op == 0xF0:
            response = bytes.fromhex("0520") + tid + b"\xf0"
        elif op == 2:
            # Captured v2 get layout; the fake never imports the product codec.
            payload = bytes.fromhex("030202060000000110") + bytes([self.position])
            payload += bytes.fromhex("02030000000110") + bytes([self.status])
            response = struct.pack("<H", 0x2000 | (4 + len(payload))) + tid + payload
            if self.get_response is not None:
                response = self.get_response[:2] + tid + self.get_response[4:]
        else:
            piid, value = int.from_bytes(request[7:9], "little"), request[11]
            self.commands.append((piid, value))
            if not self.command_code:
                if piid == 1:
                    self.status = value
                else:
                    self.position = value
            # The device's motor ACK declares 12 but contains 11 bytes.
            response = bytes.fromhex("0c20") + tid + b"\x01\x01\x02"
            response += struct.pack("<HH", piid, self.command_code)
        self.send_plaintext(response)

    def send_plaintext(self, plaintext):
        counter = self.tx_counter
        self.tx_counter += 1
        ciphertext = AESCCM(self.keys[:16], tag_length=4).encrypt(
            self.keys[32:36] + bytes(4) + struct.pack("<I", counter), plaintext, None
        )
        if self.fragmented:
            chunks = [ciphertext[i : i + 10] for i in range(0, len(ciphertext), 10)]
            self.emit(NOTIFY, bytes(4) + struct.pack("<H", len(chunks)))
            for index, chunk in enumerate(chunks, 1):
                prefix = struct.pack("<H", index)
                if index == 1:
                    prefix += struct.pack("<H", counter)
                self.emit(NOTIFY, prefix + chunk)
        else:
            self.emit(
                NOTIFY,
                bytes.fromhex("00000200") + struct.pack("<H", counter) + ciphertext,
            )

    def push(self, piid, value, tid=500):
        self.send_plaintext(
            bytes.fromhex("0c20")
            + struct.pack("<H", tid)
            + b"\x04\x01\x02"
            + struct.pack("<H", piid)
            + b"\x01\x10"
            + bytes([value])
        )


@pytest.fixture
def peripheral():
    return Peripheral()


@pytest.fixture
def entry():
    return MockConfigEntry(
        domain=DOMAIN,
        title="Living room",
        unique_id=ADDRESS,
        data={"address": ADDRESS, "ltmk": LTMK.hex()},
    )


@pytest.fixture
def ble(peripheral):
    async def connect(cls, device, name, **kwargs):
        peripheral.new_client(kwargs["disconnected_callback"])
        peripheral.tx_counter = 0
        return peripheral.client

    with (
        patch(
            "custom_components.lumi_curtain.coordinator.bluetooth.async_ble_device_from_address",
            return_value=MagicMock(),
        ) as lookup,
        patch(
            "custom_components.lumi_curtain.coordinator.establish_connection",
            side_effect=connect,
        ) as connect_mock,
        patch(
            "custom_components.lumi_curtain.coordinator.bluetooth.async_process_advertisements",
            side_effect=TimeoutError,
        ) as wait_for_advertisement,
    ):
        yield lookup, connect_mock, wait_for_advertisement
