"""Real Home Assistant tests: flows, entities, lifecycle, image, WebRTC, diagnostics.

Run from the repository root; see tests/ha/harness.py for the isolation model.
"""

import asyncio
import json
from io import BytesIO
from pathlib import Path
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image
import probatio
from homeassistant.components import camera
from homeassistant.components.camera.webrtc import DATA_WEBRTC_PROVIDERS
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_COMPONENT_LOADED
from homeassistant.core import callback
from homeassistant.data_entry_flow import InvalidData
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers import condition, trigger
from homeassistant.setup import async_setup_component

import custom_components.netvue_local as netvue_local
from custom_components.netvue_local import camera as camera_platform
from custom_components.netvue_local.bridge_api import BridgeError, DetectionStatus
from custom_components.netvue_local.const import DOMAIN, SETTINGS_STORAGE_KEY
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
    async def test_add_camera_forms_serialize_for_frontend(self):
        self.bridge.discovered = [HOST]
        flow = self.hass.config_entries.flow
        for step in ("manual", "discovery"):
            with self.subTest(step=step):
                result = await flow.async_init(DOMAIN, context={"source": "user"})
                result = await flow.async_configure(
                    result["flow_id"], {"next_step_id": step}
                )
                fields = probatio.to_field_list(
                    result["data_schema"], custom_serializer=cv.custom_serializer
                )
                json.dumps(fields)
                zones = next(field for field in fields if field["name"] == "ai_zones")
                self.assertIn("object", zones["selector"])
                flow.async_abort(result["flow_id"])

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
            "ai_enabled": True,
            "ai_confidence": 0.38,
            "ai_fps": 3.0,
            "ai_confirmation_frames": 3,
            "ai_zones": {},
        })
        await self.hass.async_block_till_done()
        entry = result["result"]
        self.assertEqual(entry.state, ConfigEntryState.LOADED)
        self.assertEqual(self.bridge.cameras[HOST]["stream_name"], STREAM_NAME)

    async def test_manual_flow_saves_ai_settings_at_creation(self):
        flow = self.hass.config_entries.flow
        result = await flow.async_init(DOMAIN, context={"source": "user"})
        result = await flow.async_configure(result["flow_id"], {"next_step_id": "manual"})
        result = await flow.async_configure(result["flow_id"], {
            "name": "Salon",
            "host": HOST,
            "ai_enabled": True,
            "ai_confidence": 0.65,
            "ai_fps": 5,
            "ai_confirmation_frames": 4,
            "ai_zones": {"garden": [0.1, 0.2, 0.8, 0.9]},
        })
        self.assertEqual(result["type"], "create_entry")
        entry = result["result"]
        self.assertEqual(entry.data["ai_enabled"], True)
        self.assertEqual(entry.data["ai_confidence"], 0.65)
        self.assertEqual(entry.data["ai_fps"], 5)
        self.assertEqual(entry.data["ai_confirmation_frames"], 4)
        self.assertEqual(entry.data["ai_zones"], {
            "garden": {"name": "garden", "points": [[0.1, 0.2], [0.8, 0.2], [0.8, 0.9], [0.1, 0.9]]},
        })

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

    async def test_lan_discovery_hides_already_configured_hosts(self):
        await self.create_entry()
        flow = self.hass.config_entries.flow
        self.bridge.discovered = [HOST, OTHER_HOST]
        result = await flow.async_init(DOMAIN, context={"source": "user"})
        result = await flow.async_configure(result["flow_id"], {"next_step_id": "discovery"})
        self.assertEqual(result["step_id"], "select")
        with self.assertRaises(InvalidData):
            await flow.async_configure(
                result["flow_id"], {"name": "Again", "host": HOST, "manual": False})
        flow.async_abort(result["flow_id"])

        self.bridge.discovered = [HOST]
        result = await flow.async_init(DOMAIN, context={"source": "user"})
        result = await flow.async_configure(result["flow_id"], {"next_step_id": "discovery"})
        self.assertEqual(result["step_id"], "discovery")
        self.assertEqual(result["errors"], {"base": "no_new_cameras"})

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
    async def test_overlay_defaults_are_persisted_per_camera(self):
        entry = await self.create_entry()
        other = await self.create_entry(host=OTHER_HOST)
        request = SimpleNamespace(app={"hass": self.hass})
        view = netvue_local.NetvueOverlayView()
        self.assertEqual(json.loads((await view.get(request, entry.entry_id)).body),
                         {"live": True, "replay": True, "export": False})
        result = await self._init(entry, "player", overlay_live=False,
                                  overlay_replay=False, overlay_export=True)
        self.assertEqual(result["type"], "create_entry")
        await self.hass.async_block_till_done()
        self.assertEqual(json.loads((await view.get(request, entry.entry_id)).body),
                         {"live": False, "replay": False, "export": True})
        self.assertEqual(json.loads((await view.get(request, other.entry_id)).body),
                         {"live": True, "replay": True, "export": False})
        self.assertEqual((await view.get(request, "missing")).status, 404)

    async def test_global_settings_are_shared_restored_and_keep_token_private(self):
        entry = await self.create_entry()
        other = await self.create_entry(host=OTHER_HOST)
        result = await self._init(
            entry, "global_settings", retention_days=14, storage_limit_gb=1.5,
            turn_enabled=True, turn_key_id="synthetic-key",
            turn_api_token="synthetic-secret",
        )
        self.assertEqual(result["type"], "create_entry")
        await self.hass.async_block_till_done()
        self.assertNotIn("synthetic-secret", json.dumps(dict(entry.options)))
        self.assertNotIn("retention_days", entry.options)
        self.assertEqual(self.bridge.settings, {
            "recording_retention": "336h", "recording_limit_bytes": 1610612736,
        })
        self.assertEqual(self.bridge.webrtc_config, {
            "enabled": True, "key_id": "synthetic-key", "api_token": "synthetic-secret",
            "token_configured": True,
        })
        manager = self.hass.config_entries.options
        result = await self._open(other, "global_settings")
        fields = probatio.to_field_list(
            result["data_schema"], custom_serializer=cv.custom_serializer
        )
        self.assertNotIn("synthetic-secret", json.dumps(fields))
        self.assertEqual(next(field for field in fields if field["name"] == "retention_days")
                         ["default"], 14)
        token = next(field for field in fields if field["name"] == "turn_api_token")
        self.assertEqual(token["selector"]["text"]["type"], "password")
        self.assertNotIn("default", token)
        manager.async_abort(result["flow_id"])
        self.hass.data.pop(SETTINGS_STORAGE_KEY)
        restored = netvue_local.async_get_settings(self.hass)
        public = await restored.async_public()
        self.assertEqual(public["retention_days"], 14)
        self.assertTrue(public["turn_token_configured"])
        self.assertNotIn("synthetic-secret", json.dumps(public))
        await restored.async_restore()
        self.assertEqual(self.bridge.webrtc_config["api_token"], "synthetic-secret")

    async def test_turn_token_preservation_clear_and_failed_update(self):
        entry = await self.create_entry()
        result = await self._init(
            entry, "global_settings", turn_enabled=True, turn_key_id="synthetic-key",
            turn_api_token="synthetic-secret",
        )
        self.assertEqual(result["type"], "create_entry")
        await self.hass.async_block_till_done()
        result = await self._init(entry, "global_settings", retention_days=9, turn_api_token="")
        self.assertEqual(result["type"], "create_entry")
        await self.hass.async_block_till_done()
        self.assertEqual(self.bridge.webrtc_config["api_token"], "synthetic-secret")
        result = await self._init(entry, "global_settings", turn_clear_token=True)
        self.assertEqual(result["errors"], {"base": "invalid_global_settings"})
        self.hass.config_entries.options.async_abort(result["flow_id"])
        self.bridge.status_override = 503
        result = await self._init(entry, "global_settings", retention_days=10)
        self.assertEqual(result["errors"], {"base": "bridge_unavailable"})
        self.assertEqual((await netvue_local.async_get_settings(self.hass).async_public())
                         ["retention_days"], 9)
        self.hass.config_entries.options.async_abort(result["flow_id"])
        self.bridge.status_override = None
        with patch.object(netvue_local, "async_set_webrtc_config",
                          side_effect=[BridgeError("synthetic failure"), None]):
            result = await self._init(
                entry, "global_settings", retention_days=10, turn_key_id="new-key",
            )
        self.assertEqual(result["errors"], {"base": "bridge_unavailable"})
        self.assertEqual(self.bridge.settings["recording_retention"], "216h")
        self.hass.config_entries.options.async_abort(result["flow_id"])
        result = await self._init(
            entry, "global_settings", turn_enabled=False, turn_clear_token=True,
        )
        self.assertEqual(result["type"], "create_entry")
        await self.hass.async_block_till_done()
        self.assertEqual(self.bridge.webrtc_config["api_token"], "")
        self.assertFalse((await netvue_local.async_get_settings(self.hass).async_public())
                         ["turn_token_configured"])

    async def test_retention_change_preserves_existing_bridge_turn_credentials(self):
        self.bridge.webrtc_config = {
            "enabled": True, "key_id": "environment-key",
            "api_token": "environment-secret", "token_configured": True,
        }
        entry = await self.create_entry()
        result = await self._init(entry, "global_settings", retention_days=14)
        self.assertEqual(result["type"], "create_entry")
        await self.hass.async_block_till_done()
        self.assertEqual(self.bridge.webrtc_config["api_token"], "environment-secret")
        self.assertTrue(self.bridge.webrtc_config["enabled"])
        self.assertNotIn(("PUT", "/api/v1/webrtc/config"), self.bridge.requests)
        self.hass.data.pop(SETTINGS_STORAGE_KEY)
        restored = netvue_local.async_get_settings(self.hass)
        await restored.async_restore()
        self.assertNotIn(("PUT", "/api/v1/webrtc/config"), self.bridge.requests)
        self.assertTrue((await restored.async_public())["turn_token_configured"])
        result = await self._init(entry, "global_settings", turn_key_id="updated-key")
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(self.bridge.webrtc_config["api_token"], "environment-secret")

    async def test_options_form_serializes_for_frontend(self):
        entry = await self.create_entry()
        manager = self.hass.config_entries.options
        result = await self._open(entry, "zones")
        self.assertEqual(result["description_placeholders"]["zones_url"],
                         f"/netvue-settings?config_entry={entry.entry_id}&page=zones")
        fields = probatio.to_field_list(
            result["data_schema"], custom_serializer=cv.custom_serializer
        )
        json.dumps(fields)
        zones = next(field for field in fields if field["name"] == "ai_zones")
        self.assertIn("object", zones["selector"])
        manager.async_abort(result["flow_id"])

    async def _open(self, entry, step):
        manager = self.hass.config_entries.options
        result = await manager.async_init(entry.entry_id)
        self.assertEqual(result["type"], "menu")
        self.assertIn(step, result["menu_options"])
        result = await manager.async_configure(result["flow_id"], {"next_step_id": step})
        self.assertEqual(result["step_id"], step)
        return result

    async def _init(self, entry, step, **values):
        result = await self._open(entry, step)
        return await self.hass.config_entries.options.async_configure(result["flow_id"], values)

    async def test_host_change_moves_session_and_reloads_entry(self):
        entry = await self.create_entry()
        camera_entity = self.entity_id(entry, "camera")
        result = await self._init(entry, "camera", host=OTHER_HOST)
        self.assertEqual(result["type"], "create_entry")
        await self.hass.async_block_till_done()
        self.assertEqual(entry.state, ConfigEntryState.LOADED)
        self.assertEqual(list(self.bridge.cameras), [OTHER_HOST])
        self.assertEqual(self.bridge.cameras[OTHER_HOST]["stream_name"], "netvue_192_168_10_3")
        self.assertEqual(self.entity_id(entry, "camera"), camera_entity)

    async def test_ai_options_are_saved_from_integration_settings(self):
        entry = await self.create_entry()
        zones = {"garden": [0.1, 0.2, 0.8, 0.9]}
        result = await self._init(
            entry,
            "detection",
            ai_enabled=True,
            ai_confidence=0.65,
            ai_fps=5,
            ai_confirmation_frames=4,
        )
        self.assertEqual(result["type"], "create_entry")
        await self.hass.async_block_till_done()
        result = await self._init(entry, "zones", ai_zones={"garden": [0, 0, 2, 1]})
        self.assertEqual(result["errors"], {"base": "invalid_ai_zones"})
        self.hass.config_entries.options.async_abort(result["flow_id"])
        result = await self._init(entry, "zones", ai_zones=zones)
        self.assertEqual(result["type"], "create_entry")
        await self.hass.async_block_till_done()
        self.assertTrue(entry.options["ai_enabled"])
        self.assertEqual(entry.options["ai_confidence"], 0.65)
        self.assertEqual(entry.options["ai_fps"], 5)
        self.assertEqual(entry.options["ai_confirmation_frames"], 4)
        self.assertEqual(entry.options["ai_zones"], {
            "garden": {"name": "garden", "points": [[0.1, 0.2], [0.8, 0.2], [0.8, 0.9], [0.1, 0.9]]},
        })

    async def test_host_change_preserves_ai_options_when_service_is_unavailable(self):
        entry = await self.create_entry()
        ai_options = {
            "ai_enabled": True,
            "ai_confidence": 0.6,
            "ai_fps": 3,
            "ai_confirmation_frames": 3,
            "ai_zones": {"garden": [0.1, 0.2, 0.8, 0.9]},
        }
        self.hass.config_entries.async_update_entry(entry, options=ai_options)
        result = await self._init(entry, "camera", host=OTHER_HOST)
        self.assertEqual(result["type"], "create_entry")
        await self.hass.async_block_till_done()
        self.assertEqual({key: entry.options[key] for key in ai_options}, ai_options)
        self.assertEqual(entry.state, ConfigEntryState.LOADED)
        self.assertEqual(list(self.bridge.cameras), [OTHER_HOST])

    async def test_host_change_retries_after_bridge_recovers(self):
        entry = await self.create_entry()
        stop_camera = netvue_local.async_stop_camera

        async def stop_bridge_after_unload(hass, host):
            await stop_camera(hass, host)
            await self.bridge.stop()

        with patch.object(netvue_local, "async_stop_camera",
                          side_effect=stop_bridge_after_unload):
            result = await self._init(entry, "camera", host=OTHER_HOST)
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
                result = await self._init(entry, "camera", **values)
                self.assertEqual(result["errors"], {"base": error})
                self.hass.config_entries.options.async_abort(result["flow_id"])
        self.assertEqual(set(self.bridge.cameras), {HOST, OTHER_HOST})

    async def test_host_change_is_refused_while_bridge_unavailable(self):
        entry = await self.create_entry()
        self.bridge.status_override = 503
        result = await self._init(entry, "camera", host=OTHER_HOST)
        self.assertEqual(result["errors"], {"base": "bridge_unavailable"})
        self.assertEqual(entry.options, {})
        self.assertIn(HOST, self.bridge.cameras)


