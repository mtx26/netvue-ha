"""Offline tests for the pinned Home Assistant integration environment."""

import asyncio
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from aiohttp import ClientSession, web

from custom_components.netvue_local import async_setup_entry, async_unload_entry
from custom_components.netvue_local.bridge_api import (
    BridgeError, ManagedCamera, SessionStatus, async_stop_camera,
)
from custom_components.netvue_local.binary_sensor import NetvueStreamSensor
from custom_components.netvue_local.button import NetvueRestartButton
from custom_components.netvue_local.camera import NetvueCamera
from custom_components.netvue_local.config_flow import NetvueConfigFlow, NetvueOptionsFlow, _duplicate
from custom_components.netvue_local.coordinator import CameraData, NetvueCoordinator
from custom_components.netvue_local.rtsp import (
    async_probe_stream, stream_url_from_name, validate_host, validate_stream_url,
)
from custom_components.netvue_local.sensor import NetvueSessionSensor
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
class ConfigurationTests(unittest.TestCase):
    def test_urls_and_addresses(self):
        self.assertEqual(validate_host("192.168.10.2"), "192.168.10.2")
        self.assertEqual(validate_stream_url("rtsp://127.0.0.1:8554/netvue2"),
                         "rtsp://127.0.0.1:8554/netvue2")
        for url in ["rtsp://192.168.10.2:8554/x", "rtsp://user:pw@127.0.0.1:8554/x",
                    "rtsp://127.0.0.1:8554/x?secret=x", "http://127.0.0.1:8554/x",
                    "rtsp://127.0.0.1/x", "rtsp://127.0.0.1:8555/x",
                    "rtsp://127.0.0.1:8554/a/b", "rtsp://127.0.0.1:8554/x%0d"]:
            with self.assertRaises(ValueError):
                validate_stream_url(url)
        with self.assertRaises(ValueError):
            validate_host("8.8.8.8")

    def test_duplicates_follow_options_and_ignore_self(self):
        entry = SimpleNamespace(entry_id="1", data={"host": "192.168.10.2",
                                "stream_url": "rtsp://127.0.0.1:8554/a"},
                                options={"host": "192.168.10.3"})
        hass = SimpleNamespace(config_entries=SimpleNamespace(async_entries=lambda _: [entry]))
        self.assertTrue(_duplicate(hass, "192.168.10.3", "rtsp://127.0.0.1:8554/b"))
        self.assertTrue(_duplicate(hass, "192.168.10.4", "rtsp://127.0.0.1:8554/a"))
        self.assertFalse(_duplicate(hass, "192.168.10.2", "rtsp://127.0.0.1:8554/b"))
        self.assertFalse(_duplicate(hass, "192.168.10.3", "rtsp://127.0.0.1:8554/a", "1"))

    def test_stream_names(self):
        self.assertEqual(stream_url_from_name("netvue2"), "rtsp://127.0.0.1:8554/netvue2")
        self.assertEqual(stream_url_from_name("camera_2-test"), "rtsp://127.0.0.1:8554/camera_2-test")
        for name in ("", "a/b", "../a", "foo?x", "foo#bar", "foo bar", "foo%20", "é", "x\n"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                stream_url_from_name(name)

    def test_translations_have_matching_keys(self):
        base = Path(__file__).parents[1] / "custom_components" / "netvue_local"
        def keys(value):
            return {key: keys(item) for key, item in value.items()} if isinstance(value, dict) else None
        reference = keys(json.loads((base / "strings.json").read_text(encoding="utf-8")))
        for lang in ("fr", "en"):
            self.assertEqual(keys(json.loads((base / "translations" / f"{lang}.json")
                                             .read_text(encoding="utf-8"))), reference)


class RTSPTests(unittest.IsolatedAsyncioTestCase):
    async def test_h264_and_absent_stream(self):
        body = b"v=0\r\nm=video 0 RTP/AVP 96\r\na=rtpmap:96 H264/90000\r\n"

        def fake_connection(response):
            reader = asyncio.StreamReader()
            reader.feed_data(response)
            reader.feed_eof()
            writer = SimpleNamespace(write=unittest.mock.Mock(),
                                     drain=unittest.mock.AsyncMock(),
                                     close=unittest.mock.Mock(),
                                     wait_closed=unittest.mock.AsyncMock())
            return reader, writer

        available = (b"RTSP/1.0 200 OK\r\nContent-Type: application/sdp\r\nContent-Length: "
                     + str(len(body)).encode() + b"\r\n\r\n" + body)
        absent = b"RTSP/1.0 404 Not Found\r\nContent-Length: 0\r\n\r\n"
        successful_connection = fake_connection(available)
        missing_connection = fake_connection(absent)
        with patch("custom_components.netvue_local.rtsp.asyncio.open_connection",
                   new=unittest.mock.AsyncMock(side_effect=[
                       successful_connection, missing_connection,
                   ])) as connect:
            url = "rtsp://127.0.0.1:8554/netvue2"
            self.assertTrue(await async_probe_stream(url))
            self.assertFalse(await async_probe_stream(url))
        self.assertEqual(connect.await_args_list[0].args, ("127.0.0.1", 8554))
        self.assertNotIn(b"Authorization:", successful_connection[1].write.call_args.args[0])


class HomeAssistantApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_manual_flow_creates_a_unique_managed_stream(self):
        hass = SimpleNamespace(config_entries=SimpleNamespace(async_entries=lambda _: []))
        flow = NetvueConfigFlow()
        flow.hass = hass
        data = {"name": "Test", "host": "192.168.10.2"}
        form = await flow.async_step_user()
        self.assertEqual(form["step_id"], "user")
        self.assertEqual(form["data_schema"](data), data)
        invalid = await flow.async_step_user({**data, "host": "8.8.8.8"})
        self.assertEqual(invalid["errors"]["base"], "invalid_input")
        result = await flow.async_step_user(data)
        name = "netvue_192_168_10_2"
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["title"], "Test")
        self.assertEqual(result["data"], {
            "host": "192.168.10.2", "stream_name": name,
            "stream_url": f"rtsp://127.0.0.1:8554/{name}",
        })

    async def test_options_change_ip_and_duplicates(self):
        entry = SimpleNamespace(entry_id="1", data={"host": "192.168.10.2",
                                "stream_name": "a",
                                "stream_url": "rtsp://127.0.0.1:8554/a"}, options={})
        other = SimpleNamespace(entry_id="2", data={"host": "192.168.10.4",
                                "stream_name": "b",
                                "stream_url": "rtsp://127.0.0.1:8554/b"}, options={})
        hass = SimpleNamespace(config_entries=SimpleNamespace(
            async_entries=lambda _: [entry, other], async_get_known_entry=lambda _: entry))
        flow = NetvueOptionsFlow()
        flow.hass = hass
        flow.handler = "1"
        result = await flow.async_step_init({"host": "192.168.10.4"})
        self.assertEqual(result["errors"]["base"], "already_configured")
        with patch("custom_components.netvue_local.config_flow.async_stop_camera",
                   new=unittest.mock.AsyncMock()) as stop:
            result = await flow.async_step_init({"host": "192.168.10.3"})
        stop.assert_awaited_once_with(hass, "192.168.10.2")
        self.assertEqual(result["type"], "create_entry")
        name = "netvue_192_168_10_3"
        self.assertEqual(result["data"], {
            "host": "192.168.10.3", "stream_url": f"rtsp://127.0.0.1:8554/{name}",
            "stream_name": name,
        })

    async def test_options_preserve_requested_host_when_bridge_is_unavailable(self):
        entry = SimpleNamespace(entry_id="1", data={"host": "192.168.10.2",
                                "stream_name": "a",
                                "stream_url": "rtsp://127.0.0.1:8554/a"}, options={})
        hass = SimpleNamespace(config_entries=SimpleNamespace(
            async_entries=lambda _: [entry], async_get_known_entry=lambda _: entry))
        flow = NetvueOptionsFlow()
        flow.hass = hass
        flow.handler = "1"
        with patch("custom_components.netvue_local.config_flow.async_stop_camera",
                   new=unittest.mock.AsyncMock(side_effect=BridgeError("unavailable"))):
            result = await flow.async_step_init({"host": "192.168.10.3"})
        self.assertEqual(result["errors"]["base"], "bridge_unavailable")
        self.assertEqual(result["data_schema"]({}), {"host": "192.168.10.3"})

    async def test_setup_unload_and_camera(self):
        entry = SimpleNamespace(entry_id="1", title="Fake",
                                data={"host": "192.168.10.2", "stream_name": "a",
                                      "stream_url": "rtsp://127.0.0.1:8554/a"}, options={})
        entries = SimpleNamespace(async_forward_entry_setups=unittest.mock.AsyncMock(),
                                  async_unload_platforms=unittest.mock.AsyncMock(return_value=True))
        hass = SimpleNamespace(config_entries=entries)
        coordinator = SimpleNamespace(data=CameraData(SessionStatus("supervising", None), True),
                                      last_update_success=True,
                                      async_refresh=unittest.mock.AsyncMock(),
                                      camera=SimpleNamespace(
                                          async_reconcile=unittest.mock.AsyncMock()))
        with patch("custom_components.netvue_local.NetvueCoordinator",
                   return_value=coordinator):
            self.assertTrue(await async_setup_entry(hass, entry))
            coordinator.camera.async_reconcile.assert_awaited_once_with()
        camera = NetvueCamera(entry, coordinator)
        self.assertEqual(await camera.stream_source(), entry.data["stream_url"])
        self.assertTrue(camera.available)
        self.assertEqual(camera.stream_options["rtsp_transport"], "tcp")
        sensor = NetvueStreamSensor(entry, coordinator)
        self.assertTrue(sensor.is_on)
        coordinator.data = CameraData(SessionStatus("failed", "Authentication rejected"), False)
        self.assertFalse(camera.available)
        self.assertFalse(sensor.is_on)
        with patch("custom_components.netvue_local.async_stop_camera",
                   new=unittest.mock.AsyncMock()) as stop:
            self.assertTrue(await async_unload_entry(hass, entry))
        stop.assert_awaited_once_with(hass, "192.168.10.2")
        entries.async_unload_platforms.assert_awaited_once()

    async def test_session_diagnostic_and_explicit_restart(self):
        entry = SimpleNamespace(entry_id="1", title="Fake")
        managed = SimpleNamespace(async_start=unittest.mock.AsyncMock())
        coordinator = SimpleNamespace(
            data=CameraData(SessionStatus("failed", "Camera authentication rejected"), False),
            last_update_success=True, camera=managed,
            async_request_refresh=unittest.mock.AsyncMock(),
        )
        sensor = NetvueSessionSensor(entry, coordinator)
        button = NetvueRestartButton(entry, coordinator)
        self.assertTrue(sensor.available)
        self.assertEqual(sensor.native_value, "failed")
        self.assertEqual(sensor.extra_state_attributes, {"error": "Camera authentication rejected"})
        self.assertTrue(button.available)
        await button.async_press()
        managed.async_start.assert_awaited_once()
        coordinator.async_request_refresh.assert_awaited_once()

        coordinator.data = CameraData(SessionStatus("supervising", None), True)
        self.assertFalse(button.available)
        self.assertEqual(sensor.extra_state_attributes, {})
        coordinator.data = CameraData(SessionStatus("stopped", None), False)
        self.assertTrue(button.available)
        coordinator.data = CameraData(None, True)
        self.assertFalse(sensor.available)
        self.assertFalse(button.available)
        self.assertIsNone(sensor.native_value)
        self.assertEqual(sensor.extra_state_attributes, {})

        managed.async_start.side_effect = BridgeError("Bridge returned HTTP 503")
        with self.assertRaisesRegex(HomeAssistantError, "HTTP 503"):
            await button.async_press()

    async def test_unload_preserves_entry_when_bridge_cannot_delete_camera(self):
        entry = SimpleNamespace(
            data={
                "host": "192.168.10.2",
                "stream_name": "a",
                "stream_url": "rtsp://127.0.0.1:8554/a",
            },
            options={},
        )
        entries = SimpleNamespace(
            async_unload_platforms=unittest.mock.AsyncMock(return_value=True)
        )
        hass = SimpleNamespace(config_entries=entries)
        with patch(
            "custom_components.netvue_local.async_stop_camera",
            new=unittest.mock.AsyncMock(side_effect=BridgeError("offline")),
        ) as stop:
            self.assertFalse(await async_unload_entry(hass, entry))
        stop.assert_awaited_once_with(hass, "192.168.10.2")
        entries.async_unload_platforms.assert_not_awaited()

    async def test_setup_rejects_entry_without_managed_stream_name(self):
        entry = SimpleNamespace(data={"host": "192.168.10.2",
                                      "stream_url": "rtsp://127.0.0.1:8554/legacy"},
                                options={})
        with self.assertRaisesRegex(ConfigEntryNotReady, "unsupported configuration"):
            await async_setup_entry(SimpleNamespace(), entry)

class ManagerContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.host = "192.168.10.2"
        self.name = "netvue_192_168_10_2"
        self.record = {
            "host": self.host, "stream_name": self.name,
            "rtsp_url": f"rtsp://127.0.0.1:8554/{self.name}", "state": "supervising",
        }
        self.current = None
        self.requests = []
        self.post_started = asyncio.Event()
        self.post_continue = asyncio.Event()
        self.delay_post = False
        self.get_started = asyncio.Event()
        self.get_continue = asyncio.Event()
        self.delay_get = False
        self.start_started = asyncio.Event()
        self.start_continue = asyncio.Event()
        self.delay_start = False
        self.post_status = 202
        self.invalid_json = False
        self.status = {}
        self.body = None

        async def handler(request):
            self.requests.append((request.method, request.path))
            if request.path.endswith("/start"):
                if self.status.get("START") is not None:
                    return web.Response(status=self.status["START"])
                self.start_started.set()
                if self.delay_start:
                    await self.start_continue.wait()
                if self.current is None:
                    return web.Response(status=404)
                self.current["state"] = "supervising"
                self.current.pop("error", None)
                return web.json_response(self.current, status=202)
            status = self.status.get(request.method)
            if status is not None:
                return web.Response(status=status)
            if request.method == "GET":
                self.get_started.set()
                if self.delay_get:
                    await self.get_continue.wait()
                if self.current is None:
                    return web.Response(status=404)
                if self.invalid_json:
                    return web.Response(text="{", content_type="application/json")
                return web.json_response(self.current)
            if request.method == "POST":
                self.body = await request.json()
                self.post_started.set()
                if self.delay_post:
                    await self.post_continue.wait()
                self.current = dict(self.record)
                return web.json_response(self.current, status=self.post_status)
            existed = self.current is not None
            self.current = None
            return web.Response(status=200 if existed else 404)

        app = web.Application()
        app.router.add_route("*", "/api/v1/cameras", handler)
        app.router.add_route("*", "/api/v1/cameras/{host}", handler)
        app.router.add_route("*", "/api/v1/cameras/{host}/start", handler)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await self.site.start()
        port = self.site._server.sockets[0].getsockname()[1]
        self.client = ClientSession()
        self.hass = SimpleNamespace(data={})
        self.api_patch = patch("custom_components.netvue_local.bridge_api.API_URL",
                               f"http://127.0.0.1:{port}/api/v1")
        self.session_patch = patch(
            "custom_components.netvue_local.bridge_api.async_get_clientsession",
            return_value=self.client,
        )
        self.api_patch.start()
        self.session_patch.start()
        self.camera = ManagedCamera(self.hass, self.host, self.name)

    async def asyncTearDown(self):
        self.post_continue.set()
        self.get_continue.set()
        self.start_continue.set()
        await self.client.close()
        await self.runner.cleanup()
        self.session_patch.stop()
        self.api_patch.stop()

    async def test_restore_after_restart_and_do_not_restart_existing_states(self):
        await self.camera.async_reconcile()
        self.assertEqual(self.body, {"host": self.host, "stream_name": self.name})
        self.assertEqual([method for method, _ in self.requests], ["GET", "POST"])
        for state in ("supervising", "stopped", "failed"):
            self.current["state"] = state
            status = await self.camera.async_reconcile()
            self.assertEqual(status.state, state)
        self.assertEqual([method for method, _ in self.requests],
                         ["GET", "POST", "GET", "GET", "GET"])
        self.current = None  # The manager restarted.
        await self.camera.async_reconcile()
        await self.camera.async_reconcile()
        self.assertEqual([method for method, _ in self.requests][-3:],
                         ["GET", "POST", "GET"])

    async def test_delayed_post_then_delete_and_waiting_refresh(self):
        self.delay_post = True
        refresh = asyncio.create_task(self.camera.async_reconcile())
        await asyncio.wait_for(self.post_started.wait(), 2)
        delete = asyncio.create_task(async_stop_camera(self.hass, self.host))
        await asyncio.sleep(0)
        waiting_refresh = asyncio.create_task(self.camera.async_reconcile())
        await asyncio.sleep(0)
        self.assertFalse(delete.done())
        self.post_continue.set()
        await asyncio.gather(refresh, delete, waiting_refresh)
        await self.camera.async_reconcile()
        self.assertIsNone(self.current)
        self.assertEqual([method for method, _ in self.requests], ["GET", "POST", "DELETE"])
        await async_stop_camera(self.hass, self.host)  # 404 is idempotent.

    async def test_delete_after_get_removes_session_and_invalidates_refresh(self):
        self.delay_get = True
        refresh = asyncio.create_task(self.camera.async_reconcile())
        await asyncio.wait_for(self.get_started.wait(), 2)
        delete = asyncio.create_task(async_stop_camera(self.hass, self.host))
        await asyncio.sleep(0)
        self.get_continue.set()
        await asyncio.gather(refresh, delete)
        self.assertIsNone(self.current)
        await self.camera.async_reconcile()
        self.assertEqual(
            [method for method, _ in self.requests],
            ["GET", "POST", "DELETE"],
        )

    async def test_post_200_and_bad_json(self):
        self.post_status = 200
        await self.camera.async_reconcile()
        self.invalid_json = True
        with self.assertRaisesRegex(BridgeError, "invalid camera JSON"):
            await self.camera.async_reconcile()

    async def test_errors_and_inconsistent_json_are_visible(self):
        self.status["GET"] = 503
        with self.assertRaisesRegex(BridgeError, "HTTP 503"):
            await self.camera.async_reconcile()
        self.status.clear()
        self.current = {**self.record, "stream_name": "other"}
        with self.assertRaisesRegex(BridgeError, "inconsistent"):
            await self.camera.async_reconcile()
        self.assertNotIn("POST", [method for method, _ in self.requests])
        self.status["DELETE"] = 500
        with self.assertRaisesRegex(BridgeError, "HTTP 500"):
            await async_stop_camera(self.hass, self.host)

    async def test_failed_delete_does_not_disable_future_reconciliation(self):
        await self.camera.async_reconcile()
        self.status["DELETE"] = 500
        with self.assertRaisesRegex(BridgeError, "HTTP 500"):
            await async_stop_camera(self.hass, self.host)

        self.status.clear()
        self.current = None
        await self.camera.async_reconcile()
        self.assertEqual(
            [method for method, _ in self.requests],
            ["GET", "POST", "DELETE", "GET", "POST"],
        )

    async def test_invalid_url_or_state_does_not_trigger_post(self):
        for field, value in (("rtsp_url", "rtsp://127.0.0.1:8554/other"),
                             ("state", ""), ("state", None), ("state", "running"),
                             ("error", 42)):
            with self.subTest(field=field, value=value):
                self.current = {**self.record, field: value}
                with self.assertRaisesRegex(BridgeError, "inconsistent"):
                    await self.camera.async_reconcile()
        self.assertEqual([method for method, _ in self.requests], ["GET"] * 5)

    async def test_manual_start_and_failure_are_visible(self):
        self.current = {**self.record, "state": "failed", "error": "Camera authentication rejected"}
        self.assertEqual(await self.camera.async_reconcile(),
                         SessionStatus("failed", "Camera authentication rejected"))
        self.assertEqual(await self.camera.async_start(), SessionStatus("supervising", None))
        self.assertEqual([method for method, _ in self.requests], ["GET", "POST"])
        self.assertTrue(self.requests[-1][1].endswith("/start"))
        self.status["START"] = 503
        with self.assertRaisesRegex(BridgeError, "HTTP 503"):
            await self.camera.async_start()
        self.status.clear()
        self.current = None
        with self.assertRaisesRegex(BridgeError, "HTTP 404"):
            await self.camera.async_start()
        self.assertIsNone(self.current)

    async def test_start_is_serialized_with_removal(self):
        self.current = {**self.record, "state": "failed"}
        self.delay_start = True
        restart = asyncio.create_task(self.camera.async_start())
        await asyncio.wait_for(self.start_started.wait(), 2)
        delete = asyncio.create_task(async_stop_camera(self.hass, self.host))
        await asyncio.sleep(0)
        self.assertFalse(delete.done())
        self.start_continue.set()
        await asyncio.gather(restart, delete)
        with self.assertRaisesRegex(BridgeError, "removed or replaced"):
            await self.camera.async_start()
        self.assertIsNone(self.current)
        self.assertEqual([method for method, _ in self.requests], ["POST", "DELETE"])

    async def test_each_coordinator_update_reconciles_then_probes(self):
        url = self.record["rtsp_url"]
        coordinator = SimpleNamespace(camera=self.camera, url=url)
        with patch("custom_components.netvue_local.coordinator.async_probe_stream",
                   new=unittest.mock.AsyncMock(return_value=True)) as probe:
            result = await NetvueCoordinator._async_update_data(coordinator)
            self.assertEqual(result, CameraData(SessionStatus("supervising", None), True))
            self.current = None
            self.assertEqual(await NetvueCoordinator._async_update_data(coordinator), result)
        self.assertEqual([method for method, _ in self.requests], ["GET", "POST", "GET", "POST"])
        self.assertEqual(probe.await_count, 2)

    async def test_coordinator_probes_sdp_even_if_manager_fails(self):
        entry = SimpleNamespace(data={"stream_url": self.record["rtsp_url"]}, options={})
        # Exercise the update method without constructing HA's scheduler.
        coordinator = SimpleNamespace(camera=self.camera, url=entry.data["stream_url"])
        self.status["GET"] = 503
        with self.assertLogs("custom_components.netvue_local.coordinator", level="WARNING"), \
                patch("custom_components.netvue_local.coordinator.async_probe_stream",
                      new=unittest.mock.AsyncMock(return_value=True)) as probe:
            self.assertEqual(await NetvueCoordinator._async_update_data(coordinator),
                             CameraData(None, True))
        probe.assert_awaited_once_with(entry.data["stream_url"])

if __name__ == "__main__":
    unittest.main()
