"""Real Home Assistant tests: flows, entities, lifecycle, image, WebRTC, diagnostics.

Run from the repository root; see tests/ha/harness.py for the isolation model.
"""

import asyncio
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image
from homeassistant.components import camera
from homeassistant.components.camera.webrtc import DATA_WEBRTC_PROVIDERS
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_COMPONENT_LOADED
from homeassistant.data_entry_flow import InvalidData
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

import custom_components.netvue_local as netvue_local
from custom_components.netvue_local import camera as camera_platform
from custom_components.netvue_local.const import DOMAIN
from custom_components.netvue_local.webrtc import NetvueWebRTCProvider
from harness import HOST, STREAM_NAME, HassTestCase

OTHER_HOST = "192.168.10.3"
MIC_OFFER = "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=sendonly\r\n"


def _jpeg(size=(160, 90)) -> bytes:
    out = BytesIO()
    Image.new("RGB", size, "white").save(out, format="JPEG")
    return out.getvalue()


class _ControlledSleeper:
    def __init__(self) -> None:
        self.calls = asyncio.Queue()
        self.releases = asyncio.Queue()

    async def sleep(self, delay: float) -> None:
        self.calls.put_nowait(delay)
        await self.releases.get()


class ConfigFlowTests(HassTestCase):
    async def test_manual_flow_creates_entry_and_managed_session(self):
        flow = self.hass.config_entries.flow
        result = await flow.async_init(DOMAIN, context={"source": "user"})
        self.assertEqual((result["type"], result["step_id"]), ("menu", "user"))
        result = await flow.async_configure(result["flow_id"], {"next_step_id": "manual"})
        self.assertEqual(result["step_id"], "manual")
        result = await flow.async_configure(
            result["flow_id"], {"name": "Salon", "host": HOST})
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["title"], "Salon")
        self.assertEqual(result["data"], {
            "host": HOST, "stream_name": STREAM_NAME,
            "stream_url": f"rtsp://127.0.0.1:8554/{STREAM_NAME}",
        })
        await self.hass.async_block_till_done()
        entry = result["result"]
        self.assertEqual(entry.state, ConfigEntryState.LOADED)
        self.assertEqual(self.bridge.cameras[HOST]["stream_name"], STREAM_NAME)

    async def test_manual_flow_rejects_invalid_input_without_touching_bridge(self):
        flow = self.hass.config_entries.flow
        for host, name in (("8.8.8.8", "Cam"), ("not-an-ip", "Cam"), ("", "Cam"),
                           ("192.168.10.999", "Cam"), (HOST, "   ")):
            with self.subTest(host=host, name=name):
                result = await flow.async_init(DOMAIN, context={"source": "user"})
                result = await flow.async_configure(
                    result["flow_id"], {"next_step_id": "manual"})
                result = await flow.async_configure(
                    result["flow_id"], {"name": name, "host": host})
                self.assertEqual(result["type"], "form")
                self.assertEqual(result["errors"], {"base": "invalid_input"})
                flow.async_abort(result["flow_id"])
        self.assertEqual(self.hass.config_entries.async_entries(DOMAIN), [])
        self.assertEqual(self.bridge.requests, [])

    async def test_duplicate_host_is_rejected(self):
        await self.create_entry()
        flow = self.hass.config_entries.flow
        result = await flow.async_init(DOMAIN, context={"source": "user"})
        result = await flow.async_configure(result["flow_id"], {"next_step_id": "manual"})
        result = await flow.async_configure(
            result["flow_id"], {"name": "Other", "host": HOST})
        self.assertEqual(result["errors"], {"base": "already_configured"})
        self.assertEqual(len(self.hass.config_entries.async_entries(DOMAIN)), 1)

    async def test_lan_discovery_lists_cameras_and_starts_nothing_until_selected(self):
        self.bridge.discovered = [HOST, OTHER_HOST]
        flow = self.hass.config_entries.flow
        result = await flow.async_init(DOMAIN, context={"source": "user"})
        result = await flow.async_configure(result["flow_id"], {"next_step_id": "discovery"})
        self.assertEqual((result["type"], result["step_id"]), ("form", "select"))
        self.assertEqual(self.bridge.cameras, {})
        self.assertEqual(self.bridge.requests, [("POST", "/api/v1/discovery")])
        # Only discovered addresses are selectable.
        with self.assertRaises(InvalidData):
            await flow.async_configure(
                result["flow_id"], {"name": "X", "host": "192.168.10.9", "manual": False})
        result = await flow.async_configure(
            result["flow_id"], {"name": "Cuisine", "host": OTHER_HOST, "manual": False})
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["data"]["host"], OTHER_HOST)
        await self.hass.async_block_till_done()
        self.assertEqual(list(self.bridge.cameras), [OTHER_HOST])

    async def test_lan_discovery_skips_already_configured_host(self):
        await self.create_entry()
        self.bridge.discovered = [HOST]
        flow = self.hass.config_entries.flow
        result = await flow.async_init(DOMAIN, context={"source": "user"})
        result = await flow.async_configure(result["flow_id"], {"next_step_id": "discovery"})
        result = await flow.async_configure(
            result["flow_id"], {"name": "Again", "host": HOST, "manual": False})
        self.assertEqual(result["errors"], {"base": "already_configured"})

    async def test_lan_discovery_errors_then_manual_fallback(self):
        flow = self.hass.config_entries.flow
        for label, prepare, error in (
            ("empty", lambda: None, "no_cameras"),
            ("http_503", lambda: setattr(self.bridge, "status_override", 503),
             "discovery_failed"),
        ):
            with self.subTest(label):
                prepare()
                result = await flow.async_init(DOMAIN, context={"source": "user"})
                result = await flow.async_configure(
                    result["flow_id"], {"next_step_id": "discovery"})
                self.assertEqual(result["step_id"], "discovery")
                self.assertEqual(result["errors"], {"base": error})
                flow.async_abort(result["flow_id"])
        self.bridge.status_override = None
        await self.bridge.stop()  # network error: connection refused
        result = await flow.async_init(DOMAIN, context={"source": "user"})
        result = await flow.async_configure(result["flow_id"], {"next_step_id": "discovery"})
        self.assertEqual(result["errors"], {"base": "discovery_failed"})
        result = await flow.async_configure(result["flow_id"], {"manual": True})
        self.assertEqual(result["step_id"], "manual")


