"""Optional local metadata with real HA devices/entities and a fake bridge."""

import unittest
from unittest.mock import AsyncMock, patch

from homeassistant.components import camera
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.netvue_local.bridge_api import (
    SessionStatus, _camera_response, async_discover_cameras,
)
from custom_components.netvue_local.const import DOMAIN
from harness import HOST, STREAM_NAME, HassTestCase

OTHER_HOST = "192.168.10.3"
METADATA = {
    "serial_number": "test-serial",
    "firmware_version": "1.0",
    "capabilities": {"audio": True, "flip": False, "light": None},
    "wifi_ssid": "test-private-ssid",
    "wifi_signal_level": 37,
}


def camera_record(host=HOST, metadata=None):
    name = "netvue_" + host.replace(".", "_")
    record = {
        "host": host, "stream_name": name,
        "rtsp_url": f"rtsp://127.0.0.1:8554/{name}", "state": "supervising",
    }
    if metadata is not None:
        record.update(metadata=metadata, connection_port=4321)
    return record


class MetadataTests(HassTestCase):
    def device(self, entry):
        devices = dr.async_entries_for_config_entry(dr.async_get(self.hass), entry.entry_id)
        self.assertEqual(len(devices), 1)
        return devices[0]

    def entity_ids(self, entry):
        return {
            entity.unique_id: (entity.entity_id, entity.device_id)
            for entity in er.async_entries_for_config_entry(
                er.async_get(self.hass), entry.entry_id)
        }

    async def test_initial_metadata_populates_device_and_existing_diagnostic(self):
        self.bridge.cameras[HOST] = camera_record(metadata=METADATA)
        entry = await self.create_entry()
        device = self.device(entry)
        self.assertEqual(device.manufacturer, "Netvue")
        self.assertEqual(device.serial_number, METADATA["serial_number"])
        self.assertEqual(device.sw_version, "1.0")
        self.assertIsNone(device.model)
        self.assertEqual(device.identifiers, {(DOMAIN, entry.entry_id)})
        self.assertEqual(len(self.entity_ids(entry)), 12)
        attributes = self.state(entry, "sensor", "diagnostic").attributes
        self.assertEqual(attributes["wifi_ssid"], METADATA["wifi_ssid"])
        self.assertEqual(attributes["wifi_signal_level"], 37)
        self.assertEqual(attributes["capabilities"], {"audio": True, "flip": False})
        self.assertEqual(attributes["connection_port"], 4321)
        self.assertNotIn("unit_of_measurement", attributes)
        entity = camera.get_camera_from_entity_id(self.hass, self.entity_id(entry, "camera"))
        self.assertEqual(entity.device_info["sw_version"], "1.0")
        self.assertNotIn("model", entity.device_info)

    async def test_old_response_and_missing_or_null_metadata_remain_optional(self):
        entry = await self.create_entry()
        ids = self.entity_ids(entry)
        for metadata in (None, {}, {"capabilities": {
                "audio": None, "flip": None, "light": None, "sd_card": None}}):
            with self.subTest(metadata=metadata):
                self.bridge.cameras[HOST]["metadata"] = metadata
                await self.refresh(entry)
                device = self.device(entry)
                self.assertIsNone(device.serial_number)
                self.assertIsNone(device.sw_version)
                self.assertIsNone(device.model)
                attributes = self.state(entry, "sensor", "diagnostic").attributes
                for key in ("wifi_ssid", "wifi_signal_level", "capabilities",
                            "connection_port"):
                    self.assertNotIn(key, attributes)
                self.assertEqual(self.entity_ids(entry), ids)
                self.assertEqual(self.state(entry, "sensor", "diagnostic").state, "healthy")

    async def test_refresh_updates_firmware_and_recovers_metadata_without_stream_restart(self):
        # A legacy response first; metadata can appear after the entity is already live.
        entry = await self.create_entry()
        coordinator = entry.runtime_data
        entity = camera.get_camera_from_entity_id(self.hass, self.entity_id(entry, "camera"))
        stream = await entity.async_create_stream()  # No worker or network started.
        self.assertIsNotNone(stream)
        provider = entity._netvue_provider
        ids = self.entity_ids(entry)
        device_id = self.device(entry).id
        self.bridge.requests.clear()
        with (
            patch.object(entity, "async_will_remove_from_hass", new_callable=AsyncMock) as remove,
            patch.object(stream, "start") as start_stream,
            patch.object(stream, "stop") as stop_stream,
        ):
            for firmware in ("1.0", "2.0"):
                self.bridge.cameras[HOST].update(
                    metadata={**METADATA, "firmware_version": firmware})
                with self.assertNoLogs("custom_components.netvue_local"):
                    await self.refresh(entry)
                self.assertEqual(self.device(entry).sw_version, firmware)
                self.assertEqual(entity.device_info["sw_version"], firmware)
                self.assertIs(entity.stream, stream)
                self.assertIs(entity._netvue_provider, provider)
                self.assertIs(entry.runtime_data, coordinator)
                self.assertIs(camera.get_camera_from_entity_id(
                    self.hass, self.entity_id(entry, "camera")), entity)
                self.assertEqual(self.device(entry).id, device_id)
                self.assertEqual(self.entity_ids(entry), ids)
                self.assertNotEqual(self.state(entry, "camera").state, "unavailable")
            remove.assert_not_awaited()
            start_stream.assert_not_called()
            stop_stream.assert_not_called()
        self.assertEqual(self.bridge.requests, [("GET", f"/api/v1/cameras/{HOST}")] * 2)

    async def test_missing_metadata_keeps_last_known_registry_data_but_not_diagnostics(self):
        self.bridge.cameras[HOST] = camera_record(metadata=METADATA)
        entry = await self.create_entry()
        self.bridge.cameras[HOST].pop("metadata")
        await self.refresh(entry)
        self.assertEqual(self.device(entry).serial_number, METADATA["serial_number"])
        self.assertEqual(self.device(entry).sw_version, "1.0")
        attributes = self.state(entry, "sensor", "diagnostic").attributes
        self.assertNotIn("wifi_ssid", attributes)
        self.assertNotIn("wifi_signal_level", attributes)
        self.assertNotIn("capabilities", attributes)

    async def test_identical_serials_do_not_merge_entries_devices_or_entities(self):
        self.bridge.cameras[HOST] = camera_record(metadata=METADATA)
        self.bridge.cameras[OTHER_HOST] = camera_record(OTHER_HOST, METADATA)
        first = await self.create_entry()
        second = await self.create_entry(host=OTHER_HOST, name="Other")
        first_ids, second_ids = self.entity_ids(first), self.entity_ids(second)
        self.assertNotEqual(self.device(first).id, self.device(second).id)
        self.assertEqual(self.device(first).serial_number, self.device(second).serial_number)
        self.assertEqual(self.device(first).identifiers, {(DOMAIN, first.entry_id)})
        self.assertEqual(self.device(second).identifiers, {(DOMAIN, second.entry_id)})
        self.bridge.cameras[HOST]["metadata"] = {**METADATA, "firmware_version": "2.0"}
        await self.refresh(first)
        await self.refresh(second)
        self.assertEqual(self.device(first).sw_version, "2.0")
        self.assertEqual(self.device(second).sw_version, "1.0")
        self.assertEqual(self.entity_ids(first), first_ids)
        self.assertEqual(self.entity_ids(second), second_ids)
        self.assertTrue(set(first_ids.values()).isdisjoint(second_ids.values()))

    async def test_discovery_accepts_old_and_enriched_responses_without_starting_sessions(self):
        for record in ({"host": HOST}, {"host": HOST, "connection_port": 4321,
                                       "metadata": METADATA}):
            self.bridge.discovered = [record]
            with self.assertNoLogs("custom_components.netvue_local"):
                self.assertEqual(await async_discover_cameras(self.hass), [HOST])
        self.assertEqual(self.bridge.cameras, {})
        self.assertEqual(self.bridge.requests, [("POST", "/api/v1/discovery")] * 2)

    async def test_diagnostics_update_raw_zero_and_false_without_logging_values(self):
        self.bridge.cameras[HOST] = camera_record(metadata=METADATA)
        entry = await self.create_entry()
        self.bridge.cameras[HOST]["metadata"] = {
            "wifi_ssid": "changed-private-ssid", "wifi_signal_level": 0,
            "capabilities": {"audio": False, "sd_card": True},
        }
        with self.assertNoLogs("custom_components.netvue_local"):
            await self.refresh(entry)
        attributes = self.state(entry, "sensor", "diagnostic").attributes
        self.assertEqual(attributes["wifi_ssid"], "changed-private-ssid")
        self.assertEqual(attributes["wifi_signal_level"], 0)
        self.assertEqual(attributes["capabilities"], {"audio": False, "sd_card": True})


class MetadataParsingTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_response_preserves_session_status_defaults(self):
        response = AsyncMock()
        response.json.return_value = camera_record()
        self.assertEqual(await _camera_response(response, HOST, STREAM_NAME),
                         SessionStatus("supervising", None))

    async def test_invalid_optional_values_are_unknown_not_session_errors(self):
        response = AsyncMock()
        response.json.return_value = camera_record(metadata={
            "serial_number": 42, "firmware_version": [], "wifi_ssid": False,
            "wifi_signal_level": True,
            "capabilities": {"audio": "true", "flip": 1, "light": None, "future": True},
        })
        response.json.return_value["connection_port"] = True
        session = await _camera_response(response, HOST, STREAM_NAME)
        self.assertEqual(session.state, "supervising")
        self.assertIsNone(session.connection_port)
        self.assertIsNone(session.metadata.serial_number)
        self.assertIsNone(session.metadata.firmware_version)
        self.assertIsNone(session.metadata.wifi_ssid)
        self.assertIsNone(session.metadata.wifi_signal_level)
        self.assertEqual(session.metadata.capabilities, {})
