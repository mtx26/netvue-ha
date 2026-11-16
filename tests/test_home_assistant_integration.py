"""Offline tests; run in an isolated Home Assistant container."""

import asyncio
import ipaddress
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from custom_components.netvue_local import async_setup_entry, async_unload_entry
from custom_components.netvue_local.binary_sensor import NetvueStreamSensor
from custom_components.netvue_local.camera import NetvueCamera
from custom_components.netvue_local.config_flow import NetvueConfigFlow, NetvueOptionsFlow, _duplicate
from custom_components.netvue_local.discovery import (
    Candidate, _DiscoveryProtocol, async_discover, local_network, parse_response,
)
from custom_components.netvue_local.rtsp import (
    async_probe_stream, stream_name_from_url, stream_url_from_name,
    validate_host, validate_stream_url,
)
from netvue_bridge.protocol import command_packet


def packet(host="192.168.10.2", **unit):
    return command_packet([{"A": 8, "V": 2, "C": 0,
                            "P": ["fake", "fake", host, 5555, "test-key"], **unit}])


class DiscoveryTests(unittest.IsolatedAsyncioTestCase):
    def test_validate_replies_and_discard_key(self):
        network = local_network("192.168.10.1", 24)
        candidate = parse_response(packet(), "192.168.10.2", network)
        self.assertEqual(candidate.host, "192.168.10.2")
        self.assertNotIn("test-key", repr(candidate))
        for data, sender in [
            (packet(host="192.168.10.3"), "192.168.10.2"),
            (packet(C=1), "192.168.10.2"),
            (packet(P=["fake"]), "192.168.10.2"),
            (command_packet([{"A": 8, "V": 2, "P": ["fake", "fake", "192.168.10.2", 5555, "test-key"]}, 1]), "192.168.10.2"),
            (b"bad", "192.168.10.2"),
            (packet(), "192.168.11.2"),
        ]:
            self.assertIsNone(parse_response(data, sender, network))

    def test_duplicates_and_bounds(self):
        protocol = _DiscoveryProtocol("192.168.10.1", local_network("192.168.10.1", 24))
        protocol.datagram_received(packet(), ("192.168.10.2", 36868))
        protocol.datagram_received(packet(), ("192.168.10.2", 36868))
        protocol.datagram_received(b"x", ("192.168.10.2", 36868))
        self.assertEqual(len(protocol.candidates), 1)
        self.assertEqual(protocol.invalid, 1)
        for value, prefix in [("8.8.8.8", 24), ("192.168.10.1", 16),
                              ("127.0.0.1", 24), ("192.168.10.0", 24)]:
            with self.assertRaises(ValueError):
                local_network(value, prefix)

    async def test_one_broadcast_and_deadline_cleanup(self):
        transport = unittest.mock.Mock()
        loop = asyncio.get_running_loop()
        with patch.object(loop, "create_datagram_endpoint", return_value=(transport, None)), \
                patch("custom_components.netvue_local.discovery.asyncio.sleep",
                      new=unittest.mock.AsyncMock()) as sleep:
            self.assertEqual(await async_discover("192.168.10.1", 24), [])
        transport.sendto.assert_called_once()
        self.assertEqual(transport.sendto.call_args.args[1], ("192.168.10.255", 36868))
        sleep.assert_awaited_once_with(2.0)
        transport.close.assert_called_once()