class OptionsFlowTests(HassTestCase):
    async def _init(self, entry, **values):
        manager = self.hass.config_entries.options
        result = await manager.async_init(entry.entry_id)
        self.assertEqual(result["step_id"], "init")
        return await manager.async_configure(result["flow_id"], values)

    async def test_host_change_moves_session_and_reloads_entry(self):
        entry = await self.create_entry()
        camera_entity = self.entity_id(entry, "camera")
        result = await self._init(entry, host=OTHER_HOST)
        self.assertEqual(result["type"], "create_entry")
        await self.hass.async_block_till_done()
        self.assertEqual(entry.state, ConfigEntryState.LOADED)
        self.assertEqual(list(self.bridge.cameras), [OTHER_HOST])
        self.assertEqual(self.bridge.cameras[OTHER_HOST]["stream_name"], "netvue_192_168_10_3")
        self.assertEqual(self.entity_id(entry, "camera"), camera_entity)

    async def test_host_change_retries_after_bridge_recovers(self):
        entry = await self.create_entry()
        stop_camera = netvue_local.async_stop_camera

        async def stop_bridge_after_unload(hass, host):
            await stop_camera(hass, host)
            await self.bridge.stop()

        with patch.object(netvue_local, "async_stop_camera",
                          side_effect=stop_bridge_after_unload):
            result = await self._init(entry, host=OTHER_HOST)
            self.assertEqual(result["type"], "create_entry")
            await self.hass.async_block_till_done()

            self.assertEqual(entry.options["host"], OTHER_HOST)
            self.assertEqual(entry.state, ConfigEntryState.SETUP_RETRY)
            self.assertIn(("DELETE", f"/api/v1/cameras/{HOST}"), self.bridge.requests)
            self.assertNotIn(HOST, self.bridge.cameras)
            self.assertNotIn(OTHER_HOST, self.bridge.cameras)

            await self.bridge.start()
            await self.hass.config_entries.async_reload(entry.entry_id)
            await self.hass.async_block_till_done()

        self.assertEqual(entry.state, ConfigEntryState.LOADED)
        self.assertNotIn(HOST, self.bridge.cameras)
        self.assertIn(OTHER_HOST, self.bridge.cameras)
        self.assertIn(("POST", "/api/v1/cameras"), self.bridge.requests)

    async def test_option_validation(self):
        entry = await self.create_entry()
        other = await self.create_entry(host=OTHER_HOST, name="Other")
        self.assertEqual(other.state, ConfigEntryState.LOADED)
        cases = {
            "invalid_input": {"host": "8.8.8.8"},
            "already_configured": {"host": OTHER_HOST},
        }
        for error, values in cases.items():
            with self.subTest(error):
                result = await self._init(entry, **values)
                self.assertEqual(result["errors"], {"base": error})
        self.assertEqual(set(self.bridge.cameras), {HOST, OTHER_HOST})

    async def test_host_change_is_refused_while_bridge_unavailable(self):
        entry = await self.create_entry()
        self.bridge.status_override = 503
        result = await self._init(entry, host=OTHER_HOST)
        self.assertEqual(result["errors"], {"base": "bridge_unavailable"})
        self.assertEqual(entry.options, {})
        self.assertIn(HOST, self.bridge.cameras)


