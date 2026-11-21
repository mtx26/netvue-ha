"""Offline tests for the pinned Home Assistant integration environment."""

import asyncio
import gc
import json
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from aiohttp import ClientSession, web
from PIL import Image, ImageDraw

from custom_components.netvue_local import async_setup_entry, async_unload_entry
from custom_components.netvue_local.bridge_api import (
    BridgeError, ManagedCamera, RecordingStatus, SessionStatus,
    async_discover_cameras, async_stop_camera,
)
from custom_components.netvue_local.binary_sensor import NetvueCameraUnavailableSensor
from custom_components.netvue_local.button import NetvueRestartButton
from custom_components.netvue_local.camera import NetvueCamera
from custom_components.netvue_local.config_flow import _duplicate
from custom_components.netvue_local.coordinator import CameraData, NetvueCoordinator
from custom_components.netvue_local.rtsp import (
    async_probe_stream, stream_url_from_name, validate_host, validate_stream_url,
)
from custom_components.netvue_local.sensor import (
    NetvueDiagnosticSensor, NetvueSessionSensor, _DIAGNOSTIC_OPTIONS, _diagnostic_state,
)
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.const import Platform
from homeassistant.components.camera import (
    CameraEntityFeature, CameraImageView, StreamType, WebRTCAnswer, WebRTCCandidate, WebRTCError,
)
from homeassistant.components.camera.webrtc import DATA_WEBRTC_PROVIDERS
from webrtc_models import RTCIceCandidateInit


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
            translated = json.loads((base / "translations" / f"{lang}.json")
                                    .read_text(encoding="utf-8"))
            self.assertEqual(keys(translated), reference)
            states = translated["entity"]["sensor"]["diagnostic"]["state"]
            self.assertEqual(set(states), set(_DIAGNOSTIC_OPTIONS))


class RTSPTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def fake_connection(response):
        reader = asyncio.StreamReader()
        reader.feed_data(response)
        reader.feed_eof()
        writer = SimpleNamespace(write=unittest.mock.Mock(),
                                 drain=unittest.mock.AsyncMock(),
                                 close=unittest.mock.Mock(),
                                 wait_closed=unittest.mock.AsyncMock())
        return reader, writer

    async def test_h264_and_absent_stream(self):
        body = b"v=0\r\nm=video 0 RTP/AVP 96\r\na=rtpmap:96 H264/90000\r\n"

        available = (b"RTSP/1.0 200 OK\r\nContent-Type: application/sdp\r\nContent-Length: "
                     + str(len(body)).encode() + b"\r\n\r\n" + body)
        absent = b"RTSP/1.0 404 Not Found\r\nContent-Length: 0\r\n\r\n"
        successful_connection = self.fake_connection(available)
        missing_connection = self.fake_connection(absent)
        with patch("custom_components.netvue_local.rtsp.asyncio.open_connection",
                   new=unittest.mock.AsyncMock(side_effect=[
                       successful_connection, missing_connection,
                   ])) as connect:
            url = "rtsp://127.0.0.1:8554/netvue2"
            self.assertTrue(await async_probe_stream(url))
            self.assertFalse(await async_probe_stream(url))
        self.assertEqual(connect.await_args_list[0].args, ("127.0.0.1", 8554))
        self.assertNotIn(b"Authorization:", successful_connection[1].write.call_args.args[0])
        missing_connection[1].close.assert_called_once()
        missing_connection[1].wait_closed.assert_awaited_once()

    async def test_malformed_missing_or_oversized_headers_are_rejected(self):
        valid_body = b"v=0\r\na=rtpmap:96 H264/90000\r\n"
        responses = {
            "malformed content length": (
                b"RTSP/1.0 200 OK\r\nContent-Type: application/sdp\r\n"
                b"Content-Length: invalid\r\n\r\n"
            ),
            "missing content length": (
                b"RTSP/1.0 200 OK\r\nContent-Type: application/sdp\r\n\r\n"
                + valid_body
            ),
            "oversized content length": (
                b"RTSP/1.0 200 OK\r\nContent-Type: application/sdp\r\n"
                b"Content-Length: 16385\r\n\r\n"
            ),
            "malformed content type header": (
                b"RTSP/1.0 200 OK\r\nContent-Type application/sdp\r\n"
                + f"Content-Length: {len(valid_body)}\r\n\r\n".encode()
                + valid_body
            ),
            "oversized header block": (
                b"RTSP/1.0 200 OK\r\nX-Fill: " + b"x" * 16384
                + b"\r\n\r\n"
            ),
        }

        for case, response in responses.items():
            with self.subTest(case=case):
                connection = self.fake_connection(response)
                with patch("custom_components.netvue_local.rtsp.asyncio.open_connection",
                           new=unittest.mock.AsyncMock(return_value=connection)):
                    self.assertFalse(await async_probe_stream(
                        "rtsp://127.0.0.1:8554/netvue2"
                    ))

    async def test_truncated_sdp_is_rejected(self):
        response = (
            b"RTSP/1.0 200 OK\r\nContent-Type: application/sdp\r\n"
            b"Content-Length: 100\r\n\r\nv=0\r\na=rtpmap:96 H264/90000\r\n"
        )
        connection = self.fake_connection(response)
        with patch("custom_components.netvue_local.rtsp.asyncio.open_connection",
                   new=unittest.mock.AsyncMock(return_value=connection)):
            self.assertFalse(await async_probe_stream("rtsp://127.0.0.1:8554/netvue2"))

    async def test_valid_non_h264_sdp_is_rejected(self):
        body = b"v=0\r\nm=video 0 RTP/AVP 96\r\na=rtpmap:96 VP8/90000\r\n"
        response = (
            b"RTSP/1.0 200 OK\r\nContent-Type: application/sdp\r\n"
            + f"Content-Length: {len(body)}\r\n\r\n".encode()
            + body
        )
        connection = self.fake_connection(response)
        with patch("custom_components.netvue_local.rtsp.asyncio.open_connection",
                   new=unittest.mock.AsyncMock(return_value=connection)):
            self.assertFalse(await async_probe_stream("rtsp://127.0.0.1:8554/netvue2"))

    async def test_connection_timeout_returns_false(self):
        original_timeout = asyncio.timeout

        async def never_connect(*args, **kwargs):
            await asyncio.Event().wait()

        with (
            patch("custom_components.netvue_local.rtsp.asyncio.open_connection",
                  new=unittest.mock.AsyncMock(side_effect=never_connect)),
            patch("custom_components.netvue_local.rtsp.asyncio.timeout",
                  return_value=original_timeout(0)),
        ):
            self.assertFalse(await async_probe_stream("rtsp://127.0.0.1:8554/netvue2"))

    async def test_connection_is_closed_when_probe_returns_early(self):
        response = b"RTSP/1.0 404 Not Found\r\nContent-Length: 0\r\n\r\n"
        connection = self.fake_connection(response)
        with patch("custom_components.netvue_local.rtsp.asyncio.open_connection",
                   new=unittest.mock.AsyncMock(return_value=connection)):
            self.assertFalse(await async_probe_stream("rtsp://127.0.0.1:8554/netvue2"))
        connection[1].close.assert_called_once()
        connection[1].wait_closed.assert_awaited_once()


class HomeAssistantApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovery_api_contract_and_validation(self):
        hass = SimpleNamespace()
        response = unittest.mock.MagicMock()
        response.status = 200
        response.json = unittest.mock.AsyncMock(return_value={
            "cameras": [{"host": "192.168.10.2"}, {"host": "10.0.0.2"},
                        {"host": "192.168.10.2"}],
        })
        context = unittest.mock.MagicMock()
        context.__aenter__ = unittest.mock.AsyncMock(return_value=response)
        context.__aexit__ = unittest.mock.AsyncMock(return_value=False)
        session = SimpleNamespace(post=unittest.mock.Mock(return_value=context))
        with patch("custom_components.netvue_local.bridge_api.async_get_clientsession",
                   return_value=session):
            self.assertEqual(await async_discover_cameras(hass), ["192.168.10.2", "10.0.0.2"])
            session.post.assert_called_once_with("http://127.0.0.1:8765/api/v1/discovery")
            response.json.return_value = {"cameras": []}
            self.assertEqual(await async_discover_cameras(hass), [])
            for payload in ({}, {"cameras": None}, {"cameras": [{}]},
                            {"cameras": [{"host": "8.8.8.8"}]},
                            {"cameras": [{"host": 42}]}):
                response.json.return_value = payload
                with self.subTest(payload=payload), self.assertRaises(BridgeError):
                    await async_discover_cameras(hass)
            response.status = 503
            with self.assertRaisesRegex(BridgeError, "HTTP 503"):
                await async_discover_cameras(hass)
            response.status = 200
            response.json.side_effect = ValueError("invalid JSON")
            with self.assertRaises(BridgeError):
                await async_discover_cameras(hass)
            context.__aenter__.side_effect = TimeoutError()
            with self.assertRaises(BridgeError):
                await async_discover_cameras(hass)

    async def test_camera_badge_preserves_entire_image_and_other_snapshots(self):
        entry = SimpleNamespace(
            entry_id="1", title="Fake",
            data={"stream_url": "rtsp://127.0.0.1:8554/a"}, options={},
        )
        coordinator = SimpleNamespace(
            data=CameraData(SessionStatus("supervising", None), True),
            last_update_success=True,
        )
        camera = NetvueCamera(entry, coordinator)
        camera.hass = SimpleNamespace(async_add_executor_job=asyncio.to_thread)
        self.assertFalse(camera.use_stream_for_stills)

        for size in ((160, 90), (90, 160), (120, 120)):
            with self.subTest(size=size):
                source = Image.new("RGB", size, "white")
                draw = ImageDraw.Draw(source)
                for x, y, color in ((0, 0, "red"), (size[0] - 20, 0, "green"),
                                    (0, size[1] - 20, "blue"),
                                    (size[0] - 20, size[1] - 20, "yellow")):
                    draw.rectangle((x, y, x + 19, y + 19), fill=color)
                buf = BytesIO()
                source.save(buf, format="JPEG", quality=100)
                original = buf.getvalue()
                with patch("custom_components.netvue_local.camera.async_get_stream_image",
                           new=unittest.mock.AsyncMock(return_value=original)) as get_image:
                    badge = await camera.async_camera_image(width=80, height=80)
                    self.assertEqual(await camera.async_camera_image(width=640, height=360),
                                     original)
                    self.assertEqual(await camera.async_camera_image(), original)
                self.assertEqual(get_image.await_args_list, [
                    unittest.mock.call(camera, wait_for_next_keyframe=False),
                    unittest.mock.call(camera, wait_for_next_keyframe=False),
                    unittest.mock.call(camera, wait_for_next_keyframe=True),
                ])
                with Image.open(BytesIO(badge)) as image:
                    self.assertEqual(image.size, (80, 80))
                    pixels = image.convert("RGB")
                    box = pixels.point(lambda value: 255 if value > 45 else 0).getbbox()
                    self.assertIsNotNone(box)
                    x0, y0, x1, y1 = box
                    self.assertAlmostEqual((x1 - x0) / (y1 - y0), size[0] / size[1],
                                           delta=0.08)
                    for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
                        self.assertLessEqual((x - 40) ** 2 + (y - 40) ** 2, 40 ** 2)
                    for x, y, channel in ((x0 + 4, y0 + 4, 0), (x1 - 5, y0 + 4, 1),
                                          (x0 + 4, y1 - 5, 2)):
                        self.assertGreater(pixels.getpixel((x, y))[channel], 100)
                    yellow = pixels.getpixel((x1 - 5, y1 - 5))
                    self.assertGreater(min(yellow[:2]), 100)

    async def test_camera_proxy_badge_does_not_change_snapshot(self):
        entry = SimpleNamespace(entry_id="1", title="Fake",
                                data={"stream_url": "rtsp://127.0.0.1:8554/a"}, options={})
        camera = NetvueCamera(entry, SimpleNamespace(last_update_success=True))
        camera.hass = SimpleNamespace(async_add_executor_job=asyncio.to_thread)
        buf = BytesIO()
        Image.new("RGB", (320, 180), "white").save(buf, format="JPEG")
        original = buf.getvalue()
        stream = SimpleNamespace(async_get_image=unittest.mock.AsyncMock(return_value=original))
        camera.stream = stream
        view = CameraImageView(None)
        badge = await view.handle(SimpleNamespace(query={"width": "80", "height": "80"}),
                                  camera)
        scaled = await view.handle(SimpleNamespace(query={"width": "100", "height": "50"}),
                                   camera)
        snapshot = await view.handle(SimpleNamespace(query={}), camera)
        self.assertEqual(badge.content_type, "image/jpeg")
        with Image.open(BytesIO(badge.body)) as image:
            self.assertEqual(image.size, (80, 80))
        with Image.open(BytesIO(scaled.body)) as image:
            self.assertAlmostEqual(image.width / image.height, 320 / 180, delta=0.03)
        self.assertEqual(snapshot.body, original)
        with Image.open(BytesIO(snapshot.body)) as image:
            self.assertEqual(image.size, (320, 180))
        self.assertEqual(stream.async_get_image.await_args_list, [
            unittest.mock.call(width=None, height=None, wait_for_next_keyframe=False),
            unittest.mock.call(width=None, height=None, wait_for_next_keyframe=False),
            unittest.mock.call(width=None, height=None, wait_for_next_keyframe=True),
        ])

    async def test_camera_image_unavailable_is_not_replaced(self):
        entry = SimpleNamespace(entry_id="1", title="Fake",
                                data={"stream_url": "rtsp://127.0.0.1:8554/a"}, options={})
        camera = NetvueCamera(entry, SimpleNamespace(last_update_success=True))
        with patch("custom_components.netvue_local.camera.async_get_stream_image",
                   new=unittest.mock.AsyncMock(return_value=None)):
            self.assertIsNone(await camera.async_camera_image(width=80, height=80))
        camera.hass = SimpleNamespace(async_add_executor_job=asyncio.to_thread)
        with patch("custom_components.netvue_local.camera.async_get_stream_image",
                   new=unittest.mock.AsyncMock(return_value=b"not an image")):
            with self.assertRaises(OSError):
                await camera.async_camera_image(width=80, height=80)

    async def test_camera_never_exposes_native_two_way_audio(self):
        entry = SimpleNamespace(
            entry_id="1", title="Fake",
            data={"stream_url": "rtsp://127.0.0.1:8554/a"}, options={},
        )
        coordinator = SimpleNamespace(
            data=CameraData(SessionStatus("supervising", None), True),
            last_update_success=True,
        )
        camera = NetvueCamera(entry, coordinator)
        camera.hass = SimpleNamespace()
        provider = SimpleNamespace(
            async_register_camera=unittest.mock.AsyncMock(),
            async_handle_async_webrtc_offer=unittest.mock.AsyncMock(),
            async_on_webrtc_candidate=unittest.mock.AsyncMock(),
            async_close_session=unittest.mock.Mock(),
        )
        self.assertEqual(camera.supported_features, CameraEntityFeature.STREAM)
        self.assertFalse(camera.camera_entity_capabilities.two_way_audio)
        with patch("homeassistant.components.camera.entity.async_is_preview_feature_enabled",
                   return_value=True):
            capabilities = camera.camera_capabilities
            self.assertEqual(capabilities.frontend_stream_types, {StreamType.HLS})
            self.assertFalse(capabilities.two_way_audio)
            with self.assertRaisesRegex(HomeAssistantError, "does not support WebRTC"):
                await camera.async_handle_async_webrtc_offer("offer", "session", lambda _: None)
            with patch.object(camera, "_async_get_supported_webrtc_provider",
                              new=unittest.mock.AsyncMock(return_value=provider)):
                await camera.async_refresh_providers(write_state=False)
            provider.async_register_camera.assert_awaited_once_with(camera)
            capabilities = camera.camera_capabilities
            self.assertEqual(capabilities.frontend_stream_types,
                             {StreamType.HLS, StreamType.WEB_RTC})
            self.assertFalse(capabilities.two_way_audio)
            send_message = unittest.mock.Mock()
            await camera.async_handle_async_webrtc_offer("offer", "session", send_message)
            provider.async_handle_async_webrtc_offer.assert_awaited_once_with(
                camera, "offer", "session", send_message
            )
            await camera.async_on_webrtc_candidate("session", "candidate")
            provider.async_on_webrtc_candidate.assert_awaited_once_with("session", "candidate")
            camera.close_webrtc_session("session")
            provider.async_close_session.assert_called_once_with("session")

    async def test_webrtc_provider_selection_and_signalling_preserve_ha_messages(self):
        entry = SimpleNamespace(
            entry_id="1", title="Fake",
            data={"stream_url": "rtsp://127.0.0.1:8554/a"}, options={},
        )
        coordinator = SimpleNamespace(
            data=CameraData(SessionStatus("supervising", None), True),
            last_update_success=True,
        )
        camera = NetvueCamera(entry, coordinator)
        candidate = RTCIceCandidateInit(
            candidate="candidate:1 1 UDP 1 192.0.2.1 1234 typ host",
            sdp_mid="0", sdp_m_line_index=0,
        )
        messages = [
            WebRTCAnswer("answer"), WebRTCCandidate(candidate),
            WebRTCError("failed", "negotiation failed"),
        ]

        async def offer(_camera, _offer, _session, send_message):
            for message in messages:
               send_message(message)

        provider = unittest.mock.Mock()
        provider.async_is_supported.side_effect = lambda source: source == entry.data["stream_url"]
        provider.async_register_camera = unittest.mock.AsyncMock()
        provider.async_unregister_camera = unittest.mock.AsyncMock()
        provider.async_handle_async_webrtc_offer = unittest.mock.AsyncMock(side_effect=offer)
        provider.async_on_webrtc_candidate = unittest.mock.AsyncMock()
        camera.hass = SimpleNamespace(data={DATA_WEBRTC_PROVIDERS: {provider}})
        await camera.async_refresh_providers(write_state=False)
        provider.async_is_supported.assert_called_once_with(entry.data["stream_url"])
        self.assertIs(camera.webrtc_provider, provider)

        received = []
        await camera.async_handle_async_webrtc_offer("offer", "session", received.append)
        self.assertEqual(received, messages)
        await camera.async_on_webrtc_candidate("session", candidate)
        provider.async_on_webrtc_candidate.assert_awaited_once_with("session", candidate)
        camera.close_webrtc_session("session")
        provider.async_close_session.assert_called_once_with("session")

        camera.hass.data[DATA_WEBRTC_PROVIDERS].clear()
        await camera.async_refresh_providers(write_state=False)
        provider.async_unregister_camera.assert_awaited_once_with(camera)
        self.assertIsNone(camera.webrtc_provider)
        self.assertEqual(camera.camera_capabilities.frontend_stream_types, {StreamType.HLS})
        with self.assertRaises(HomeAssistantError):
            await camera.async_on_webrtc_candidate("session", candidate)

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

    async def test_unavailable_alert_and_actionable_diagnostic(self):
        entry = SimpleNamespace(entry_id="1", title="Fake")
        coordinator = SimpleNamespace(
            data=CameraData(SessionStatus("supervising", None), True),
            last_update_success=True,
        )
        unavailable = NetvueCameraUnavailableSensor(entry, coordinator)
        diagnostic = NetvueDiagnosticSensor(entry, coordinator)
        self.assertFalse(unavailable.is_on)
        self.assertEqual(diagnostic.native_value, "healthy")

        coordinator.data = CameraData(SessionStatus("supervising", None), False)
        self.assertTrue(unavailable.is_on)
        self.assertEqual(diagnostic.native_value, "stream_unavailable")

        cases = [
            (CameraData(None, False, "Cannot reach the local bridge manager"),
             "bridge_unavailable"),
            (CameraData(SessionStatus("failed", "Camera authentication rejected [authentication]"),
                        False), "authentication_failed"),
            (CameraData(SessionStatus("failed", "Connection error [camera connection]"),
                        False), "connection_failed"),
            (CameraData(SessionStatus("failed", "Video publication failed [RTSP publication]"),
                        False), "publication_failed"),
            (CameraData(SessionStatus("failed", "Invalid Netvue protocol data [LIVE response]"),
                        False), "protocol_error"),
            (CameraData(SessionStatus("failed", "Video stream not ready or interrupted [LIVE packet]"),
                        False), "video_interrupted"),
            (CameraData(SessionStatus("failed", "Unexpected camera session error"), False),
             "session_failed"),
            (CameraData(SessionStatus("stopped", None), False), "session_stopped"),
            (CameraData(None, False), "session_unknown"),
        ]
        for data, expected in cases:
            with self.subTest(expected=expected):
                coordinator.data = data
                self.assertEqual(_diagnostic_state(data), expected)
                self.assertEqual(diagnostic.native_value, expected)

        coordinator.data = CameraData(
            SessionStatus("failed", "Camera authentication rejected [authentication]"), False
        )
        self.assertEqual(
            diagnostic.extra_state_attributes,
            {"details": "Camera authentication rejected [authentication]"},
        )
        coordinator.data = CameraData(None, False, "Cannot reach the local bridge manager")
        self.assertEqual(
            diagnostic.extra_state_attributes,
            {"details": "Cannot reach the local bridge manager"},
        )

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
            async_forward_entry_unload=unittest.mock.AsyncMock(return_value=True),
            async_forward_entry_setups=unittest.mock.AsyncMock(),
        )
        hass = SimpleNamespace(config_entries=entries)
        with patch(
            "custom_components.netvue_local.async_stop_camera",
            new=unittest.mock.AsyncMock(side_effect=BridgeError("offline")),
        ) as stop:
            self.assertFalse(await async_unload_entry(hass, entry))
        stop.assert_awaited_once_with(hass, "192.168.10.2")
        entries.async_forward_entry_setups.assert_awaited_once_with(
            entry, [Platform.CAMERA, Platform.BINARY_SENSOR, Platform.SENSOR,
                    Platform.BUTTON, Platform.SWITCH]
        )

    async def test_cancelled_unload_waits_and_restores_only_successful_platforms(self):
        for result in (True, False):
            with self.subTest(result=result):
                entry = SimpleNamespace(data={
                    "host": "192.168.10.2", "stream_name": "a",
                    "stream_url": "rtsp://127.0.0.1:8554/a",
                }, options={})
                started, proceed = asyncio.Event(), asyncio.Event()
                restore_started, restore_proceed = asyncio.Event(), asyncio.Event()

                async def unload(_entry, platform):
                    if platform == Platform.CAMERA:
                        return True
                    started.set()
                    await proceed.wait()
                    return result

                async def restore(_entry, _platforms):
                    restore_started.set()
                    await restore_proceed.wait()

                entries = SimpleNamespace(
                    async_forward_entry_unload=unittest.mock.AsyncMock(side_effect=unload),
                    async_forward_entry_setups=unittest.mock.AsyncMock(side_effect=restore),
                )
                hass = SimpleNamespace(config_entries=entries)
                with patch("custom_components.netvue_local.async_stop_camera",
                           new=unittest.mock.AsyncMock()) as stop:
                    task = asyncio.create_task(async_unload_entry(hass, entry))
                    await asyncio.wait_for(started.wait(), 2)
                    task.cancel()
                    await asyncio.sleep(0)
                    self.assertFalse(task.done())
                    proceed.set()
                    await asyncio.wait_for(restore_started.wait(), 2)
                    task.cancel()  # A second cancellation must not interrupt rollback.
                    await asyncio.sleep(0)
                    self.assertFalse(task.done())
                    restore_proceed.set()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                expected = [Platform.CAMERA]
                if result:
                    expected.append(Platform.BINARY_SENSOR)
                entries.async_forward_entry_setups.assert_awaited_once_with(entry, expected)
                self.assertEqual(entries.async_forward_entry_unload.await_count, 2)
                stop.assert_not_awaited()

    async def test_unload_exception_restores_only_previously_unloaded_platforms(self):
        entry = SimpleNamespace(data={
            "host": "192.168.10.2", "stream_name": "a",
            "stream_url": "rtsp://127.0.0.1:8554/a",
        }, options={})
        entries = SimpleNamespace(
            async_forward_entry_unload=unittest.mock.AsyncMock(
                side_effect=[True, RuntimeError("unload failed")]),
            async_forward_entry_setups=unittest.mock.AsyncMock(),
        )
        with patch("custom_components.netvue_local.async_stop_camera",
                   new=unittest.mock.AsyncMock()) as stop:
            with self.assertRaisesRegex(RuntimeError, "unload failed"):
                await async_unload_entry(SimpleNamespace(config_entries=entries), entry)
        entries.async_forward_entry_setups.assert_awaited_once_with(entry, [Platform.CAMERA])
        stop.assert_not_awaited()

    async def test_cancel_during_confirmed_delete_completes_unload(self):
        entry = SimpleNamespace(data={
            "host": "192.168.10.2", "stream_name": "a",
            "stream_url": "rtsp://127.0.0.1:8554/a",
        }, options={})
        entries = SimpleNamespace(
            async_forward_entry_unload=unittest.mock.AsyncMock(return_value=True),
            async_forward_entry_setups=unittest.mock.AsyncMock(),
        )
        started, proceed = asyncio.Event(), asyncio.Event()

        async def delete(_hass, _host):
            started.set()
            await proceed.wait()

        with patch("custom_components.netvue_local.async_stop_camera", side_effect=delete):
            task = asyncio.create_task(async_unload_entry(SimpleNamespace(config_entries=entries), entry))
            await asyncio.wait_for(started.wait(), 2)
            task.cancel()
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            proceed.set()
            self.assertTrue(await task)
        entries.async_forward_entry_setups.assert_not_awaited()

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
        self.delete_started = asyncio.Event()
        self.delete_continue = asyncio.Event()
        self.delay_delete = False
        self.post_status = 202
        self.invalid_post_json = False
        self.inconsistent_post = False
        self.invalid_json = False
        self.status = {}
        self.body = None

        async def handler(request):
            self.requests.append((request.method, request.path))
            if request.method == "DELETE":
                self.delete_started.set()
                if self.delay_delete:
                    await self.delete_continue.wait()
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
                if self.invalid_post_json:
                    return web.Response(text="{", content_type="application/json",
                                        status=self.post_status)
                response = dict(self.current)
                if self.inconsistent_post:
                    response["stream_name"] = "other"
                return web.json_response(response, status=self.post_status)
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
        self.delete_continue.set()
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

    async def test_ambiguous_post_blocks_retries_even_after_state_is_collected(self):
        for status, invalid_json, inconsistent in (
            (500, False, False), (502, False, False), (202, True, False),
            (200, True, False), (202, False, True),
        ):
            with self.subTest(status=status, invalid_json=invalid_json, inconsistent=inconsistent):
                hass = SimpleNamespace(data={})
                camera = ManagedCamera(hass, self.host, self.name)
                self.current = None
                self.requests.clear()
                self.post_status = status
                self.invalid_post_json = invalid_json
                self.inconsistent_post = inconsistent
                with self.assertRaises(BridgeError):
                    await camera.async_reconcile()
                self.assertIsNotNone(self.current)  # Server inserted before the bad response.
                self.current = None  # A later GET must not authorize another create.
                del camera
                gc.collect()
                camera = ManagedCamera(hass, self.host, self.name)
                with self.assertRaisesRegex(BridgeError, "creation outcome is uncertain"):
                    await camera.async_reconcile()
                with self.assertRaisesRegex(BridgeError, "creation outcome is uncertain"):
                    await async_stop_camera(hass, self.host)
                with self.assertRaisesRegex(BridgeError, "operation outcome is uncertain"):
                    await camera.async_start()
                self.assertEqual([method for method, _ in self.requests], ["GET", "POST"])

    async def test_cancel_after_post_headers_preserves_creation_guard(self):
        body_started = asyncio.Event()

        async def read_body():
            body_started.set()
            await asyncio.Event().wait()

        missing = unittest.mock.MagicMock()
        missing.status = 404
        created = unittest.mock.MagicMock()
        created.status = 202
        created.json = unittest.mock.AsyncMock(side_effect=read_body)
        get_context = unittest.mock.MagicMock()
        get_context.__aenter__ = unittest.mock.AsyncMock(return_value=missing)
        get_context.__aexit__ = unittest.mock.AsyncMock(return_value=False)
        post_context = unittest.mock.MagicMock()
        post_context.__aenter__ = unittest.mock.AsyncMock(return_value=created)
        post_context.__aexit__ = unittest.mock.AsyncMock(return_value=False)
        session = SimpleNamespace(get=unittest.mock.Mock(return_value=get_context),
                                  post=unittest.mock.Mock(return_value=post_context))
        with patch("custom_components.netvue_local.bridge_api.async_get_clientsession",
                   return_value=session):
            task = asyncio.create_task(self.camera.async_reconcile())
            await asyncio.wait_for(body_started.wait(), 2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            with self.assertRaisesRegex(BridgeError, "creation outcome is uncertain"):
                await self.camera.async_reconcile()
            with self.assertRaisesRegex(BridgeError, "creation outcome is uncertain"):
                await async_stop_camera(self.hass, self.host)
        session.get.assert_called_once()
        session.post.assert_called_once()

    async def test_pre_insertion_post_rejections_allow_recovery(self):
        for status in (400, 409, 503):
            with self.subTest(status=status):
                hass = SimpleNamespace(data={})
                camera = ManagedCamera(hass, self.host, self.name)
                self.current = None
                self.requests.clear()
                self.status["POST"] = status
                with self.assertRaisesRegex(BridgeError, f"HTTP {status}"):
                    await camera.async_reconcile()
                self.status.clear()
                self.assertEqual(await camera.async_reconcile(),
                                 SessionStatus("supervising", None))
                self.assertEqual([method for method, _ in self.requests],
                                 ["GET", "POST", "GET", "POST"])

    async def test_cancel_during_failed_delete_restores_and_keeps_guard(self):
        self.current = dict(self.record)
        self.delay_delete = True
        self.status["DELETE"] = 500
        entries = SimpleNamespace(
            async_forward_entry_unload=unittest.mock.AsyncMock(return_value=True),
            async_forward_entry_setups=unittest.mock.AsyncMock(),
        )
        self.hass.config_entries = entries
        entry = SimpleNamespace(data={
            "host": self.host, "stream_name": self.name,
            "stream_url": self.record["rtsp_url"],
        }, options={})
        task = asyncio.create_task(async_unload_entry(self.hass, entry))
        await asyncio.wait_for(self.delete_started.wait(), 2)
        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        self.delete_continue.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        entries.async_forward_entry_setups.assert_awaited_once_with(
            entry, [Platform.CAMERA, Platform.BINARY_SENSOR, Platform.SENSOR,
                    Platform.BUTTON, Platform.SWITCH])
        self.current = None  # The failed DELETE may have removed the session.
        with self.assertRaisesRegex(BridgeError, "deletion outcome is uncertain"):
            await self.camera.async_reconcile()
        self.assertEqual([method for method, _ in self.requests], ["DELETE", "GET"])

    async def test_cancel_during_confirmed_delete_invalidates_coordinator(self):
        self.current = dict(self.record)
        self.delay_delete = True
        entries = SimpleNamespace(
            async_forward_entry_unload=unittest.mock.AsyncMock(return_value=True),
            async_forward_entry_setups=unittest.mock.AsyncMock(),
        )
        self.hass.config_entries = entries
        entry = SimpleNamespace(data={
            "host": self.host, "stream_name": self.name,
            "stream_url": self.record["rtsp_url"],
        }, options={})
        task = asyncio.create_task(async_unload_entry(self.hass, entry))
        await asyncio.wait_for(self.delete_started.wait(), 2)
        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        self.delete_continue.set()
        self.assertTrue(await task)
        entries.async_forward_entry_setups.assert_not_awaited()
        self.assertIsNone(self.current)
        self.assertIsNone(await self.camera.async_reconcile())
        self.assertEqual([method for method, _ in self.requests], ["DELETE"])

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

    async def test_failed_delete_does_not_recreate_missing_session(self):
        await self.camera.async_reconcile()
        self.status["DELETE"] = 500
        with self.assertRaisesRegex(BridgeError, "HTTP 500"):
            await async_stop_camera(self.hass, self.host)

        self.status.clear()
        self.assertEqual(await self.camera.async_reconcile(), SessionStatus("supervising", None))
        self.current = None
        with self.assertRaisesRegex(BridgeError, "deletion outcome is uncertain"):
            await self.camera.async_reconcile()
        self.assertEqual(
            [method for method, _ in self.requests],
            ["GET", "POST", "DELETE", "GET", "GET"],
        )
        await async_stop_camera(self.hass, self.host)
        replacement = ManagedCamera(self.hass, self.host, self.name)
        await replacement.async_reconcile()
        self.assertEqual(self.current["state"], "supervising")

    async def test_uncertain_delete_survives_without_a_coordinator(self):
        hass = SimpleNamespace(data={})
        self.status["DELETE"] = 500
        with self.assertRaisesRegex(BridgeError, "HTTP 500"):
            await async_stop_camera(hass, self.host)
        gc.collect()
        self.status.clear()
        camera = ManagedCamera(hass, self.host, self.name)
        with self.assertRaisesRegex(BridgeError, "deletion outcome is uncertain"):
            await camera.async_reconcile()
        self.assertEqual([method for method, _ in self.requests], ["DELETE", "GET"])

    async def test_timed_out_create_cannot_be_followed_by_delete(self):
        self.delay_post = True
        with patch("custom_components.netvue_local.bridge_api.REQUEST_TIMEOUT", 1.5):
            with self.assertRaisesRegex(BridgeError, "Cannot reach"):
                await self.camera.async_reconcile()
        self.assertTrue(self.post_started.is_set())
        with self.assertRaisesRegex(BridgeError, "creation outcome is uncertain"):
            await async_stop_camera(self.hass, self.host)
        with self.assertRaisesRegex(BridgeError, "creation outcome is uncertain"):
            await self.camera.async_reconcile()
        with self.assertRaisesRegex(BridgeError, "operation outcome is uncertain"):
            await self.camera.async_start()
        self.post_continue.set()
        for _ in range(100):
            if self.current is not None:
                break
            await asyncio.sleep(0.01)
        self.assertIsNotNone(self.current)
        self.assertEqual([method for method, _ in self.requests], ["GET", "POST"])

    async def test_failed_platform_unload_keeps_failed_session_and_coordinator(self):
        self.current = {
            **self.record, "state": "failed", "error": "Camera authentication rejected",
        }
        self.hass.config_entries = SimpleNamespace(
            async_forward_entry_unload=unittest.mock.AsyncMock(side_effect=[True, False]),
            async_forward_entry_setups=unittest.mock.AsyncMock(),
        )
        entry = SimpleNamespace(data={
            "host": self.host, "stream_name": self.name,
            "stream_url": self.record["rtsp_url"],
        }, options={})

        self.assertFalse(await async_unload_entry(self.hass, entry))
        self.hass.config_entries.async_forward_entry_setups.assert_awaited_once_with(
            entry, [Platform.CAMERA]
        )
        self.assertEqual(await self.camera.async_reconcile(),
                         SessionStatus("failed", "Camera authentication rejected"))
        self.assertEqual([method for method, _ in self.requests], ["GET"])
        await self.camera.async_start()
        self.assertEqual(self.current["state"], "supervising")

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
            self.assertEqual(
                result,
                CameraData(
                    SessionStatus("supervising", None),
                    True,
                    recording=RecordingStatus(False, False, False),
                ),
            )
            self.current = None
            self.assertEqual(await NetvueCoordinator._async_update_data(coordinator), result)
        self.assertEqual([method for method, _ in self.requests],
                         ["GET", "POST", "GET", "POST"])
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
                             CameraData(None, True, "Bridge returned HTTP 503"))
        probe.assert_awaited_once_with(entry.data["stream_url"])

if __name__ == "__main__":
    unittest.main()
