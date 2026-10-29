"""Exercise cloud import through HA, including local authentication and cleanup."""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.data_entry_flow import FlowResultType

from custom_components.lumi_curtain import xiaomi_cloud as cloud
from custom_components.lumi_curtain.const import DOMAIN, MODEL

from .conftest import ADDRESS, LTMK
from .test_config_flow import discovery
from .test_xiaomi_cloud import AUTH, QR

DEVICE = {"did": "mesh.new", "model": MODEL, "mac": ADDRESS, "name": "New curtain"}


@pytest.fixture
def cloud_api(monkeypatch):
    methods = {
        "start_qr_login": AsyncMock(return_value=QR),
        "wait_for_qr_scan": AsyncMock(return_value=AUTH),
        "list_devices": AsyncMock(return_value=[DEVICE]),
        "get_ltmk": AsyncMock(return_value=LTMK.hex()),
    }
    for name, mock in methods.items():
        monkeypatch.setattr(cloud, name, mock)
    return methods


async def start(hass, source="user", entry=None):
    context = {"source": source}
    data = None
    if source == "bluetooth":
        data = discovery()
    if source == "reauth":
        assert entry is not None
        entry.add_to_hass(hass)
        context["entry_id"] = entry.entry_id
        data = entry.data
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context=context, data=data
    )
    assert result["type"] == FlowResultType.MENU
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "cloud"}
    )
    assert result["step_id"] == "cloud"
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"region": "cn"}
    )


async def finish_login(hass, result):
    await hass.async_block_till_done()
    result = await hass.config_entries.flow.async_configure(result["flow_id"])
    return result


@pytest.mark.parametrize("source", ["user", "bluetooth", "reauth"])
async def test_import_and_reauth(hass, entry, cloud_api, peripheral, ble, source):
    cloud_api["list_devices"].return_value += [
        {**DEVICE, "did": "mesh.wrong-model", "model": "other"},
        {**DEVICE, "did": "mesh.wrong-mac", "mac": "not-a-mac"},
        {**DEVICE, "did": ""},
    ]
    result = await start(hass, source, entry)
    assert result["type"] == FlowResultType.SHOW_PROGRESS
    flow = hass.config_entries.flow._progress[result["flow_id"]]
    session = flow._cloud_session
    result = await finish_login(hass, result)
    assert result["step_id"] == "cloud_device"
    assert list(flow._devices) == ["mesh.new"]
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"device": "mesh.new"}
    )
    assert result["step_id"] == "cloud_confirm"
    assert result["description_placeholders"]["name"] == DEVICE["name"]
    cloud_api["get_ltmk"].assert_awaited_once_with(session, AUTH, "cn", "mesh.new")
    assert session.closed
    assert flow._auth is None and flow._qr is None and flow._devices == {}
    ble[1].assert_not_called()  # User must close Mi Home before the local login.
    with (
        patch("custom_components.lumi_curtain.async_setup_entry", return_value=True),
        patch.object(hass.config_entries, "async_reload", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        await hass.async_block_till_done()
    if source == "reauth":
        assert result["reason"] == "reauth_successful"
        assert entry.data == {"address": ADDRESS, "ltmk": LTMK.hex()}
    else:
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["data"] == {"address": ADDRESS, "ltmk": LTMK.hex()}
        assert result["result"].unique_id == ADDRESS
    assert flow._cloud_data == {}
    peripheral.client.disconnect.assert_awaited_once()


async def test_key_fetch_and_ble_errors_allow_retry(hass, cloud_api, peripheral, ble):
    result = await finish_login(hass, await start(hass))
    cloud_api["get_ltmk"].side_effect = cloud.CloudError("expired")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"device": "mesh.new"}
    )
    assert result["errors"] == {"base": "cloud_failed"}
    cloud_api["get_ltmk"].side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"device": "mesh.new"}
    )
    peripheral.reject = True
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["errors"] == {"base": "invalid_auth"}
    flow = hass.config_entries.flow._progress[result["flow_id"]]
    assert flow._cloud_data["ltmk"] == LTMK.hex()
    hass.config_entries.flow.async_abort(result["flow_id"])
    assert flow._cloud_data == {}