class EntityTests(HassTestCase):
    async def test_entities_device_and_initial_states(self):
        entry = await self.create_entry(name="Salon")
        registry = er.async_get(self.hass)
        entities = er.async_entries_for_config_entry(registry, entry.entry_id)
        self.assertEqual(
            sorted((e.domain, e.unique_id.removeprefix(entry.entry_id)) for e in entities),
            [("binary_sensor", "_rtsp"), ("binary_sensor", "_unavailable"),
             ("button", "_restart"), ("camera", ""), ("sensor", "_diagnostic"),
             ("sensor", "_session")],
        )
        device = dr.async_entries_for_config_entry(dr.async_get(self.hass), entry.entry_id)
        self.assertEqual([(d.name, d.manufacturer) for d in device], [("Salon", "Netvue")])
        self.assertEqual(self.state(entry, "binary_sensor", "rtsp").state, "on")
        self.assertEqual(self.state(entry, "binary_sensor", "unavailable").state, "off")
        self.assertEqual(self.state(entry, "sensor", "session").state, "supervising")
        self.assertEqual(self.state(entry, "sensor", "diagnostic").state, "healthy")
        # Restart is only offered for a stopped or failed session.
        self.assertEqual(self.state(entry, "button", "restart").state, "unavailable")
        entity = camera.get_camera_from_entity_id(self.hass, self.entity_id(entry, "camera"))
        self.assertEqual(await entity.stream_source(), f"rtsp://127.0.0.1:8554/{STREAM_NAME}")
        self.assertEqual(entity.stream_options["rtsp_transport"], "tcp")

    async def test_camera_image_and_badge(self):
        entry = await self.create_entry()
        entity_id = self.entity_id(entry, "camera")
        source = _jpeg()
        with patch("custom_components.netvue_local.camera.async_get_stream_image",
                   new=AsyncMock(return_value=source)) as get_image:
            full = await camera.async_get_image(self.hass, entity_id)
            badge = await camera.async_get_image(self.hass, entity_id, width=80, height=80)
        self.assertEqual(full.content, source)
        with Image.open(BytesIO(badge.content)) as image:
            self.assertEqual(image.size, (80, 80))
        self.assertEqual(get_image.await_count, 2)

    async def test_camera_image_unavailable_raises_instead_of_fake_image(self):
        entry = await self.create_entry()
        entity_id = self.entity_id(entry, "camera")
        with patch("custom_components.netvue_local.camera.async_get_stream_image",
                   new=AsyncMock(return_value=None)):
            with self.assertRaises(HomeAssistantError):
                await camera.async_get_image(self.hass, entity_id)
        self.rtsp_available = False
        await self.refresh(entry)
        with self.assertRaises(HomeAssistantError):
            await camera.async_get_image(self.hass, entity_id)

    async def test_rtsp_loss_marks_camera_unavailable_and_recovers(self):
        entry = await self.create_entry()
        self.rtsp_available = False
        await self.refresh(entry)
        self.assertEqual(self.state(entry, "camera").state, "unavailable")
        self.assertEqual(self.state(entry, "binary_sensor", "rtsp").state, "off")
        self.assertEqual(self.state(entry, "binary_sensor", "unavailable").state, "on")
        self.assertEqual(self.state(entry, "sensor", "diagnostic").state, "stream_unavailable")
        self.rtsp_available = True
        await self.refresh(entry)
        self.assertNotEqual(self.state(entry, "camera").state, "unavailable")
        self.assertEqual(self.state(entry, "sensor", "diagnostic").state, "healthy")

    async def test_bridge_outage_does_not_hide_a_live_rtsp_stream(self):
        entry = await self.create_entry()
        await self.bridge.stop()
        await self.refresh(entry)
        self.assertEqual(entry.state, ConfigEntryState.LOADED)
        self.assertEqual(self.state(entry, "sensor", "session").state, "unavailable")
        self.assertEqual(self.state(entry, "button", "restart").state, "unavailable")
        self.assertEqual(self.state(entry, "sensor", "diagnostic").state, "bridge_unavailable")
        self.assertEqual(self.state(entry, "binary_sensor", "rtsp").state, "on")
        self.assertNotEqual(self.state(entry, "camera").state, "unavailable")
        await self.bridge.start()
        await self.refresh(entry)
        self.assertEqual(self.state(entry, "sensor", "session").state, "supervising")
        self.assertEqual(self.state(entry, "sensor", "diagnostic").state, "healthy")

    async def test_failed_session_is_diagnosed_not_restarted_and_button_restarts_it(self):
        entry = await self.create_entry()
        self.bridge.cameras[HOST].update(
            state="failed", error="Camera authentication rejected [authentication]")
        await self.refresh(entry)
        session = self.state(entry, "sensor", "session")
        self.assertEqual(session.state, "failed")
        self.assertEqual(session.attributes["error"],
                         "Camera authentication rejected [authentication]")
        diagnostic = self.state(entry, "sensor", "diagnostic")
        self.assertEqual(diagnostic.state, "authentication_failed")
        self.assertIn("authentication", diagnostic.attributes["details"])
        self.assertNotIn(("POST", f"/api/v1/cameras/{HOST}/start"), self.bridge.requests)
        button = self.entity_id(entry, "button", "restart")
        self.assertNotEqual(self.hass.states.get(button).state, "unavailable")

        self.bridge.status_override = 503
        with self.assertRaisesRegex(HomeAssistantError, "HTTP 503"):
            await self.hass.services.async_call(
                "button", "press", {"entity_id": button}, blocking=True)
        self.bridge.status_override = None
        await self.hass.services.async_call(
            "button", "press", {"entity_id": button}, blocking=True)
        await self.hass.async_block_till_done()
        self.assertEqual(self.state(entry, "sensor", "session").state, "supervising")
        self.assertEqual(self.state(entry, "button", "restart").state, "unavailable")
        self.assertEqual(self.bridge.requests.count(
            ("POST", f"/api/v1/cameras/{HOST}/start")), 2)

    async def test_restart_button_follows_session_state(self):
        entry = await self.create_entry()
        self.assertEqual(self.state(entry, "button", "restart").state, "unavailable")
        self.bridge.cameras[HOST]["state"] = "stopped"
        await self.refresh(entry)
        self.assertEqual(self.state(entry, "sensor", "diagnostic").state, "session_stopped")
        self.assertEqual(self.state(entry, "button", "restart").state, "unknown")


