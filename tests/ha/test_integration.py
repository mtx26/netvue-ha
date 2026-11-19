"""Real Home Assistant tests: flows, entities, lifecycle, image, WebRTC, diagnostics.

Run from the repository root; see tests/ha/harness.py for the isolation model.
"""

import asyncio
from io import BytesIO
from unittest.mock import AsyncMock, patch

from PIL import Image
from homeassistant.components import camera
from homeassistant.components.camera.webrtc import DATA_WEBRTC_PROVIDERS
from homeassistant.config_entries import ConfigEntryState
from homeassistant.data_entry_flow import InvalidData
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.netvue_local.const import DOMAIN
from custom_components.netvue_local.webrtc import NetvueWebRTCProvider
from harness import HOST, STREAM_NAME, HassTestCase

OTHER_HOST = "192.168.10.3"
MIC_OFFER = "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=sendonly\r\n"


def _jpeg(size=(160, 90)) -> bytes:
    out = BytesIO()
    Image.new("RGB", size, "white").save(out, format="JPEG")
    return out.getvalue()


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
        result = await self._init(entry, host=OTHER_HOST, webrtc_enabled=False,
                                  experimental_talkback=False)
        self.assertEqual(result["type"], "create_entry")
        await self.hass.async_block_till_done()
        self.assertEqual(entry.state, ConfigEntryState.LOADED)
        self.assertEqual(list(self.bridge.cameras), [OTHER_HOST])
        self.assertEqual(self.bridge.cameras[OTHER_HOST]["stream_name"], "netvue_192_168_10_3")
        self.assertEqual(self.entity_id(entry, "camera"), camera_entity)

    async def test_option_validation(self):
        entry = await self.create_entry()
        other = await self.create_entry(host=OTHER_HOST, name="Other")
        self.assertEqual(other.state, ConfigEntryState.LOADED)
        cases = {
            "invalid_input": {"host": "8.8.8.8"},
            "already_configured": {"host": OTHER_HOST},
            "talkback_requires_webrtc": {"host": HOST, "experimental_talkback": True},
        }
        for error, values in cases.items():
            with self.subTest(error):
                values = {"webrtc_enabled": False, "experimental_talkback": False, **values}
                result = await self._init(entry, **values)
                self.assertEqual(result["errors"], {"base": error})
        self.assertEqual(set(self.bridge.cameras), {HOST, OTHER_HOST})

    async def test_host_change_is_refused_while_bridge_unavailable(self):
        entry = await self.create_entry()
        self.bridge.status_override = 503
        result = await self._init(entry, host=OTHER_HOST, webrtc_enabled=False,
                                  experimental_talkback=False)
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

    async def test_webrtc_provider_is_opt_in(self):
        entry = await self.create_entry()
        self.assertEqual(self.providers(), set())
        entity = camera.get_camera_from_entity_id(self.hass, self.entity_id(entry, "camera"))
        self.assertIsNone(entity.webrtc_provider)
        self.assertFalse(entity.camera_entity_capabilities.two_way_audio)
        with self.assertRaisesRegex(HomeAssistantError, "does not support WebRTC"):
            await entity.async_handle_async_webrtc_offer("offer", "s", lambda _: None)

    async def test_webrtc_registers_provider_without_two_way_audio(self):
        entry = await self.create_entry(webrtc_enabled=True, experimental_talkback=False)
        entity = camera.get_camera_from_entity_id(self.hass, self.entity_id(entry, "camera"))
        (provider,) = self.providers()
        self.assertIsInstance(provider, NetvueWebRTCProvider)
        self.assertIs(entity.webrtc_provider, provider)
        self.assertFalse(entity.camera_entity_capabilities.two_way_audio)
        self.assertFalse(provider._allow_microphone)
        # A microphone offer is refused before reaching the bridge.
        with self.assertRaisesRegex(HomeAssistantError, "microphone control is disabled"):
            await entity.async_handle_async_webrtc_offer(MIC_OFFER, "mic", lambda _: None)
        self.assertFalse([r for r in self.bridge.requests if "/webrtc/" in r[1]])

    async def test_two_way_audio_requires_the_experimental_option(self):
        entry = await self.create_entry(webrtc_enabled=True, experimental_talkback=True)
        entity = camera.get_camera_from_entity_id(self.hass, self.entity_id(entry, "camera"))
        (provider,) = self.providers()
        self.assertTrue(entity.camera_entity_capabilities.two_way_audio)
        self.assertTrue(provider._allow_microphone)
        messages = []
        await entity.async_handle_async_webrtc_offer(MIC_OFFER, "mic", messages.append)
        async with asyncio.timeout(3):
            while ("PUT", f"/api/v1/cameras/{HOST}/webrtc/mic") not in self.bridge.requests:
                await asyncio.sleep(0.01)
        entity.close_webrtc_session("mic")
        async with asyncio.timeout(3):
            while ("DELETE", f"/api/v1/cameras/{HOST}/webrtc/mic") not in self.bridge.requests:
                await asyncio.sleep(0.01)

    async def test_disabling_webrtc_or_removing_entry_unregisters_provider(self):
        entry = await self.create_entry(webrtc_enabled=True)
        self.assertEqual(len(self.providers()), 1)
        manager = self.hass.config_entries.options
        result = await manager.async_init(entry.entry_id)
        await manager.async_configure(
            result["flow_id"],
            {"host": HOST, "webrtc_enabled": False, "experimental_talkback": False})
        await self.hass.async_block_till_done()
        self.assertEqual(self.providers(), set())
        result = await manager.async_init(entry.entry_id)
        await manager.async_configure(
            result["flow_id"],
            {"host": HOST, "webrtc_enabled": True, "experimental_talkback": False})
        await self.hass.async_block_till_done()
        self.assertEqual(len(self.providers()), 1)
        await self.hass.config_entries.async_remove(entry.entry_id)
        await self.hass.async_block_till_done()
        self.assertEqual(self.providers(), set())