@pytest.mark.parametrize("failure", ["start", "login", "fetch", "empty", "cancelled"])
async def test_cloud_failure_and_restart(hass, cloud_api, failure):
    if failure == "start":
        cloud_api["start_qr_login"].side_effect = cloud.CloudError()
    elif failure == "login":
        cloud_api["wait_for_qr_scan"].side_effect = cloud.CloudError()
    elif failure == "fetch":
        cloud_api["list_devices"].side_effect = cloud.CloudError()
    elif failure == "empty":
        cloud_api["list_devices"].return_value = []
    elif failure == "cancelled":
        cloud_api["wait_for_qr_scan"].side_effect = asyncio.CancelledError()
    result = await start(hass)
    if failure != "start":
        result = await finish_login(hass, result)
    assert result["step_id"] == "cloud_error"
    assert result["errors"]["base"] == (
        "cloud_no_devices" if failure == "empty" else "cloud_failed"
    )
    flow = hass.config_entries.flow._progress[result["flow_id"]]
    assert flow._cloud_session is None
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "cloud"
    hass.config_entries.flow.async_abort(result["flow_id"])


async def test_cancel_pending_scan_releases_task_and_session(hass, cloud_api):
    waiting = asyncio.Event()

    async def wait(*args):
        await waiting.wait()

    cloud_api["wait_for_qr_scan"].side_effect = wait
    result = await start(hass)
    flow = hass.config_entries.flow._progress[result["flow_id"]]
    task, session = flow._cloud_task, flow._cloud_session
    hass.config_entries.flow.async_abort(result["flow_id"])
    await hass.async_block_till_done()
    assert task.cancelled() and session.closed
    assert flow._auth is None


@pytest.mark.parametrize("source", ["user", "bluetooth", "reauth"])
async def test_device_filtering(hass, entry, cloud_api, source):
    if source == "user":
        entry.add_to_hass(hass)
    else:
        cloud_api["list_devices"].return_value = [
            {**DEVICE, "mac": "11:22:33:44:55:66"}
        ]
    result = await finish_login(hass, await start(hass, source, entry))
    assert result["step_id"] == "cloud_error"
    assert result["errors"] == {"base": "cloud_no_devices"}
    hass.config_entries.flow.async_abort(result["flow_id"])


@pytest.mark.parametrize("stage", ["qr", "key"])
async def test_abort_during_http_does_not_restart_flow(hass, cloud_api, stage):
    entered, release = asyncio.Event(), asyncio.Event()

    async def response(*args):
        entered.set()
        await release.wait()
        return QR if stage == "qr" else LTMK.hex()

    if stage == "qr":
        cloud_api["start_qr_login"].side_effect = response
        request = asyncio.create_task(start(hass))
    else:
        result = await finish_login(hass, await start(hass))
        cloud_api["get_ltmk"].side_effect = response
        request = asyncio.create_task(
            hass.config_entries.flow.async_configure(
                result["flow_id"], {"device": "mesh.new"}
            )
        )
    await entered.wait()
    progress = hass.config_entries.flow.async_progress_by_handler(DOMAIN)[0]
    flow = hass.config_entries.flow._progress[progress["flow_id"]]
    session = flow._cloud_session
    hass.config_entries.flow.async_abort(progress["flow_id"])
    release.set()
    result = await request
    assert result["reason"] == "cloud_cancelled"
    assert session.closed and flow._cloud_task is None
    assert flow._cloud_data == {}