class LifecycleTests(HassTestCase):
    async def test_harness_never_opens_ha_listeners(self):
        await self.create_entry()
        self.assertIsNone(self.hass.http._server)
        self.assertNotIn("zeroconf", self.hass.config.components)

    async def test_setup_retries_when_bridge_is_down_then_loads_on_reload(self):
        await self.bridge.stop()
        entry = await self.create_entry()
        self.assertEqual(entry.state, ConfigEntryState.SETUP_RETRY)
        self.assertEqual(er.async_entries_for_config_entry(
            er.async_get(self.hass), entry.entry_id), [])
        await self.bridge.start()
        await self.hass.config_entries.async_reload(entry.entry_id)
        await self.hass.async_block_till_done()
        self.assertEqual(entry.state, ConfigEntryState.LOADED)
        self.assertEqual(self.state(entry, "sensor", "session").state, "supervising")

    async def test_unsupported_entry_data_is_not_loaded_nor_sent_to_bridge(self):
        entry = await self.add_raw_entry({
            "host": HOST, "stream_name": STREAM_NAME,
            "stream_url": "rtsp://192.168.10.2:8554/x"})
        self.assertEqual(entry.state, ConfigEntryState.SETUP_RETRY)
        self.assertEqual(self.bridge.requests, [])
        self.assertEqual(self.hass.states.async_entity_ids(), [])

    async def test_reload_keeps_one_session_and_same_entities(self):
        entry = await self.create_entry()
        before = self.entity_id(entry, "camera")
        old = entry.runtime_data
        await self.hass.config_entries.async_reload(entry.entry_id)
        await self.hass.async_block_till_done()
        self.assertEqual(entry.state, ConfigEntryState.LOADED)
        self.assertIsNot(entry.runtime_data, old)
        self.assertEqual(self.entity_id(entry, "camera"), before)
        self.assertEqual(list(self.bridge.cameras), [HOST])
        self.assertEqual(len(er.async_entries_for_config_entry(
            er.async_get(self.hass), entry.entry_id)), 6)

    async def test_removal_deletes_session_entities_and_device(self):
        entry = await self.create_entry()
        entry_id = entry.entry_id
        await self.hass.config_entries.async_remove(entry_id)
        await self.hass.async_block_till_done()
        self.assertEqual(self.bridge.cameras, {})
        self.assertEqual(er.async_entries_for_config_entry(
            er.async_get(self.hass), entry_id), [])
        self.assertEqual(dr.async_entries_for_config_entry(
            dr.async_get(self.hass), entry_id), [])
        self.assertEqual(self.hass.states.async_entity_ids("camera"), [])

    async def test_removal_of_one_camera_leaves_the_other_session(self):
        first = await self.create_entry()
        await self.create_entry(host=OTHER_HOST, name="Other")
        await self.hass.config_entries.async_remove(first.entry_id)
        await self.hass.async_block_till_done()
        self.assertEqual(list(self.bridge.cameras), [OTHER_HOST])

    async def test_unload_failure_keeps_session_and_entities(self):
        entry = await self.create_entry()
        self.bridge.status_override = 503
        self.assertFalse(await self.hass.config_entries.async_unload(entry.entry_id))
        await self.hass.async_block_till_done()
        self.assertEqual(entry.state, ConfigEntryState.FAILED_UNLOAD)
        self.assertIn(HOST, self.bridge.cameras)
        for domain in ("camera", "binary_sensor", "sensor", "button"):
            self.assertTrue(self.hass.states.async_entity_ids(domain), domain)