class ConfigurationTests(unittest.TestCase):
    def test_urls_and_addresses(self):
        self.assertEqual(validate_host("192.168.10.2"), "192.168.10.2")
        self.assertEqual(validate_stream_url("rtsp://127.0.0.1:8554/netvue2"),
                         "rtsp://127.0.0.1:8554/netvue2")
        for url in ["rtsp://192.168.10.2:8554/x", "rtsp://user:pw@127.0.0.1:8554/x",
                    "rtsp://127.0.0.1:8554/x?secret=x", "http://127.0.0.1:8554/x",
                    "rtsp://127.0.0.1/x", "rtsp://127.0.0.1:8554/x%0d"]:
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

    def test_stream_names_and_legacy_urls(self):
        self.assertEqual(stream_url_from_name("netvue2"), "rtsp://127.0.0.1:8554/netvue2")
        self.assertEqual(stream_url_from_name("camera_2-test"), "rtsp://127.0.0.1:8554/camera_2-test")
        for name in ("", "a/b", "../a", "foo?x", "foo#bar", "foo bar", "foo%20", "é", "x\n"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                stream_url_from_name(name)
        self.assertEqual(stream_name_from_url("rtsp://127.0.0.1:8554/netvue2"), "netvue2")
        self.assertIsNone(stream_name_from_url("rtsp://127.0.0.1:8555/netvue2"))
        self.assertIsNone(stream_name_from_url("rtsp://127.0.0.1:8554/a/b"))

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
        requests = []

        async def handler(reader, writer):
            requests.append(await reader.readuntil(b"\r\n\r\n"))
            body = b"v=0\r\nm=video 0 RTP/AVP 96\r\na=rtpmap:96 H264/90000\r\n"
            writer.write(b"RTSP/1.0 200 OK\r\nContent-Type: application/sdp\r\nContent-Length: "
                         + str(len(body)).encode() + b"\r\n\r\n" + body)
            await writer.drain()
            writer.close()
            await writer.wait_closed()
        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        async with server:
            self.assertTrue(await async_probe_stream(f"rtsp://127.0.0.1:{port}/fake"))
        self.assertFalse(await async_probe_stream(f"rtsp://127.0.0.1:{port}/fake"))
        self.assertNotIn(b"Authorization:", requests[0])


class HomeAssistantApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_user_discovery_and_selection(self):
        hass = SimpleNamespace(config_entries=SimpleNamespace(async_entries=lambda _: []))
        flow = NetvueConfigFlow()
        flow.hass = hass
        adapters = [{"name": "fake0", "enabled": True,
                     "ipv4": [{"address": "192.168.10.1", "network_prefix": 24}]}]
        with patch("custom_components.netvue_local.config_flow.async_get_adapters",
                   new=unittest.mock.AsyncMock(return_value=adapters)), \
                patch("custom_components.netvue_local.config_flow.async_discover",
                      new=unittest.mock.AsyncMock(return_value=[Candidate("192.168.10.2")])) as discover:
            await flow.async_step_discover()
            discover.assert_not_awaited()
            result = await flow.async_step_discover({"source": "fake0: 192.168.10.1 (192.168.10.0/24)"})
            self.assertEqual(result["step_id"], "select")
            result = await flow.async_step_select({"host": "192.168.10.2"})
            self.assertEqual(result["step_id"], "manual")
            self.assertEqual(flow._host, "192.168.10.2")
            discover.assert_awaited_once_with("192.168.10.1", 24)

    async def test_manual_flow_confirmation_and_duplicate(self):
        hass = SimpleNamespace(config_entries=SimpleNamespace(async_entries=lambda _: []))
        flow = NetvueConfigFlow()
        flow.hass = hass
        data = {"name": "Test", "host": "192.168.10.2",
                "stream_name": "test"}
        with patch("custom_components.netvue_local.config_flow.async_probe_stream",
                   new=unittest.mock.AsyncMock(return_value=True)) as probe:
            form = await flow.async_step_manual()
            self.assertEqual(form["data_schema"]({"name": "Test", "host": "192.168.10.2"})
                             ["stream_name"], "netvue2")
            invalid = await flow.async_step_manual({**data, "stream_name": "../test"})
            self.assertEqual(invalid["errors"]["base"], "invalid_input")
            result = await flow.async_step_manual(data)
            self.assertEqual(result["step_id"], "confirm")
            self.assertEqual(result["description_placeholders"]["url"],
                             "rtsp://127.0.0.1:8554/test")
            probe.assert_not_awaited()
            result = await flow.async_step_confirm({"confirm": False})
            self.assertEqual(result["errors"]["base"], "confirmation_required")
            result = await flow.async_step_confirm({"confirm": True})
            self.assertEqual(result["step_id"], "status")
            probe.assert_awaited_once_with("rtsp://127.0.0.1:8554/test")
            result = await flow.async_step_status({})
            self.assertEqual(result["type"], "create_entry")
            self.assertEqual(result["data"], {"name": "Test", "host": "192.168.10.2",
                                              "stream_url": "rtsp://127.0.0.1:8554/test"})

    async def test_options_change_ip_and_duplicates(self):
        entry = SimpleNamespace(entry_id="1", data={"host": "192.168.10.2",
                                "stream_url": "rtsp://127.0.0.1:8554/a"}, options={})
        other = SimpleNamespace(entry_id="2", data={"host": "192.168.10.4",
                                "stream_url": "rtsp://127.0.0.1:8554/b"}, options={})
        hass = SimpleNamespace(config_entries=SimpleNamespace(
            async_entries=lambda _: [entry, other], async_get_known_entry=lambda _: entry))
        flow = NetvueOptionsFlow()
        flow.hass = hass
        flow.handler = "1"
        result = await flow.async_step_init({"host": "192.168.10.4",
                        "stream_name": "a"})
        self.assertEqual(result["errors"]["base"], "already_configured")
        result = await flow.async_step_init({"host": "192.168.10.3",
                        "stream_name": "a"})
        self.assertEqual(result["description_placeholders"]["url"], "rtsp://127.0.0.1:8554/a")
        with patch("custom_components.netvue_local.config_flow.async_probe_stream",
                   new=unittest.mock.AsyncMock(return_value=False)) as probe:
            result = await flow.async_step_confirm({"confirm": False})
            self.assertEqual(result["errors"]["base"], "confirmation_required")
            probe.assert_not_awaited()
            result = await flow.async_step_confirm({"confirm": True})
            self.assertEqual(result["step_id"], "status")
            self.assertEqual(result["description_placeholders"]["status"], "indisponible / unavailable")
            probe.assert_awaited_once_with("rtsp://127.0.0.1:8554/a")
            result = await flow.async_step_status({})
            self.assertEqual(result["data"], {"host": "192.168.10.3",
                                               "stream_url": "rtsp://127.0.0.1:8554/a"})

    async def test_legacy_options_preserve_or_explicitly_replace_url(self):
        for old_url in ("rtsp://127.0.0.1:8555/legacy", "rtsp://127.0.0.1:8554/a/b"):
            entry = SimpleNamespace(entry_id="1", data={"host": "192.168.10.2",
                                    "stream_url": old_url}, options={})
            hass = SimpleNamespace(config_entries=SimpleNamespace(
                async_entries=lambda _: [entry], async_get_known_entry=lambda _: entry))
            flow = NetvueOptionsFlow()
            flow.hass = hass
            flow.handler = "1"
            form = await flow.async_step_init()
            self.assertEqual(form["description_placeholders"]["url"], old_url)
            self.assertEqual(form["data_schema"]({"host": "192.168.10.3"}),
                             {"host": "192.168.10.3"})
            result = await flow.async_step_init({"host": "192.168.10.3"})
            self.assertEqual(result["description_placeholders"]["url"], old_url)
            with patch("custom_components.netvue_local.config_flow.async_probe_stream",
                       new=unittest.mock.AsyncMock(return_value=True)):
                await flow.async_step_confirm({"confirm": True})
                result = await flow.async_step_status({})
            self.assertEqual(result["data"]["stream_url"], old_url)
            result = await flow.async_step_init({"host": "192.168.10.3", "stream_name": "new"})
            self.assertEqual(result["description_placeholders"]["url"],
                             "rtsp://127.0.0.1:8554/new")

    async def test_setup_unload_and_camera(self):
        entry = SimpleNamespace(entry_id="1", title="Fake",
                                data={"stream_url": "rtsp://127.0.0.1:8554/a"}, options={})
        entries = SimpleNamespace(async_forward_entry_setups=unittest.mock.AsyncMock(),
                                  async_unload_platforms=unittest.mock.AsyncMock(return_value=True))
        hass = SimpleNamespace(config_entries=entries)
        coordinator = SimpleNamespace(data=True, last_update_success=True,
                                      async_refresh=unittest.mock.AsyncMock())
        with patch("custom_components.netvue_local.NetvueCoordinator", return_value=coordinator):
            self.assertTrue(await async_setup_entry(hass, entry))
        camera = NetvueCamera(entry, coordinator)
        self.assertEqual(await camera.stream_source(), entry.data["stream_url"])
        self.assertTrue(camera.available)
        self.assertEqual(camera.stream_options["rtsp_transport"], "tcp")
        sensor = NetvueStreamSensor(entry, coordinator)
        self.assertTrue(sensor.is_on)
        coordinator.data = False
        self.assertFalse(camera.available)
        self.assertFalse(sensor.is_on)
        self.assertTrue(await async_unload_entry(hass, entry))
        entries.async_unload_platforms.assert_awaited_once()

    async def test_existing_entries_with_removed_fields_keep_plain_stream_url(self):
        url = "rtsp://127.0.0.1:8554/netvue2"
        coordinator = SimpleNamespace(data=True, last_update_success=True)
        for location in ("data", "options"):
            entry = SimpleNamespace(
                entry_id=location,
                title="Existing",
                data={"stream_url": url},
                options={},
            )
            getattr(entry, location).update({
                "rtsp_username": "legacy-user",
                "rtsp_password": "legacy-value",
            })
            camera = NetvueCamera(entry, coordinator)
            self.assertEqual(await camera.stream_source(), url)


if __name__ == "__main__":
    unittest.main()
