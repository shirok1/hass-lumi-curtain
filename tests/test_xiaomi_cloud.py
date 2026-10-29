"""Cloud contract tests use synthetic sessions and encrypted server responses."""

import asyncio
import base64
import hashlib
import json
from typing import cast
from unittest.mock import AsyncMock

import aiohttp
import pytest
from cryptography.hazmat.decrepit.ciphers.algorithms import ARC4
from cryptography.hazmat.primitives.ciphers import Cipher
from yarl import URL

from custom_components.lumi_curtain import xiaomi_cloud as cloud

AUTH = cloud.CloudAuth("owner", base64.b64encode(b"s" * 16).decode(), "secret-token")
QR = cloud.QRLogin(
    "https://account.xiaomi.com/qr",
    "",
    "https://account.xiaomi.com/poll",
    300,
    "device",
)


class Response:
    def __init__(self, value, status=200, url="https://sts.api.io.mi.com/sts"):
        self.value = value
        self.status = status
        self.url = URL(url)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def text(self):
        if isinstance(self.value, BaseException):
            raise self.value
        return self.value if isinstance(self.value, str) else json.dumps(self.value)

    async def read(self):
        return await self.text()


class Session:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.cookie_jar = aiohttp.CookieJar()
        self.requests = []

    def get(self, *args, **kwargs):
        self.requests.append((args, kwargs))
        return next(self.responses)

    def post(self, *args, **kwargs):
        self.requests.append((args, kwargs))
        return next(self.responses)


def http_session(*responses) -> aiohttp.ClientSession:
    return cast(aiohttp.ClientSession, Session(*responses))


async def test_qr_login_success_and_no_credentials_in_repr():
    session = http_session(Response({"qr": QR.qr_image_url, "lp": QR.lp_url}))
    qr = await cloud.start_qr_login(session)
    assert (
        cast(Session, session).requests[0][1]["headers"]["User-Agent"] == qr.user_agent
    )
    assert qr.timeout_seconds == 300
    assert qr.cookies["deviceId"] == qr.device_id
    assert qr.qr_image_url not in repr(qr)
    session = http_session(
        Response(
            {
                "code": 0,
                "ssecurity": AUTH.ssecurity,
                "userId": "owner",
                "location": "https://sts.api.io.mi.com/sts",
            }
        ),
        Response(""),
    )
    session.cookie_jar.update_cookies(
        {"serviceToken": AUTH.service_token},
        response_url=URL("https://sts.api.io.mi.com"),
    )
    auth = await cloud.poll_qr_login(session, qr)
    assert auth == AUTH
    assert auth.user_agent == qr.user_agent
    assert all(
        r[1]["headers"]["User-Agent"] == qr.user_agent
        for r in cast(Session, session).requests
    )
    assert AUTH.service_token not in repr(auth)


@pytest.mark.parametrize(
    "response",
    [
        Response({}, 500),
        Response([]),
        Response({}),
        Response({"qr": "qr", "lp": "lp", "timeout": "SECRET"}),
        Response(aiohttp.ClientConnectionError("SECRET")),
        Response(TimeoutError()),
    ],
)
async def test_qr_start_failures_are_redacted(response):
    with pytest.raises(cloud.CloudError) as exc:
        await cloud.start_qr_login(http_session(response))
    assert "SECRET" not in str(exc.value)


@pytest.mark.parametrize(
    "responses,error",
    [
        ([Response({}, 403)], cloud.CloudError),
        ([Response("bad json")], cloud.CloudError),
        ([Response({"code": 70016})], cloud.QRLoginPending),
        ([Response({})], cloud.QRLoginPending),
        ([Response(TimeoutError())], cloud.QRLoginPending),
        ([Response({"code": 42})], cloud.CloudError),
        ([Response({"ssecurity": "key"})], cloud.CloudError),
        ([Response(aiohttp.ClientConnectionError())], cloud.CloudError),
        (
            [
                Response(
                    {
                        "ssecurity": AUTH.ssecurity,
                        "userId": AUTH.user_id,
                        "location": "https://sts.api.io.mi.com/sts",
                    }
                ),
                Response({}, 400),
            ],
            cloud.CloudError,
        ),
        (
            [
                Response(
                    {
                        "ssecurity": AUTH.ssecurity,
                        "userId": AUTH.user_id,
                        "location": "https://sts.api.io.mi.com/sts",
                    }
                ),
                Response(TimeoutError()),
            ],
            cloud.CloudError,
        ),
        (
            [
                Response(
                    {
                        "ssecurity": AUTH.ssecurity,
                        "userId": AUTH.user_id,
                        "location": "https://sts.api.io.mi.com/sts",
                    }
                ),
                Response(""),
            ],
            cloud.CloudError,
        ),
    ],
)
async def test_poll_failures(responses, error):
    with pytest.raises(error):
        await cloud.poll_qr_login(http_session(*responses), QR)