class WebRTCTests(HassTestCase):
    def providers(self):
        return self.hass.data.get(DATA_WEBRTC_PROVIDERS, set())

    async def test_turn_is_private_to_native_netvue_camera(self):
        server = {
            "urls": ["turns:turn.cloudflare.com:443?transport=tcp"],
            "username": "temporary-user", "credential": "temporary-password",
        }
        self.bridge.turn_response = {"ice_servers": [server], "expires_in": 1800}
        entry = await self.create_entry()
        entity = camera.get_camera_from_entity_id(self.hass, self.entity_id(entry, "camera"))
        self.assertEqual(self.providers(), set())
        self.assertEqual(entity.camera_capabilities.frontend_stream_types, {camera.StreamType.WEB_RTC})
        config = entity.async_get_webrtc_client_configuration().to_frontend_dict()
        self.assertIn(server, config["configuration"]["iceServers"])

        messages = []
        await entity.async_handle_async_webrtc_offer(MIC_OFFER, "relayed", messages.append)
        entity.close_webrtc_session("relayed")
        async with asyncio.timeout(3):
            while ("DELETE", f"/api/v1/cameras/{HOST}/webrtc/relayed") not in self.bridge.requests:
                await asyncio.sleep(0.01)

    async def test_turn_refresh_expiry_and_unload_cancellation_follow_entity_lifecycle(self):
        first_server = {
            "urls": ["turns:first.example:443?transport=tcp"],
            "username": "first-user",
            "credential": "first-password",
        }
        refreshed_server = {
            "urls": ["turns:refreshed.example:443?transport=tcp"],
            "username": "refreshed-user",
            "credential": "refreshed-password",
        }
        self.bridge.turn_response = {"ice_servers": [first_server], "expires_in": 60}
        now = [100.0]
        sleeper = _ControlledSleeper()
        refresh_results = asyncio.Queue()
        get_credentials = camera_platform.async_get_turn_credentials

        async def observe_refresh(hass):
            result = await get_credentials(hass)
            refresh_results.put_nowait(result)
            return result

        with (
            patch.object(camera_platform, "asyncio", SimpleNamespace(
                sleep=sleeper.sleep, gather=asyncio.gather,
            )),
            patch.object(camera_platform, "async_get_turn_credentials", observe_refresh),
            patch("custom_components.netvue_local.webrtc.monotonic",
                  side_effect=lambda: now[0]),
        ):
            entry = await self.create_entry()
            async with asyncio.timeout(3):
                await refresh_results.get()
                self.assertEqual(await sleeper.calls.get(), 60)

            entity = camera.get_camera_from_entity_id(
                self.hass, self.entity_id(entry, "camera"),
            )
            config = entity.async_get_webrtc_client_configuration().to_frontend_dict()
            self.assertIn(first_server, config["configuration"]["iceServers"])

            now[0] = 160.0
            expired = entity.async_get_webrtc_client_configuration().to_frontend_dict()
            self.assertNotIn(first_server, expired["configuration"]["iceServers"])

            self.bridge.turn_response = {
                "ice_servers": [refreshed_server],
                "expires_in": 1800,
            }
            sleeper.releases.put_nowait(None)
            async with asyncio.timeout(3):
                await refresh_results.get()
                self.assertEqual(await sleeper.calls.get(), 60)

            refreshed = entity.async_get_webrtc_client_configuration().to_frontend_dict()
            self.assertIn(refreshed_server, refreshed["configuration"]["iceServers"])

            camera_entity_id = self.entity_id(entry, "camera")
            async with asyncio.timeout(3):
                await self.hass.config_entries.async_remove(entry.entry_id)
            await self.hass.async_block_till_done()
            self.assertIsNone(self.hass.states.get(camera_entity_id))

    async def test_provider_is_always_registered_and_native_two_way_audio_is_never_exposed(self):
        entry = await self.create_entry()
        entity = camera.get_camera_from_entity_id(self.hass, self.entity_id(entry, "camera"))
        (provider,) = self.providers()
        self.assertIsInstance(provider, NetvueWebRTCProvider)
        self.assertIs(entity.webrtc_provider, provider)
        self.assertFalse(entity.camera_entity_capabilities.two_way_audio)
        self.assertNotIn("netvue_push_to_talk", self.state(entry, "camera").attributes)
        # Push-to-talk offers (audio sendrecv) always reach the bridge.
        await entity.async_handle_async_webrtc_offer(MIC_OFFER, "mic", lambda _: None)
        await self._wait_for_request("PUT", "mic")
        entity.close_webrtc_session("mic")
        await self._wait_for_request("DELETE", "mic")

    async def _wait_for_request(self, method, session):
        request = (method, f"/api/v1/cameras/{HOST}/webrtc/{session}")
        async with asyncio.timeout(3):
            while request not in self.bridge.requests:
                await asyncio.sleep(0.01)

    async def test_four_viewers_share_the_camera_and_the_fifth_is_refused(self):
        entry = await self.create_entry()
        entity = camera.get_camera_from_entity_id(self.hass, self.entity_id(entry, "camera"))
        for index in range(4):
            await entity.async_handle_async_webrtc_offer(MIC_OFFER, f"v{index}", lambda _: None)
        for index in range(4):
            await self._wait_for_request("PUT", f"v{index}")
        with self.assertRaisesRegex(HomeAssistantError, "too many viewers"):
            await entity.async_handle_async_webrtc_offer(MIC_OFFER, "v4", lambda _: None)
        self.assertNotIn(("PUT", f"/api/v1/cameras/{HOST}/webrtc/v4"), self.bridge.requests)
        self.assertFalse([r for r in self.bridge.requests if r[0] == "DELETE" and "/webrtc/" in r[1]])
        entity.close_webrtc_session("v0")
        await self._wait_for_request("DELETE", "v0")
        await self.hass.async_block_till_done()
        await entity.async_handle_async_webrtc_offer(MIC_OFFER, "v5", lambda _: None)
        await self._wait_for_request("PUT", "v5")
        for index in (1, 2, 3, 5):
            entity.close_webrtc_session(f"v{index}")

    async def test_removing_entry_unregisters_provider(self):
        entry = await self.create_entry()
        self.assertEqual(len(self.providers()), 1)
        await self.hass.config_entries.async_remove(entry.entry_id)
        await self.hass.async_block_till_done()
        self.assertEqual(self.providers(), set())

    async def test_retired_player_options_are_dropped_silently(self):
        entry = await self.add_raw_entry(
            {"host": HOST, "stream_name": "netvue_192_168_10_2",
             "stream_url": "rtsp://127.0.0.1:8554/netvue_192_168_10_2"},
            options={"webrtc_enabled": False, "experimental_talkback": True,
                     "host": HOST, "stream_name": "netvue_192_168_10_2",
                     "stream_url": "rtsp://127.0.0.1:8554/netvue_192_168_10_2"},
        )
        self.assertEqual(entry.state, ConfigEntryState.LOADED)
        self.assertEqual(set(entry.options), {"host", "stream_name", "stream_url"})
        self.assertEqual(len(self.providers()), 1)


