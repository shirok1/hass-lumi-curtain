"""Authenticated HMCN02 BLE session, independent of Home Assistant."""

import asyncio
import struct
import zlib
from asyncio import sleep
from collections.abc import Callable
from contextlib import suppress

from bleak import BleakClient
from bleak.exc import BleakError
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESCCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .protocol import (
    AuthenticationError,
    Event,
    PropertyValues,
    ProtocolError,
    UnsupportedMTUError,
    frame,
    header,
    parse_event,
    parse_properties,
)

UPNP = "00000010-0000-1000-8000-00805f9b34fb"
AUTH = "00000016-0000-1000-8000-00805f9b34fb"
WRITE = "0000001a-0000-1000-8000-00805f9b34fb"
NOTIFY = "0000001b-0000-1000-8000-00805f9b34fb"
VENDOR = "0000001c-0000-1000-8000-00805f9b34fb"
READY = bytes.fromhex("00000101")
OK = bytes.fromhex("00000100")
ACK = bytes.fromhex("00000300")
TIMEOUT = 30
CONTROL_WAIT = 6
RETRY_DELAYS = (2, 4)
LOGIN_TIMEOUT = 60
SUBSCRIBE_TIMEOUT = 5
# Keep one session across bit 15; re-key before the 16-bit wire counter wraps.
REKEY_AT = 0x10000


def derive_keys(shared: bytes, ltmk: bytes) -> bytes:
    """Derive device/app AES keys and IVs using the mesh-login KDF."""
    return HKDF(
        algorithm=hashes.SHA256(),
        length=64,
        salt=b"miot-mesh-login-salt",
        info=b"miot-mesh-login-info",
    ).derive(shared + ltmk)


def nonce(iv: bytes, counter: int) -> bytes:
    """Build the device SDK nonce with an unsigned 32-bit little-endian counter."""
    return iv + bytes(4) + counter.to_bytes(4, "little")