class EntityTests(HassTestCase):
    async def test_ai_switch_persists_setting_across_refresh_and_reload(self):
        enabled = True

        async def set_enabled(_hass, _stream, value):
            nonlocal enabled
            enabled = value
            return DetectionStatus(enabled, "detecting", [], None, None)

        async def set_config(_hass, _stream, **config):
            nonlocal enabled
            enabled = config["enabled"]
            return DetectionStatus(enabled, "detecting", [], None, None)

        async def get_status(_hass, _stream):
            return DetectionStatus(enabled, "detecting", [], None, None)

        with (
            patch("custom_components.netvue_local.switch.async_set_detection_enabled",
                  side_effect=set_enabled),
            patch("custom_components.netvue_local.coordinator.async_set_detection_config",
                  side_effect=set_config),
            patch("custom_components.netvue_local.coordinator.async_get_detection_status",
                  side_effect=get_status),
        ):
            entry = await self.create_entry()
            self.hass.config_entries.async_update_entry(
                entry, options={"ai_confidence": 0.6}
            )
            entity_id = self.entity_id(entry, "switch", "ai_detection_enabled")
            for value in (False, True):
                await self.hass.services.async_call(
                    "switch", "turn_on" if value else "turn_off",
                    {"entity_id": entity_id}, blocking=True,
                )
                await self.refresh(entry)
                self.assertEqual(enabled, value)
                self.assertEqual(entry.options["ai_enabled"], value)
                self.assertEqual(entry.options["ai_confidence"], 0.6)
                self.assertEqual(self.hass.states.get(entity_id).state, "on" if value else "off")
                await self.hass.config_entries.async_reload(entry.entry_id)
                await self.hass.async_block_till_done()
                self.assertEqual(enabled, value)
                self.assertEqual(self.hass.states.get(entity_id).state, "on" if value else "off")

    async def test_ai_switch_failure_does_not_change_saved_setting(self):
        status = DetectionStatus(True, "detecting", [], None, None)
        with (
            patch("custom_components.netvue_local.coordinator.async_set_detection_config",
                  new_callable=AsyncMock),
            patch("custom_components.netvue_local.coordinator.async_get_detection_status",
                  new=AsyncMock(return_value=status)),
            patch("custom_components.netvue_local.switch.async_set_detection_enabled",
                  side_effect=BridgeError("AI unavailable")),
        ):
            entry = await self.create_entry()
            with self.assertRaises(HomeAssistantError):
                await self.hass.services.async_call(
                    "switch", "turn_off",
                    {"entity_id": self.entity_id(entry, "switch", "ai_detection_enabled")},
                    blocking=True,
                )
            self.assertTrue(entry.data["ai_enabled"])
            self.assertNotIn("ai_enabled", entry.options)

    async def test_zone_automation_triggers_for_car_only_when_person_is_present(self):
        entry = await self.create_entry(name="Garage")
        zones = {
            "allee": {"name": "Allée voiture", "points": [[0, 0], [1, 0], [1, 1]]},
            "portail": {"name": "Devant le portail", "points": [[0, 0], [1, 1], [0, 1]]},
        }

        async def save_zones(zones):
            options = self.hass.config_entries.options
            flow = await options.async_init(entry.entry_id)
            await options.async_configure(flow["flow_id"], {"next_step_id": "zones"})
            result = await options.async_configure(flow["flow_id"], {"ai_zones": zones})
            self.assertEqual(result["type"], "create_entry")
            await self.hass.async_block_till_done()

        await save_zones(zones)
        coordinator = entry.runtime_data
        alley = self.entity_id(entry, "binary_sensor", "zone_allee")
        gate = self.entity_id(entry, "binary_sensor", "zone_portail")
        captured = []
        self.hass.bus.async_listen("netvue_test_action", lambda event: captured.append(event.data))
        automation = {
            "alias": "Cars at the gate",
            "triggers": [{"trigger": "netvue_local.object_entered",
                          "options": {"entity_id": alley, "category": "car", "duration": "5"}}],
            "conditions": [{"condition": "netvue_local.object_present",
                            "options": {"entity_id": gate, "category": "person"}}],
            "actions": [{"event": "netvue_test_action"}],
        }
        multi_captured = []
        self.hass.bus.async_listen("netvue_test_multi", lambda event: multi_captured.append(event.data))
        multi_automations = [
            {
                "alias": alias,
                "triggers": [{"trigger": "netvue_local.object_entered",
                              "options": {"entity_id": alley, "category": enters}}],
                "conditions": [{"condition": "netvue_local.object_present",
                                "options": {"entity_id": gate, "category": present}}],
                "actions": [{"event": "netvue_test_multi",
                             "event_data": {"category": "{{ trigger.category }}"}}],
            }
            for alias, enters, present in (
                ("Selected vehicles", ["car", "truck"], ["all"]),
                ("Any object", ["all"], ["person", "dog"]),
            )
        ]
        self.assertTrue(await async_setup_component(
            self.hass, "automation", {"automation": [automation, *multi_automations]}
        ))
        await self.hass.async_block_till_done()
        descriptors = await trigger.async_get_all_descriptions(self.hass)
        fields = descriptors["netvue_local.object_entered"]["fields"]
        self.assertEqual(fields["duration"]["default"], 0)
        self.assertTrue(fields["category"]["selector"]["select"]["multiple"])
        descriptors = await condition.async_get_all_descriptions(self.hass)
        self.assertIn("netvue_local.object_present", descriptors)

        car = {"track_id": 1, "category": "car", "zone_ids": ["allee", "portail"]}
        person = {"track_id": 2, "category": "person", "zone_ids": ["portail"]}
        events = []
        timers = []

        def schedule(_hass, delay, check):
            self.assertGreater(delay, 0)
            timers.append(check)
            return lambda: None
        with (
            patch("custom_components.netvue_local.coordinator.async_get_detection_status",
                  new_callable=AsyncMock) as get_status,
            patch("custom_components.netvue_local.coordinator.async_get_detection_events",
                  new=AsyncMock(side_effect=lambda *_: list(events))),
            patch("custom_components.netvue_local.trigger.async_call_later", side_effect=schedule),
        ):
            async def update_status(detections, now, event_at):
                get_status.return_value = DetectionStatus(
                    True, "detecting", detections, now, event_at, zone_tracking=True,
                )
                await entry.runtime_data.live_detection.async_refresh()
                await self.hass.async_block_till_done()

            async def publish(detections, *, zone="allee", category="car", age=0):
                now = time.time() - age
                presence_id = str(len(events))
                events.append({
                    "id": presence_id, "timestamp": now, "event": "zone_entered",
                    "zone": zone, "category": category, "zone_ids": [zone],
                    "presence_id": presence_id,
                })
                detections = [
                    {**item, "zone_presence_ids": {zone: presence_id}}
                    if item["category"] == category else item
                    for item in detections
                ]
                await update_status(detections, now, now)

            async def run_timers():
                for timer in timers:
                    timer(None)
                timers.clear()
                await self.hass.async_block_till_done()

            await publish([car])
            await run_timers()
            self.assertEqual(captured, [])
            await publish([car, person], zone="portail", category="person")
            self.assertEqual(captured, [])
            await publish([car, person])
            self.assertEqual(captured, [])
            await publish([car, person])
            self.assertEqual(len(timers), 2)
            await run_timers()
            self.assertEqual(len(captured), 1)
            await publish([car, person], age=60)
            self.assertEqual(len(captured), 1)
            self.assertEqual(self.hass.states.get(gate).state, "unavailable")

            zones = {**zones, "portail": {**zones["portail"], "name": "Grand portail"}}
            await save_zones(zones)
            self.assertIs(entry.runtime_data, coordinator)
            self.assertEqual(self.entity_id(entry, "binary_sensor", "zone_portail"), gate)
            self.assertIn("Grand portail", self.hass.states.get(gate).name)
            await publish([car, person])
            await run_timers()
            self.assertEqual(len(captured), 2)
            dog = {"track_id": 3, "category": "dog", "zone_ids": ["portail"]}
            truck = {"track_id": 4, "category": "truck", "zone_ids": ["allee"]}
            bird = {"track_id": 5, "category": "bird", "zone_ids": ["allee"]}
            multi_captured.clear()
            await publish([truck], category="truck")
            self.assertEqual(multi_captured, [])
            await publish([truck, person], category="truck")
            await publish([bird, dog], category="bird")
            self.assertCountEqual(multi_captured, [
                {"category": "truck"}, {"category": "truck"}, {"category": "bird"},
            ])
            await save_zones({"allee": zones["allee"]})
            self.assertIsNone(self.hass.states.get(gate))
            await publish([car, person])
            self.assertEqual(len(captured), 2)

    async def test_detection_metrics_and_event_identity_are_exposed(self):
        metrics = {
            "inference_latency_ms": 32.5,
            "frames_skipped": 90,
            "detection_rate": 0.2,
        }
        status = DetectionStatus(True, "detecting", [], None, None, metrics)
        with (
            patch("custom_components.netvue_local.coordinator.async_set_detection_config",
                  new_callable=AsyncMock),
            patch("custom_components.netvue_local.coordinator.async_get_detection_status",
                  new=AsyncMock(return_value=status)),
        ):
            entry = await self.create_entry(name="Salon")

        sensor = self.state(entry, "binary_sensor", "ai_detection")
        self.assertEqual(sensor.attributes["metrics"], metrics)
        event = {
            "id": "event-1",
            "timestamp": 1_800_000_000.0,
            "event": "zone_entered",
            "category": "person",
            "confidence": 0.91,
            "zone": "entrance",
            "entry_id": "untrusted",
            "camera": "untrusted",
            "host": "untrusted",
        }
        fired = []
        unsubscribe = self.hass.bus.async_listen(
            f"{DOMAIN}_event", lambda received: fired.append(received.data)
        )
        event_status = DetectionStatus(True, "detecting", [], None, 1_800_000_000.0)
        try:
            with patch(
                "custom_components.netvue_local.coordinator.async_get_detection_events",
                new=AsyncMock(return_value=[event, event]),
            ):
                await entry.runtime_data._async_fire_new_detection_events(event_status)
                await entry.runtime_data._async_fire_new_detection_events(event_status)
            await self.hass.async_block_till_done()
        finally:
            unsubscribe()

        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0]["entry_id"], entry.entry_id)
        self.assertEqual(fired[0]["camera"], "Salon")
        self.assertEqual(fired[0]["host"], HOST)
        self.assertEqual(fired[0]["zone"], "entrance")
        self.assertEqual(fired[0]["confidence"], 0.91)

    async def test_detected_objects_are_named_and_distinguish_ai_status(self):
        self.hass.config.language = "fr"
        with (
            patch("custom_components.netvue_local.coordinator.async_set_detection_config",
                  new_callable=AsyncMock),
            patch("custom_components.netvue_local.coordinator.async_get_detection_status",
                  new_callable=AsyncMock) as status,
        ):
            status.return_value = DetectionStatus(True, "detecting", [], None, None)
            entry = await self.create_entry()
            self.assertEqual(self.state(entry, "sensor", "detected_objects").state,
                             "Aucun objet détecté")
            cases = [
                (True, "detecting", [
                    {"category": "person", "confidence": 0.9},
                    {"category": "dog", "confidence": 0.8},
                    {"category": "person", "confidence": 0.85},
                ], "Personne ×2, Chien"),
                (True, "detecting", [{"category": "car"}, {"category": "car"}], "Voiture ×2"),
                (False, "detecting", [{"category": "person"}], "IA désactivée"),
                (True, "reconnecting", [{"category": "person"}], "Reconnexion du flux"),
                (True, "inference_error", [], "Erreur de détection IA"),
                (True, "detecting", [{"category": "future"}], "Objet non identifié"),
            ]
            for enabled, state, objects, expected in cases:
                with self.subTest(expected=expected):
                    status.return_value = DetectionStatus(enabled, state, objects, None, None)
                    await entry.runtime_data.live_detection.async_refresh()
                    self.assertEqual(self.state(entry, "sensor", "detected_objects").state,
                                     expected)
            status.side_effect = BridgeError("AI unavailable")
            await entry.runtime_data.live_detection.async_refresh()
            self.assertEqual(self.state(entry, "sensor", "detected_objects").state,
                             "unavailable")

    async def test_live_poll_delivers_vehicle_box_then_clears_without_camera_refresh(self):
        empty = DetectionStatus(True, "detecting", [], 100.0, None)
        car = {
            "category": "car", "confidence": 0.61, "track_id": 42,
            "frame_width": 1920, "frame_height": 1080,
            "box": {"x1": 300, "y1": 350, "x2": 760, "y2": 710},
        }
        with patch(
            "custom_components.netvue_local.coordinator.async_get_detection_status",
            new=AsyncMock(return_value=empty),
        ) as status:
            entry = await self.create_entry()
            ai_entity = self.entity_id(entry, "binary_sensor", "ai_detection")
            changes = asyncio.Queue()

            @callback
            def collect(event):
                changes.put_nowait(event.data["new_state"])

            unsubscribe = async_track_state_change_event(self.hass, [ai_entity], collect)
            try:
                with patch.object(entry.runtime_data.camera, "async_reconcile",
                                  wraps=entry.runtime_data.camera.async_reconcile) as reconcile:
                    status.return_value = DetectionStatus(True, "detecting", [car], 102.0, None)
                    present = await asyncio.wait_for(changes.get(), timeout=6)
                    self.assertEqual(present.state, "on")
                    self.assertEqual(present.attributes["detections"], [car])
                    status.return_value = empty._replace(last_frame_at=104.0)
                    cleared = await asyncio.wait_for(changes.get(), timeout=6)
                    self.assertEqual(cleared.state, "off")
                    self.assertEqual(cleared.attributes["detections"], [])
                    reconcile.assert_not_awaited()
            finally:
                unsubscribe()

    async def test_entities_device_and_initial_states(self):
        entry = await self.create_entry(name="Salon")
        registry = er.async_get(self.hass)
        entities = er.async_entries_for_config_entry(registry, entry.entry_id)
        self.assertEqual(
            sorted((e.domain, e.unique_id.removeprefix(entry.entry_id)) for e in entities),
            [("binary_sensor", "_ai_detection"), ("binary_sensor", "_rtsp"),
             ("binary_sensor", "_unavailable"),
             ("button", "_restart"), ("button", "_start_recording"),
             ("button", "_stop_recording"), ("camera", ""),
             ("sensor", "_detected_objects"), ("sensor", "_diagnostic"), ("sensor", "_recording"),
             ("sensor", "_session"), ("switch", "_ai_detection_enabled"),
             ("switch", "_continuous_recording")],
        )
        device = dr.async_entries_for_config_entry(dr.async_get(self.hass), entry.entry_id)
        self.assertEqual([(d.name, d.manufacturer) for d in device], [("Salon", "Netvue")])
        self.assertEqual(self.state(entry, "binary_sensor", "rtsp").state, "on")
        self.assertEqual(self.state(entry, "binary_sensor", "unavailable").state, "off")
        self.assertEqual(self.state(entry, "sensor", "session").state, "supervising")
        self.assertEqual(self.state(entry, "sensor", "diagnostic").state, "ai_unavailable")
        # Restart is only offered for a stopped or failed session.
        self.assertEqual(self.state(entry, "button", "restart").state, "unavailable")
        self.assertEqual(self.state(entry, "sensor", "recording").state, "inactive")
        self.assertEqual(self.state(entry, "switch", "continuous_recording").state, "off")
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
        self.assertEqual(self.state(entry, "sensor", "diagnostic").state, "ai_unavailable")

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
        self.assertEqual(self.state(entry, "sensor", "diagnostic").state, "ai_unavailable")

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
            er.async_get(self.hass), entry.entry_id)), 13)

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
        for domain in ("camera", "binary_sensor", "sensor", "button", "switch"):
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
        await entity.async_handle_async_webrtc_offer(MIC_OFFER, "v5", lambda _: None)
        await self._wait_for_request("DELETE", "v0")
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
        ) as register, patch(
            "homeassistant.components.panel_custom.async_register_panel",
            new_callable=AsyncMock,
        ) as register_panel:
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
            "netvue-protected-card.js",
            "netvue-settings-panel.js",
            "detection-zone-editor.js",
        ):
            self.assertTrue((Path(config.path) / module).is_file())
        register_panel.assert_awaited_once()
        panel = register_panel.await_args.kwargs
        self.assertEqual(panel["frontend_url_path"], "netvue-settings")
        self.assertEqual(panel["webcomponent_name"], "netvue-settings-panel")
        self.assertEqual(panel["module_url"], f"{config.url_path}/netvue-settings-panel.js")
        self.assertTrue(panel["require_admin"])
        self.assertEqual(panel["config_panel_domain"], DOMAIN)
        self.assertNotIn("sidebar_title", panel)
        self.assertEqual(add_js.call_count, 2)
        self.assertEqual(
            [call.args[1] for call in add_js.call_args_list],
            [
                f"{config.url_path}/netvue-camera-card.js",
                f"{config.url_path}/netvue-protected-card.js",
            ],
        )

    async def test_card_module_is_registered_when_frontend_loads_later(self):
        with patch("homeassistant.components.frontend.add_extra_js_url") as add_js, patch(
            "homeassistant.components.http.HomeAssistantHTTP.async_register_static_paths",
            new_callable=AsyncMock,
        ), patch(
            "homeassistant.components.panel_custom.async_register_panel",
            new_callable=AsyncMock,
        ) as register_panel:
            await self.create_entry()
            add_js.assert_not_called()
            register_panel.assert_not_called()

            self.hass.config.components.add("frontend")
            self.hass.bus.async_fire(
                EVENT_COMPONENT_LOADED, {"component": "frontend"}
            )
            await self.hass.async_block_till_done()

        self.assertEqual(add_js.call_count, 2)
        register_panel.assert_awaited_once()
        self.assertRegex(add_js.call_args_list[0].args[1],
                         r"^/netvue_local/frontend/\d+\.\d+\.\d+/netvue-camera-card\.js$")
        self.assertRegex(add_js.call_args_list[1].args[1],
                         r"^/netvue_local/frontend/\d+\.\d+\.\d+/netvue-protected-card\.js$")

    async def test_headless_install_skips_the_extra_module(self):
        with patch("homeassistant.components.frontend.add_extra_js_url") as add_js:
            await self.create_entry()
        add_js.assert_not_called()