class FrontendTests(HassTestCase):
    async def test_card_is_served_once_for_all_entries(self):
        self.hass.config.components.add("frontend")
        with patch("homeassistant.components.frontend.add_extra_js_url") as add_js, patch(
            "homeassistant.components.http.HomeAssistantHTTP.async_register_static_paths",
            new_callable=AsyncMock,
        ) as register:
            await self.create_entry()
            await self.create_entry(host=OTHER_HOST, name="Other")
            await self.hass.async_block_till_done()
        register.assert_awaited_once()
        (config,) = register.await_args.args[0]
        self.assertRegex(config.url_path, r"^/netvue_local/frontend/\d+\.\d+\.\d+$")
        for module in (
            "netvue-camera-card.js",
            "netvue-camera-wall-card.js",
            "netvue-camera-status-card.js",
            "card-editors.js",
        ):
            self.assertTrue((Path(config.path) / module).is_file())
        add_js.assert_called_once()
        self.assertEqual(
            add_js.call_args.args[1],
            f"{config.url_path}/netvue-camera-card.js",
        )

    async def test_card_module_is_registered_when_frontend_loads_later(self):
        with patch("homeassistant.components.frontend.add_extra_js_url") as add_js, patch(
            "homeassistant.components.http.HomeAssistantHTTP.async_register_static_paths",
            new_callable=AsyncMock,
        ):
            await self.create_entry()
            add_js.assert_not_called()

            self.hass.config.components.add("frontend")
            self.hass.bus.async_fire(
                EVENT_COMPONENT_LOADED, {"component": "frontend"}
            )
            await self.hass.async_block_till_done()

        add_js.assert_called_once()
        self.assertRegex(add_js.call_args.args[1],
                         r"^/netvue_local/frontend/\d+\.\d+\.\d+/netvue-camera-card\.js$")

    async def test_headless_install_skips_the_extra_module(self):
        with patch("homeassistant.components.frontend.add_extra_js_url") as add_js:
            await self.create_entry()
        add_js.assert_not_called()