class Session:
    """One connection, one fresh ECDH key, and serialized MIOT requests."""

    def __init__(
        self,
        client: BleakClient,
        on_properties: Callable[[PropertyValues], None],
        on_error: Callable[[Exception], None],
        on_event: Callable[[Event], None] | None = None,
    ) -> None:
        self.client = client
        self.on_properties = on_properties
        self.on_error = on_error
        self.on_event = on_event
        self.tx_counter = 0
        self.rx_counter = -1
        self._att_capacity = 0
        self._keys = b""
        self._tid = 0
        self._queues: dict[str, asyncio.Queue[bytes | Exception]] = {
            uuid: asyncio.Queue(maxsize=128)
            for uuid in (
                UPNP,
                AUTH,
                WRITE,
                NOTIFY,
                AUTH + ":control",
                WRITE + ":control",
            )
        }
        self._reader: asyncio.Task[None] | None = None
        self._pending: tuple[int, int, asyncio.Future[bytes]] | None = None
        self._error: Exception | None = None
        self._lock = asyncio.Lock()
        self._closed = False

    @property
    def is_ready(self) -> bool:
        """Whether this authenticated connection can still serve requests."""
        return bool(self._keys) and self._error is None and not self._closed

    @property
    def needs_rekey(self) -> bool:
        """Re-establish the session before nonce reuse is possible."""
        return self.tx_counter >= REKEY_AT or self.rx_counter >= REKEY_AT - 1

    def fail(self, error: Exception) -> None:
        """Wake requests immediately after disconnect or invalid ciphertext."""
        if self._closed or self._error is not None:
            return
        self._error = error
        for queue in self._queues.values():
            while not queue.empty():
                queue.get_nowait()
            queue.put_nowait(error)
        if self._pending and not self._pending[2].done():
            self._pending[2].set_exception(error)
        self.on_error(error)

    def _notification(self, uuid: str, data: bytearray) -> None:
        if self._closed or self._error is not None:
            return
        if uuid in (AUTH, WRITE) and bytes(data) in (READY, OK, ACK):
            uuid += ":control"
        if uuid in (AUTH, UPNP) and bytes(data) in (
            bytes.fromhex("e0000000"),
            bytes.fromhex("e1000000"),
            bytes.fromhex("e2000000"),
            bytes.fromhex("e3000000"),
        ):
            self.fail(ProtocolError("Device rejected authentication exchange"))
            return
        try:
            self._queues[uuid].put_nowait(bytes(data))
        except asyncio.QueueFull:
            self.fail(ProtocolError("BLE notification queue overflow"))

    async def _recv(self, uuid: str) -> bytes:
        if self._error:
            raise self._error
        async with asyncio.timeout(TIMEOUT):
            value = await self._queues[uuid].get()
        if isinstance(value, Exception):
            raise value
        return value

    async def _write(self, uuid: str, data: bytes) -> None:
        if self._error:
            raise self._error
        char = self.client.services.get_characteristic(uuid)
        if char is None:
            raise ProtocolError("Missing curtain GATT characteristic")
        if "write-without-response" in char.properties:
            response = False
        elif "write" in char.properties:
            response = True
        else:
            raise ProtocolError("Curtain characteristic is not writable")
        await self.client.write_gatt_char(uuid, data, response=response)

    async def _expect(self, uuid: str, expected: bytes) -> None:
        # Match Mi Home's six-second control budget. Keep protocol data
        # in a separate queue so an early public key cannot be discarded here.
        with suppress(TimeoutError):
            async with asyncio.timeout(CONTROL_WAIT):
                while await self._recv(uuid + ":control") != expected:
                    pass

    def _capacity(self, uuid: str) -> int:
        char = self.client.services.get_characteristic(uuid)
        if char is None:
            raise ProtocolError("Missing curtain GATT characteristic")
        if "write-without-response" in char.properties:
            capacity = char.max_write_without_response_size
        else:
            capacity = self.client.mtu_size - 3
        return max(capacity, self._att_capacity) if capacity == 20 else capacity

    async def login(self, ltmk: bytes) -> None:
        """Run the greeting and CMD 0x50 mesh secure login."""
        if len(ltmk) != 32:
            raise ValueError("LTMK must contain 32 bytes")
        # Login writes 66 bytes and receives a 68-byte public-key notification.
        # Both must fit in one ATT payload; auth fragmentation is unsupported.
        capacity = self._capacity(AUTH)
        # BlueZ can retain its unknown/default 20-byte capacity after connecting.
        # Let the greeting establish the actual AUTH payload size in that case.
        if capacity != 20 and capacity < 68:
            raise UnsupportedMTUError("Secure login needs ATT MTU >= 71")
        for uuid in (UPNP, AUTH, WRITE, NOTIFY):
            for attempt in range(3):
                try:
                    async with asyncio.timeout(SUBSCRIBE_TIMEOUT):
                        await self.client.start_notify(
                            uuid,
                            lambda _char, data, uuid=uuid: self._notification(
                                uuid, data
                            ),
                        )
                    break
                except BleakError, TimeoutError:
                    if attempt == 2 or not self.client.is_connected:
                        raise
                    await sleep(RETRY_DELAYS[attempt])
        for attempt in range(3):
            # A new handshake needs fresh keys and no packets from the failed one.
            self._keys = b""
            self._error = None
            for queue in self._queues.values():
                while not queue.empty():
                    queue.get_nowait()
            if not self.client.is_connected:
                raise BleakError("Curtain disconnected before authentication")
            try:
                async with asyncio.timeout(LOGIN_TIMEOUT):
                    await self._authenticate(ltmk)
                break
            except AuthenticationError, UnsupportedMTUError:
                raise
            except ProtocolError, BleakError, TimeoutError:
                if attempt == 2 or not self.client.is_connected:
                    raise
                await sleep(RETRY_DELAYS[attempt])
        self._reader = asyncio.create_task(self._read_notifications())
        await self.request(0xF0)

    async def _authenticate(self, ltmk: bytes) -> None:
        """Perform one greeting and fresh ECDH exchange on the subscribed link."""
        for opcode in range(4):
            try:
                await self._write(VENDOR, bytes([opcode]))
            except BleakError, ProtocolError:
                break
            await sleep(0.06)
        await self._write(UPNP, b"\xa4")
        for _ in range(2):
            challenge = await self._recv(AUTH)
            if not challenge.startswith(b"\x00\x00\x04"):
                raise ProtocolError("Invalid greeting challenge")
            await self._write(AUTH, b"\x00\x00\x05" + challenge[3:])
        if (
            challenge[:4] == bytes.fromhex("00000401")
            and len(challenge) >= 68
            and len(challenge) == challenge[4] + 2
            and challenge[4:] == bytes([challenge[4]]) * (challenge[4] - 2)
        ):
            # The full challenge and its successful echo establish an ATT
            # payload lower bound for this connection, despite stale BlueZ data.
            self._att_capacity = len(challenge)
        if self._capacity(AUTH) < 68:
            raise UnsupportedMTUError("Secure login needs ATT MTU >= 71")
        # The OEM waits after the second echo before starting secure login.
        await sleep(0.1)
        private = ec.generate_private_key(ec.SECP256R1())
        public = private.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )[1:]
        await self._write(UPNP, bytes.fromhex("50000000"))
        await self._auth_parcel(3, public)
        announcement = await self._recv(AUTH)
        if len(announcement) != 68 or announcement[:4] != bytes.fromhex("00000203"):
            raise ProtocolError("Invalid device public key announcement")
        device_public = announcement[4:]
        await self._write(AUTH, ACK)
        try:
            peer = ec.EllipticCurvePublicKey.from_encoded_point(
                ec.SECP256R1(), b"\x04" + device_public
            )
        except ValueError as err:
            raise ProtocolError("Invalid P-256 device public key") from err
        self._keys = derive_keys(private.exchange(ec.ECDH(), peer), ltmk)
        confirm = AESCCM(self._keys[16:32], tag_length=4).encrypt(
            bytes(range(16, 28)), struct.pack("<I", zlib.crc32(device_public)), None
        )
        await self._auth_parcel(5, confirm)
        verdict = await self._recv(UPNP)
        if verdict == bytes.fromhex("52000000"):
            raise AuthenticationError("Device rejected LTMK")
        if verdict != bytes.fromhex("51000000"):
            raise ProtocolError("Device rejected secure login")

    async def _auth_parcel(self, code: int, payload: bytes) -> None:
        await self._write(AUTH, bytes([0, 0, 0, code, 1, 0]))
        await self._expect(AUTH, READY)
        await self._write(AUTH, b"\x01\x00" + payload)
        await self._expect(AUTH, OK)

    async def _read_notifications(self) -> None:
        try:
            while True:
                packet = await self._recv_idle_notification()
                async with asyncio.timeout(TIMEOUT):
                    if len(packet) >= 6 and packet[:4] == bytes.fromhex("00000200"):
                        counter = int.from_bytes(packet[4:6], "little")
                        ciphertext = packet[6:]
                        await self._write(NOTIFY, ACK)
                    elif len(packet) == 6 and packet[:4] == bytes(4):
                        count = int.from_bytes(packet[4:], "little")
                        if not 1 <= count <= 64:
                            raise ProtocolError("Invalid parcel count")
                        await self._write(NOTIFY, READY)
                        chunks = []
                        counter = -1
                        for index in range(1, count + 1):
                            part = await self._recv(NOTIFY)
                            prefix = 4 if index == 1 else 2
                            if (
                                len(part) <= prefix
                                or int.from_bytes(part[:2], "little") != index
                            ):
                                raise ProtocolError("Invalid MIOT parcel sequence")
                            if index == 1:
                                counter = int.from_bytes(part[2:4], "little")
                            chunks.append(part[prefix:])
                        ciphertext = b"".join(chunks)
                        await self._write(NOTIFY, OK)
                    else:
                        raise ProtocolError("Invalid MIOT notification envelope")
                    if counter <= self.rx_counter:
                        raise ProtocolError("Replayed MIOT counter")
                    plaintext = AESCCM(self._keys[:16], tag_length=4).decrypt(
                        nonce(self._keys[32:36], counter), ciphertext, None
                    )
                    self.rx_counter = counter
                    tid, opcode = header(plaintext)
                    pending = self._pending
                    if opcode == 7:
                        event = parse_event(plaintext)
                        if self.on_event is not None:
                            self.on_event(event)
                    elif pending and (tid, opcode) == pending[:2]:
                        if opcode == 3:
                            # Merge reports in wire order, before a newer push
                            # can arrive while the request waiter is waking up.
                            self.on_properties(parse_properties(plaintext))
                        if not pending[2].done():
                            pending[2].set_result(plaintext)
                    elif opcode == 4:
                        self.on_properties(parse_properties(plaintext))
        except (ProtocolError, BleakError, TimeoutError, InvalidTag) as err:
            self.fail(
                ProtocolError("Invalid encrypted BLE notification")
                if isinstance(err, InvalidTag)
                else err
            )

    async def _recv_idle_notification(self) -> bytes:
        """An idle connection need not produce unsolicited notifications."""
        value = await self._queues[NOTIFY].get()
        if isinstance(value, Exception):
            raise value
        return value

    async def request(self, opcode: int, payload: bytes = b"") -> bytes:
        """Match both TID and opcode; never retry an ambiguous motor command."""
        async with self._lock:
            if self._closed:
                raise ProtocolError("Session closed")
            if self._error:
                raise self._error
            if self.tx_counter > 0xFFFF:
                raise ProtocolError("Session counter exhausted")
            self._tid = self._tid % 65535 + 1
            counter = self.tx_counter
            self.tx_counter += 1
            ciphertext = AESCCM(self._keys[16:32], tag_length=4).encrypt(
                nonce(self._keys[36:40], counter),
                frame(self._tid, opcode, payload),
                None,
            )
            future: asyncio.Future[bytes] = asyncio.get_running_loop().create_future()
            self._pending = (self._tid, 0xF0 if opcode == 0xF0 else opcode + 1, future)
            try:
                async with asyncio.timeout(TIMEOUT):
                    size = min(240, self._capacity(WRITE) - 4)
                    if size < 1:
                        raise UnsupportedMTUError("MIOT write capacity too small")
                    chunks = [
                        ciphertext[i : i + size]
                        for i in range(0, len(ciphertext), size)
                    ]
                    await self._write(WRITE, bytes(4) + struct.pack("<H", len(chunks)))
                    await self._expect(WRITE, READY)
                    for index, chunk in enumerate(chunks, 1):
                        prefix = struct.pack("<H", index)
                        if index == 1:
                            prefix += struct.pack("<H", counter)
                        await self._write(WRITE, prefix + chunk)
                    await self._expect(WRITE, OK)
                    return await future
            except (ProtocolError, BleakError, TimeoutError) as err:
                self.fail(err)
                raise
            except asyncio.CancelledError:
                self.fail(ProtocolError("MIOT request cancelled"))
                raise
            finally:
                self._pending = None
                if not future.done():
                    future.cancel()
                elif not future.cancelled():
                    future.exception()

    async def close(self) -> None:
        """Cancel the reader and erase session key material."""
        self.fail(ProtocolError("Session closed"))
        self._closed = True
        if self._reader:
            self._reader.cancel()
            with suppress(asyncio.CancelledError):
                await self._reader
            self._reader = None
        self._keys = b""
