"""Xiaomi cloud QR login and existing Mesh credential retrieval.

Adapted and modified from shirok1/ha-cuk-ble (Apache-2.0).
Changes: validated Mesh credentials, bounded polling and redacted errors.

Adapted from https://github.com/PiotrMachowski/Xiaomi-cloud-tokens-extractor.
QR-login only — no password/2FA paths.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import logging
import os
import random
import re
import string
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import aiohttp
from cryptography.hazmat.decrepit.ciphers.algorithms import ARC4
from cryptography.hazmat.primitives.ciphers import Cipher

_LOGGER = logging.getLogger(__name__)

SERVERS: tuple[str, ...] = ("cn", "de", "us", "ru", "tw", "sg", "in", "i2")

_USER_AGENT = "Android-7.1.1-1.0.0-ONEPLUS A3010-136-%s APP/xiaomi.smarthome APPV/62830"


def _fresh_user_agent() -> str:
    return _USER_AGENT % "".join(random.choices(string.ascii_uppercase, k=13))


def _rc4_xor(key: bytes, data: bytes) -> bytes:
    encryptor = Cipher(ARC4(key), mode=None).encryptor()
    encryptor.update(bytes(1024))
    return encryptor.update(data) + encryptor.finalize()


def _rc4_encrypt_b64(key_b64: str, payload: str) -> str:
    return base64.b64encode(
        _rc4_xor(base64.b64decode(key_b64), payload.encode())
    ).decode()


def _rc4_decrypt_b64(key_b64: str, payload_b64: str) -> bytes:
    return _rc4_xor(base64.b64decode(key_b64), base64.b64decode(payload_b64))


def _strip_jsonp(text: str) -> dict[str, Any]:
    data = json.loads(text.replace("&&&START&&&", "").strip())
    if not isinstance(data, dict):
        raise ValueError("response root must be a JSON object")
    return data


class CloudError(Exception):
    """Sanitized failure with a config-flow translation key."""

    def __init__(self, message: str = "", *, reason: str = "cloud_failed"):
        super().__init__(message)
        self.reason = reason


def _check_status(status: int) -> None:
    if status != 200:
        _LOGGER.warning("Mi Home request failed: HTTP %s", status)
    if status == 401:
        raise CloudError("Cloud authentication expired", reason="cloud_auth")
    if status == 403:
        raise CloudError("Cloud access denied", reason="cloud_forbidden")
    if status != 200:
        raise CloudError("Cloud HTTP request failed", reason="cloud_network")


def _https_url(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("Invalid URL")
    url = urlsplit(value)
    if url.scheme != "https" or not url.hostname or url.username or url.password:
        raise ValueError("Invalid URL")
    return value


class QRLoginPending(Exception):
    """QR scan still pending — caller should retry."""


@dataclass(repr=False)
class QRLogin:
    """State carried between starting the QR login and polling for approval."""

    qr_image_url: str
    login_url: str
    lp_url: str
    timeout_seconds: int
    device_id: str
    cookies: dict[str, str] = field(default_factory=dict)
    user_agent: str = field(default_factory=_fresh_user_agent, compare=False)


@dataclass(repr=False)
class CloudAuth:
    """Resolved Xiaomi cloud credentials after successful QR scan."""

    user_id: str
    ssecurity: str
    service_token: str
    user_agent: str = field(default_factory=_fresh_user_agent, compare=False)


def _cookies_for(device_id: str) -> dict[str, str]:
    return {
        "sdkVersion": "accountsdk-18.8.15",
        "deviceId": device_id,
    }


async def start_qr_login(session: aiohttp.ClientSession) -> QRLogin:
    """Request a QR-code login URL. Show ``qr_image_url`` to the user."""
    device_id = "".join(random.choices(string.ascii_letters, k=16))
    cookies = _cookies_for(device_id)
    url = "https://account.xiaomi.com/longPolling/loginUrl"
    params = {
        "_qrsize": "480",
        "qs": "%3Fsid%3Dxiaomiio%26_json%3Dtrue",
        "bizDeviceType": "",
        "callback": "https://sts.api.io.mi.com/sts",
        "_hasLogo": "false",
        "theme": "",
        "needTheme": "false",
        "showActiveX": "false",
        "serviceParam": (
            '{"checkSafePhone":false,"checkSafeAddress":false,"lsrp_score":0.0}'
        ),
        "sid": "xiaomiio",
        "_locale": "en_GB",
        "_dc": str(int(time.time() * 1000)),
    }
    headers = {"User-Agent": _fresh_user_agent()}
    try:
        async with session.get(
            url,
            params=params,
            headers=headers,
            cookies=cookies,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as r:
            text = await r.text()
            _check_status(r.status)
    except (TimeoutError, aiohttp.ClientError) as err:
        _LOGGER.warning("Mi Home login URL request failed: %s", type(err).__name__)
        raise CloudError("QR login request failed", reason="cloud_network") from None
    try:
        data = _strip_jsonp(text)
    except ValueError:
        raise CloudError("QR login response not JSON") from None
    try:
        timeout = int(data.get("timeout", 300))
        if timeout <= 0 or isinstance(data.get("timeout"), bool):
            raise ValueError("Invalid timeout")
        login_url = data.get("loginUrl", "")
        return QRLogin(
            qr_image_url=_https_url(data["qr"]),
            login_url=_https_url(login_url) if login_url != "" else "",
            lp_url=_https_url(data["lp"]),
            timeout_seconds=min(timeout, 600),
            device_id=device_id,
            cookies=cookies,
            user_agent=headers["User-Agent"],
        )
    except KeyError, TypeError, ValueError:
        raise CloudError("QR login response invalid") from None


async def poll_qr_login(session: aiohttp.ClientSession, qr: QRLogin) -> CloudAuth:
    """Poll the long-polling URL once; returns CloudAuth on success.

    Raises ``QRLoginPending`` if the user has not scanned/approved yet.
    Raises ``CloudError`` if the login endpoint returns a terminal error.
    """
    headers = {"User-Agent": qr.user_agent}
    try:
        async with session.get(
            qr.lp_url,
            headers=headers,
            cookies=qr.cookies,
            timeout=aiohttp.ClientTimeout(total=35),
        ) as r:
            text = await r.text()
            _check_status(r.status)
    except TimeoutError:
        raise QRLoginPending from None
    except aiohttp.ClientError as err:
        _LOGGER.warning("Mi Home login polling failed: %s", type(err).__name__)
        raise CloudError("QR poll request failed", reason="cloud_network") from None

    try:
        data = _strip_jsonp(text)
    except ValueError:
        raise CloudError("QR poll response not JSON") from None

    code = data.get("code")
    if code in (401, 700, 70016):
        raise QRLoginPending()
    if code not in (0, None):
        raise CloudError("QR login was rejected", reason="cloud_auth")
    if "ssecurity" not in data:
        raise QRLoginPending()

    try:
        location = _https_url(data.get("location"))
        user_id = data.get("userId")
        security = data["ssecurity"]
        if (
            not isinstance(user_id, (str, int))
            or isinstance(user_id, bool)
            or not str(user_id).strip()
            or not isinstance(security, str)
            or len(base64.b64decode(security, validate=True)) != 16
        ):
            raise ValueError("Invalid credentials")
    except ValueError, TypeError:
        raise CloudError("QR login response invalid") from None

    try:
        async with session.get(
            location,
            headers=headers,
            cookies=qr.cookies,
            allow_redirects=True,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as r2:
            await r2.read()
            _check_status(r2.status)
            cookie_url = r2.url
    except (TimeoutError, aiohttp.ClientError) as err:
        _LOGGER.warning("Mi Home login completion failed: %s", type(err).__name__)
        raise CloudError("QR login redirect failed", reason="cloud_network") from None

    service_cookie = session.cookie_jar.filter_cookies(cookie_url).get("serviceToken")
    if service_cookie is None or not service_cookie.value:
        raise CloudError("serviceToken cookie not set after location fetch")

    return CloudAuth(
        user_id=str(user_id),
        ssecurity=security,
        service_token=str(service_cookie.value),
        user_agent=qr.user_agent,
    )


async def wait_for_qr_scan(
    session: aiohttp.ClientSession,
    qr: QRLogin,
    *,
    max_wait: float | None = None,
) -> CloudAuth:
    """Long-poll until the user scans and approves, or ``max_wait`` elapses."""
    try:
        async with asyncio.timeout(
            max_wait if max_wait is not None else qr.timeout_seconds
        ):
            while True:
                try:
                    return await poll_qr_login(session, qr)
                except QRLoginPending:
                    await asyncio.sleep(1)
    except TimeoutError:
        raise CloudError("QR login timed out", reason="cloud_auth") from None


def _api_base(region: str) -> str:
    return "https://" + ("" if region == "cn" else region + ".") + "api.io.mi.com/app"


def _signed_nonce(ssecurity: str, nonce_b64: str) -> str:
    digest = hashlib.sha256(
        base64.b64decode(ssecurity) + base64.b64decode(nonce_b64)
    ).digest()
    return base64.b64encode(digest).decode()


def _enc_signature(
    url: str, method: str, signed_nonce: str, params: dict[str, str]
) -> str:
    parts = [method.upper(), url.split("com")[1].replace("/app/", "/")]
    parts.extend(f"{k}={v}" for k, v in params.items())
    parts.append(signed_nonce)
    return base64.b64encode(hashlib.sha1("&".join(parts).encode()).digest()).decode()


async def _encrypted_post(
    session: aiohttp.ClientSession,
    auth: CloudAuth,
    region: str,
    path: str,
    data: str,
) -> dict[str, Any]:
    if region not in SERVERS:
        raise CloudError("Unsupported region")
    url = _api_base(region) + path
    params = {"data": data}
    millis = int(time.time() * 1000)
    nonce_bytes = os.urandom(8) + (millis // 60000).to_bytes(4, "big")
    nonce = base64.b64encode(nonce_bytes).decode()
    try:
        signed_nonce = _signed_nonce(auth.ssecurity, nonce)
        params["rc4_hash__"] = _enc_signature(url, "POST", signed_nonce, params)
        for key, value in params.items():
            params[key] = _rc4_encrypt_b64(signed_nonce, value)
        params["signature"] = _enc_signature(url, "POST", signed_nonce, params)
        params["ssecurity"] = auth.ssecurity
        params["_nonce"] = nonce
    except binascii.Error, IndexError, ValueError:
        raise CloudError(f"{path}: request signing failed") from None

    headers = {
        "Accept-Encoding": "identity",
        "User-Agent": auth.user_agent,
        "Content-Type": "application/x-www-form-urlencoded",
        "x-xiaomi-protocal-flag-cli": "PROTOCAL-HTTP2",
        "MIOT-ENCRYPT-ALGORITHM": "ENCRYPT-RC4",
    }
    cookies = {
        "userId": auth.user_id,
        "serviceToken": auth.service_token,
        "yetAnotherServiceToken": auth.service_token,
        "locale": "en_GB",
        "timezone": "GMT+00:00",
        "is_daylight": "0",
        "dst_offset": "0",
        "channel": "MI_APP_STORE",
    }
    try:
        async with session.post(
            url,
            headers=headers,
            cookies=cookies,
            data=params,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as r:
            text = await r.text()
            _check_status(r.status)
    except (TimeoutError, aiohttp.ClientError) as err:
        _LOGGER.warning("Mi Home %s request failed: %s", path, type(err).__name__)
        raise CloudError(f"{path}: request failed", reason="cloud_network") from None
    try:
        decoded = _rc4_decrypt_b64(_signed_nonce(auth.ssecurity, nonce), text)
        response = json.loads(decoded)
    except binascii.Error, UnicodeDecodeError, ValueError:
        raise CloudError(f"{path}: decrypt/parse failed") from None
    if not isinstance(response, dict):
        raise CloudError(f"{path}: response root must be a JSON object")
    return response


async def list_devices(
    session: aiohttp.ClientSession,
    auth: CloudAuth,
    region: str,
) -> list[dict[str, Any]]:
    """Return raw device records for the given region."""
    resp = await _encrypted_post(
        session,
        auth,
        region,
        "/home/device_list",
        '{"getVirtualModel":true,"getHuamiDevices":1}',
    )
    result = _result(resp)
    devices = result.get("list")
    if devices is None:
        return []
    if not isinstance(devices, list) or not all(
        isinstance(device, dict) for device in devices
    ):
        raise CloudError("Device-list response has an invalid device list")
    return devices


def _result(response: dict[str, Any]) -> dict[str, Any]:
    """Reject business errors even when the HTTP request succeeded."""
    if response.get("code") in (401, 403):
        _check_status(response["code"])
    if response.get("code") != 0:
        raise CloudError("Cloud request was rejected")
    result = response.get("result", response)
    if not isinstance(result, dict):
        raise CloudError("Invalid cloud result")
    return result


async def get_ltmk(
    session: aiohttp.ClientSession, auth: CloudAuth, region: str, did: str
) -> str:
    """Read an existing key using the device owner's account."""
    response = await _encrypted_post(
        session, auth, region, "/v2/blemesh/query_dev", json.dumps({"did": did})
    )
    key = _result(response).get("gatt_ltmk")
    if not isinstance(key, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", key):
        raise CloudError("Missing or invalid GATT LTMK")
    return key.lower()