async def test_poll_deadline_success_and_cancellation(monkeypatch):
    poll = AsyncMock(side_effect=[cloud.QRLoginPending(), AUTH])
    monkeypatch.setattr(cloud, "poll_qr_login", poll)
    assert await cloud.wait_for_qr_scan(http_session(), QR) == AUTH
    poll.side_effect = cloud.QRLoginPending()
    with pytest.raises(cloud.CloudError, match="timed out"):
        await cloud.wait_for_qr_scan(http_session(), QR, max_wait=0)
    poll.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await cloud.wait_for_qr_scan(http_session(), QR)


def arc4(key, data):
    cipher = Cipher(ARC4(key), mode=None).encryptor()
    cipher.update(b"\0" * 1024)
    return cipher.update(data)


@pytest.mark.parametrize("region", ["cn", "de"])
async def test_signed_request_and_encrypted_response(region):
    class Server(Session):
        def post(self, url, **kwargs):
            assert (
                url
                == "https://"
                + ("de." if region == "de" else "")
                + "api.io.mi.com/app/v2/blemesh/query_dev"
            )
            assert "params" not in kwargs  # Credentials stay out of URLs.
            assert kwargs["headers"]["User-Agent"] == AUTH.user_agent
            params = kwargs["data"]
            assert kwargs["cookies"]["serviceToken"] == AUTH.service_token
            key = hashlib.sha256(
                base64.b64decode(AUTH.ssecurity) + base64.b64decode(params["_nonce"])
            ).digest()
            assert json.loads(arc4(key, base64.b64decode(params["data"]))) == {
                "did": "mesh.test"
            }
            parts = [
                "POST",
                "/v2/blemesh/query_dev",
                f"data={params['data']}",
                f"rc4_hash__={params['rc4_hash__']}",
                base64.b64encode(key).decode(),
            ]
            signature = base64.b64encode(
                hashlib.sha1("&".join(parts).encode()).digest()
            ).decode()
            assert params["signature"] == signature
            body = json.dumps({"code": 0, "result": {"gatt_ltmk": "AB" * 32}}).encode()
            return Response(base64.b64encode(arc4(key, body)).decode())

    assert (
        await cloud.get_ltmk(
            cast(aiohttp.ClientSession, Server()), AUTH, region, "mesh.test"
        )
        == "ab" * 32
    )


@pytest.mark.parametrize(
    "region,auth,response",
    [
        ("bad", AUTH, Response("")),
        ("cn", cloud.CloudAuth("owner", "bad!", "token"), Response("")),
        ("cn", AUTH, Response("", 500)),
        ("cn", AUTH, Response(aiohttp.ClientConnectionError("SECRET"))),
        ("cn", AUTH, Response("invalid-base64")),
    ],
)
async def test_encrypted_request_failures(region, auth, response):
    with pytest.raises(cloud.CloudError) as exc:
        await cloud.get_ltmk(http_session(response), auth, region, "mesh.test")
    assert "SECRET" not in str(exc.value)


async def test_non_object_encrypted_response(monkeypatch):
    monkeypatch.setattr(cloud, "_rc4_decrypt_b64", lambda *args: b"[]")
    with pytest.raises(cloud.CloudError, match="JSON object"):
        await cloud.get_ltmk(
            http_session(Response("anything")), AUTH, "cn", "mesh.test"
        )


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"code": 1, "result": {"gatt_ltmk": "ab" * 32}},
        {"code": 0, "result": []},
        {"code": 0},
        {"code": 0, "gatt_ltmk": "ab" * 16},
        {"code": 0, "gatt_ltmk": "g" * 64},
        {"code": 0, "gatt_ltmk": 42},
    ],
)
async def test_invalid_credentials_rejected(monkeypatch, response):
    monkeypatch.setattr(cloud, "_encrypted_post", AsyncMock(return_value=response))
    with pytest.raises(cloud.CloudError):
        await cloud.get_ltmk(http_session(), AUTH, "cn", "mesh.test")