@pytest.mark.parametrize("language", ["en", "zh-Hans"])
@pytest.mark.parametrize(
    "login_url", ["", "https://account.xiaomi.com/login?ticket=test"]
)
async def test_login_link_is_in_progress_description(
    hass, cloud_api, language, login_url
):
    """Match the frontend's SHOW_PROGRESS translation lookup, not form copy.

    frontend/src/dialogs/config-flow/show-dialog-config-flow.ts:
    renderShowFormProgressDescription reads config.progress.<progress_action>.
    """
    from homeassistant.helpers.translation import async_get_translations

    waiting = asyncio.Event()

    async def wait(*args):
        await waiting.wait()

    cloud_api["wait_for_qr_scan"].side_effect = wait
    cloud_api["start_qr_login"].return_value = replace(QR, login_url=login_url)
    result = await start(hass)
    try:
        assert result["type"] == FlowResultType.SHOW_PROGRESS
        translations = await async_get_translations(hass, language, "config", {DOMAIN})
        key = f"component.{DOMAIN}.config.progress.{result['progress_action']}"
        description = translations[key].format(**result["description_placeholders"])
        assert "![" not in description
        target = login_url or QR.qr_image_url
        from html.parser import HTMLParser

        class Links(HTMLParser):
            def __init__(self):
                super().__init__()
                self.links = []

            def handle_starttag(self, tag, attrs):
                if tag == "a":
                    self.links.append(dict(attrs))

        links = Links()
        links.feed(description)
        assert links.links == [
            {"href": target, "target": "_blank", "rel": "noopener noreferrer"}
        ]
    finally:
        hass.config_entries.flow.async_abort(result["flow_id"])
        await hass.async_block_till_done()


@pytest.mark.parametrize(
    "language,region_label,submit",
    [
        ("en", "Mainland China", "Connect"),
        ("zh-Hans", "中国大陆", "连接"),
    ],
)
async def test_native_selectors_and_connection_action(
    hass, cloud_api, language, region_label, submit
):
    from homeassistant.helpers.translation import async_get_translations

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "cloud"}
    )
    assert result["last_step"] is False
    region = next(iter(result["data_schema"].schema.values()))
    assert region.config["mode"] == "dropdown"
    assert region.config["translation_key"] == "region"
    translations = await async_get_translations(hass, language, "selector", {DOMAIN})
    assert (
        translations[f"component.{DOMAIN}.selector.region.options.cn"] == region_label
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"region": "cn"}
    )
    result = await finish_login(hass, result)
    device = next(iter(result["data_schema"].schema.values()))
    assert device.config["mode"] == "dropdown"
    assert device.config["options"] == [
        {"value": "mesh.new", "label": f"New curtain ({ADDRESS})"}
    ]
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"device": "mesh.new"}
    )
    assert result["last_step"] is True
    translations = await async_get_translations(hass, language, "config", {DOMAIN})
    assert (
        translations[f"component.{DOMAIN}.config.step.cloud_confirm.submit"] == submit
    )
    hass.config_entries.flow.async_abort(result["flow_id"])


@pytest.mark.parametrize("stage", ["start", "login", "key"])
@pytest.mark.parametrize("reason", ["cloud_auth", "cloud_network", "cloud_forbidden"])
async def test_cloud_error_categories_and_relogin(hass, cloud_api, stage, reason):
    error = cloud.CloudError("redacted", reason=reason)
    if stage == "start":
        cloud_api["start_qr_login"].side_effect = error
    elif stage == "login":
        cloud_api["wait_for_qr_scan"].side_effect = error
    result = await start(hass)
    if stage != "start":
        result = await finish_login(hass, result)
    if stage == "key":
        cloud_api["get_ltmk"].side_effect = error
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"device": "mesh.new"}
        )
    assert result["errors"] == {"base": reason}
    flow = hass.config_entries.flow._progress[result["flow_id"]]
    if stage == "key" and reason != "cloud_auth":
        assert result["step_id"] == "cloud_device"
        assert flow._cloud_session is not None
    else:
        assert result["step_id"] == "cloud_error"
        assert flow._cloud_session is None
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        assert result["step_id"] == "cloud"
    hass.config_entries.flow.async_abort(result["flow_id"])


@pytest.mark.parametrize("source", ["bluetooth", "user"])
async def test_network_retry_starts_fresh_session(hass, cloud_api, source):
    cloud_api["start_qr_login"].side_effect = [
        cloud.CloudError("network", reason="cloud_network"),
        QR,
    ]
    result = await start(hass, source)
    first_session = cloud_api["start_qr_login"].call_args.args[0]
    assert result["errors"] == {"base": "cloud_network"}
    assert first_session.closed
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"region": "cn"}
    )
    assert result["type"] == FlowResultType.SHOW_PROGRESS
    second_session = cloud_api["start_qr_login"].call_args.args[0]
    assert second_session is not first_session
    assert not second_session.closed
    hass.config_entries.flow.async_abort(result["flow_id"])
    await hass.async_block_till_done()
    assert second_session.closed