@pytest.mark.parametrize("result", [{}, {"list": []}, {"list": [{"did": "mesh.test"}]}])
async def test_device_list_and_root_key(monkeypatch, result):
    post = AsyncMock(return_value={"code": 0, "result": result})
    monkeypatch.setattr(cloud, "_encrypted_post", post)
    assert await cloud.list_devices(http_session(), AUTH, "cn") == result.get(
        "list", []
    )
    post.return_value = {"code": 0, "gatt_ltmk": "AB" * 32}
    assert await cloud.get_ltmk(http_session(), AUTH, "cn", "mesh.test") == "ab" * 32


@pytest.mark.parametrize("devices", [{}, ["bad"]])
async def test_malformed_device_list(monkeypatch, devices):
    monkeypatch.setattr(
        cloud,
        "_encrypted_post",
        AsyncMock(return_value={"code": 0, "result": {"list": devices}}),
    )
    with pytest.raises(cloud.CloudError):
        await cloud.list_devices(http_session(), AUTH, "cn")


@pytest.mark.parametrize(
    "status,reason",
    [(401, "cloud_auth"), (403, "cloud_forbidden"), (503, "cloud_network")],
)
async def test_http_error_categories(status, reason):
    with pytest.raises(cloud.CloudError) as exc:
        await cloud.get_ltmk(
            http_session(Response("", status)), AUTH, "cn", "mesh.test"
        )
    assert exc.value.reason == reason


@pytest.mark.parametrize(
    "field,value",
    [
        ("qr", None),
        ("lp", "http://account.xiaomi.com/poll"),
        ("loginUrl", "javascript:SECRET"),
        ("loginUrl", None),
        ("timeout", 0),
        ("timeout", -1),
        ("timeout", True),
    ],
)
async def test_invalid_login_fields(field, value):
    data = {"qr": QR.qr_image_url, "lp": QR.lp_url, field: value}
    with pytest.raises(cloud.CloudError):
        await cloud.start_qr_login(http_session(Response(data)))


@pytest.mark.parametrize(
    "field,value",
    [
        ("userId", None),
        ("userId", ""),
        ("userId", True),
        ("ssecurity", "SECRET"),
        ("ssecurity", 42),
        ("location", "http://sts.api.io.mi.com/sts"),
    ],
)
async def test_invalid_auth_does_not_follow_redirect(field, value):
    data = {
        "ssecurity": AUTH.ssecurity,
        "userId": "owner",
        "location": "https://sts.api.io.mi.com/sts",
        field: value,
    }
    session = http_session(Response(data))
    with pytest.raises(cloud.CloudError):
        await cloud.poll_qr_login(session, QR)
    assert len(cast(Session, session).requests) == 1


def test_rc4_drop1024_known_vector():
    # RFC 6229, 40-bit key, keystream at offset 1024.
    assert (
        cloud._rc4_xor(bytes.fromhex("0102030405"), bytes(16)).hex()
        == "30abbcc7c20b01609f23ee2d5f6bb7df"
    )


@pytest.mark.parametrize("code,reason", [(401, "cloud_auth"), (403, "cloud_forbidden")])
async def test_api_auth_error_categories(monkeypatch, code, reason):
    monkeypatch.setattr(
        cloud, "_encrypted_post", AsyncMock(return_value={"code": code})
    )
    with pytest.raises(cloud.CloudError) as exc:
        await cloud.get_ltmk(http_session(), AUTH, "cn", "mesh.test")
    assert exc.value.reason == reason


@pytest.mark.parametrize(
    "error",
    [aiohttp.ClientConnectionError("SECRET-ticket"), TimeoutError("SECRET-ticket")],
)
async def test_login_network_diagnostics_are_redacted(caplog, error):
    with pytest.raises(cloud.CloudError):
        await cloud.start_qr_login(http_session(Response(error)))
    assert type(error).__name__ in caplog.text
    assert "login URL" in caplog.text
    assert "SECRET" not in caplog.text
