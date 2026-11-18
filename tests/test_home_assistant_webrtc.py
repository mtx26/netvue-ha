"""Synthetic REST signalling tests; no WebRTC media or camera is started."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from aiohttp import ClientSession, web
from homeassistant.components.camera import WebRTCAnswer, WebRTCCandidate, WebRTCError
from homeassistant.components.camera.const import DATA_COMPONENT
from homeassistant.components.camera.webrtc import DATA_WEBRTC_PROVIDERS
from homeassistant.exceptions import HomeAssistantError
from webrtc_models import RTCIceCandidateInit

from custom_components.netvue_local.webrtc import NetvueWebRTCProvider, _offer_sends_audio
from custom_components.netvue_local.camera import NetvueCamera


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []
        self.events = asyncio.Queue()
        self.offer_started = asyncio.Event()
        self.offer_continue = asyncio.Event()
        self.delete_started = asyncio.Event()
        self.delete_continue = asyncio.Event()
        self.candidate_started = asyncio.Event()
        self.candidate_continue = asyncio.Event()
        self.block_offer = False
        self.block_delete = False
        self.block_candidate = False
        self.status = {}
        self.client = ClientSession()
        app = web.Application()
        base = "/api/v1/cameras/{host}/webrtc/{session}"

        async def handler(request):
            body = await request.json() if request.method in ("PUT", "POST") else None
            self.requests.append((request.method, request.path, request.query_string, body))
            if request.method == "PUT":
                self.offer_started.set()
                if self.block_offer:
                    await self.offer_continue.wait()
                return web.Response(status=self.status.get("PUT", 201))
            if request.method == "POST":
                self.candidate_started.set()
                if self.block_candidate:
                    await self.candidate_continue.wait()
                return web.Response(status=self.status.get("POST", 204))
            if request.method == "DELETE":
                self.delete_started.set()
                if self.block_delete:
                    await self.delete_continue.wait()
                return web.Response(status=self.status.get("DELETE", 204))
            try:
                result = await asyncio.wait_for(self.events.get(), 0.2)
            except TimeoutError:
                result = {"events": []}
            return web.json_response(result, status=self.status.get("GET", 200))

        app.router.add_put(base, handler)
        app.router.add_post(base + "/candidates", handler)
        app.router.add_get(base + "/events", handler)
        app.router.add_delete(base, handler)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.api_patch = patch("custom_components.netvue_local.bridge_api.API_URL",
                               f"http://127.0.0.1:{port}/api/v1")
        self.client_patch = patch("custom_components.netvue_local.webrtc.async_get_clientsession",
                                  return_value=self.client)
        self.api_patch.start()
        self.client_patch.start()
        self.camera = object()
        entry = SimpleNamespace(async_create_background_task=lambda hass, coro, name, eager_start:
                                asyncio.create_task(coro))
        self.provider = NetvueWebRTCProvider(
            SimpleNamespace(), entry, self.camera,
            "192.168.10.2", "rtsp://127.0.0.1:8554/a",
        )

    async def asyncTearDown(self):
        self.offer_continue.set()
        self.delete_continue.set()
        self.candidate_continue.set()
        await self.provider.async_shutdown()
        await self.client.close()
        await self.runner.cleanup()
        self.client_patch.stop()
        self.api_patch.stop()

    async def wait_for(self, condition):
        async with asyncio.timeout(3):
            while not condition():
                await asyncio.sleep(0.01)

    def test_offer_audio_direction_identifies_microphone_track(self):
        base = "v=0\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\n"
        self.assertTrue(_offer_sends_audio(base + "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=sendrecv\r\n"))
        self.assertTrue(_offer_sends_audio(base + "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=sendonly\r\n"))
        self.assertFalse(_offer_sends_audio(base + "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=recvonly\r\n"))
        self.assertFalse(_offer_sends_audio(
            "v=0\r\na=recvonly\r\n" + base + "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"
        ))
        self.assertFalse(_offer_sends_audio(
            base + "m=audio 0 UDP/TLS/RTP/SAVPF 111\r\na=sendrecv\r\n"
        ))

    async def test_microphone_offer_is_rejected_when_experimental_option_is_off(self):
        offer = (
            "v=0\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\n"
            "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=sendrecv\r\n"
        )
        with self.assertRaisesRegex(HomeAssistantError, "microphone control is disabled"):
            await self.provider.async_handle_async_webrtc_offer(
                self.camera, offer, "microphone-disabled", lambda _: None,
            )
        self.assertEqual(self.requests, [])

    async def test_microphone_offer_is_forwarded_when_experimental_option_is_on(self):
        provider = NetvueWebRTCProvider(
            SimpleNamespace(), SimpleNamespace(async_create_background_task=lambda hass, coro,
                                               name, eager_start: asyncio.create_task(coro)),
            self.camera, "192.168.10.2", "rtsp://127.0.0.1:8554/a",
            allow_microphone=True,
        )
        try:
            await provider.async_handle_async_webrtc_offer(
                self.camera,
                "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=sendonly\r\n",
                "microphone-enabled", lambda _: None,
            )
            await self.wait_for(lambda: any(r[0] == "PUT" for r in self.requests))
        finally:
            await provider.async_shutdown()

    async def test_offer_candidates_events_and_close(self):
        received = []
        candidate = RTCIceCandidateInit("candidate:1", sdp_mid="0", sdp_m_line_index=0)
        self.assertTrue(self.provider.async_is_supported("rtsp://127.0.0.1:8554/a"))
        self.assertFalse(self.provider.async_is_supported("rtsp://127.0.0.1:8554/ab"))
        await self.provider.async_handle_async_webrtc_offer(
            self.camera, "offer-sdp", "session", received.append)
        await self.provider.async_on_webrtc_candidate("session", candidate)
        self.assertEqual(self.requests[0][0:2],
                         ("PUT", "/api/v1/cameras/192.168.10.2/webrtc/session"))
        self.assertEqual(self.requests[0][3], {"offer": "offer-sdp"})
        post = next(item for item in self.requests if item[0] == "POST")
        self.assertEqual(post[3], {"candidate": candidate.to_dict()})
        await self.events.put({"events": [
            {"seq": 1, "type": "answer", "answer": "answer-sdp"},
            {"seq": 2, "type": "candidate", "candidate": candidate.to_dict()},
        ]})
        await self.wait_for(lambda: len(received) == 2)
        self.assertEqual(received, [WebRTCAnswer("answer-sdp"), WebRTCCandidate(candidate)])
        await self.wait_for(lambda: any(r[2] == "after=2" for r in self.requests))
        self.provider.async_close_session("session")
        self.provider.async_close_session("session")
        await self.wait_for(lambda: not self.provider._sessions)
        self.assertEqual(sum(r[0] == "DELETE" for r in self.requests), 1)

    async def test_close_before_worker_starts_and_cancel_open(self):
        await self.provider.async_handle_async_webrtc_offer(
            self.camera, "offer", "early", lambda _: None)
        self.provider.async_close_session("early")
        await self.wait_for(lambda: not self.provider._sessions)
        self.assertEqual([r[0] for r in self.requests], ["DELETE"])
        self.requests.clear()
        self.block_offer = True
        messages = []
        await self.provider.async_handle_async_webrtc_offer(
            self.camera, "offer", "opening", messages.append)
        await asyncio.wait_for(self.offer_started.wait(), 2)
        waiting = asyncio.create_task(
            self.provider.async_on_webrtc_candidate("opening", RTCIceCandidateInit("candidate")))
        self.provider.async_close_session("opening")
        with self.assertRaises(HomeAssistantError):
            await waiting
        await self.wait_for(lambda: not self.provider._sessions)
        self.assertEqual([r[0] for r in self.requests], ["PUT", "DELETE"])
        self.assertEqual(messages, [])

    async def test_rejection_bad_events_and_shutdown(self):
        messages = []
        self.status["PUT"] = 409
        await self.provider.async_handle_async_webrtc_offer(
            self.camera, "offer", "busy", messages.append)
        await self.wait_for(lambda: not self.provider._sessions)
        self.assertIsInstance(messages[0], WebRTCError)
        self.assertIn("409", messages[0].message)
        self.status.clear()
        messages.clear()
        await self.provider.async_handle_async_webrtc_offer(
            self.camera, "offer", "invalid", messages.append)
        await self.events.put({"events": [{"seq": 0, "type": "answer", "answer": "x"}]})
        await self.wait_for(lambda: not self.provider._sessions)
        self.assertIsInstance(messages[0], WebRTCError)
        self.assertIn("sequence", messages[0].message)
        await self.provider.async_handle_async_webrtc_offer(
            self.camera, "offer", "shutdown", messages.append)
        await self.wait_for(lambda: any(r[0] == "GET" and "/shutdown/" in r[1]
                                        for r in self.requests))
        await self.provider.async_shutdown()
        self.assertFalse(self.provider._sessions)
        self.assertFalse(self.provider.async_is_supported("rtsp://127.0.0.1:8554/a"))
        with self.assertRaises(HomeAssistantError):
            await self.provider.async_handle_async_webrtc_offer(
                self.camera, "offer", "closed", messages.append)

    async def test_candidate_rejection_closes_and_wrong_camera_is_rejected(self):
        with self.assertRaises(HomeAssistantError):
            await self.provider.async_handle_async_webrtc_offer(object(), "x", "s", lambda _: None)
        self.status["POST"] = 503
        await self.provider.async_handle_async_webrtc_offer(
            self.camera, "offer", "candidate", lambda _: None)
        with self.assertRaisesRegex(HomeAssistantError, "503"):
            await self.provider.async_on_webrtc_candidate("candidate", RTCIceCandidateInit("x"))
        await self.wait_for(lambda: not self.provider._sessions)
        self.assertEqual(sum(r[0] == "DELETE" for r in self.requests), 1)

    async def test_single_peer_and_cancelled_shutdown_waits_for_delete(self):
        self.block_delete = True
        await self.provider.async_handle_async_webrtc_offer(
            self.camera, "offer", "one", lambda _: None)
        with self.assertRaisesRegex(HomeAssistantError, "already has"):
            await self.provider.async_handle_async_webrtc_offer(
                self.camera, "offer", "two", lambda _: None)
        await asyncio.wait_for(self.offer_started.wait(), 2)
        task = asyncio.create_task(self.provider.async_shutdown())
        await asyncio.wait_for(self.delete_started.wait(), 2)
        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        self.delete_continue.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(self.provider._sessions)
        self.assertEqual(sum(r[0] == "DELETE" for r in self.requests), 1)

    async def test_cancel_candidate_closes_uncertain_session(self):
        self.block_candidate = True
        await self.provider.async_handle_async_webrtc_offer(
            self.camera, "offer", "one", lambda _: None)
        candidate = asyncio.create_task(
            self.provider.async_on_webrtc_candidate("one", RTCIceCandidateInit("candidate")))
        await asyncio.wait_for(self.candidate_started.wait(), 2)
        candidate.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await candidate
        await self.wait_for(lambda: not self.provider._sessions)
        self.assertEqual(sum(r[0] == "DELETE" for r in self.requests), 1)

    async def test_terminal_server_error_is_not_reflected_verbatim(self):
        messages = []
        await self.provider.async_handle_async_webrtc_offer(
            self.camera, "offer", "one", messages.append)
        await self.events.put({"events": [
            {"seq": 1, "type": "error", "code": "failed", "message": "secret"},
        ]})
        await self.wait_for(lambda: not self.provider._sessions)
        self.assertEqual(messages, [WebRTCError(
            "netvue_webrtc_failed", "WebRTC negotiation failed")])

    async def test_entity_registers_provider_only_when_opted_in(self):
        entry = SimpleNamespace(
            entry_id="1", title="Fake",
            data={"host": "192.168.10.2", "stream_url": "rtsp://127.0.0.1:8554/a"},
            options={},
        )
        coordinator = SimpleNamespace(last_update_success=True)
        remove = unittest.mock.Mock()
        with patch("homeassistant.helpers.update_coordinator.CoordinatorEntity.async_added_to_hass",
                   new=unittest.mock.AsyncMock()), \
             patch("homeassistant.helpers.update_coordinator.CoordinatorEntity.async_will_remove_from_hass",
                   new=unittest.mock.AsyncMock()), \
             patch("custom_components.netvue_local.camera.async_register_webrtc_provider",
                   return_value=remove) as register:
            camera = NetvueCamera(entry, coordinator)
            camera.hass = SimpleNamespace()
            await camera.async_added_to_hass()
            register.assert_not_called()
            await camera.async_will_remove_from_hass()
            entry.options = {"webrtc_enabled": True}
            camera = NetvueCamera(entry, coordinator)
            camera.hass = SimpleNamespace()
            await camera.async_added_to_hass()
            register.assert_called_once()
            self.assertIsInstance(register.call_args.args[1], NetvueWebRTCProvider)
            self.assertFalse(camera.camera_entity_capabilities.two_way_audio)
            await camera.async_will_remove_from_hass()
            remove.assert_called_once()

            entry.options = {"webrtc_enabled": True, "experimental_talkback": True}
            camera = NetvueCamera(entry, coordinator)
            camera.hass = SimpleNamespace()
            await camera.async_added_to_hass()
            self.assertTrue(camera.camera_entity_capabilities.two_way_audio)
            self.assertTrue(register.call_args.args[1]._allow_microphone)
            await camera.async_will_remove_from_hass()

    async def test_real_ha_registry_selects_and_removes_provider(self):
        entry = SimpleNamespace(
            entry_id="1", title="Fake",
            data={"host": "192.168.10.2", "stream_url": "rtsp://127.0.0.1:8554/a"},
            options={"webrtc_enabled": True},
        )
        camera = NetvueCamera(entry, SimpleNamespace(last_update_success=True))
        camera.hass = SimpleNamespace(
            data={"camera": object(), DATA_COMPONENT: SimpleNamespace(entities=[camera])},
            async_create_task=asyncio.create_task,
        )
        with patch("homeassistant.helpers.update_coordinator.CoordinatorEntity.async_added_to_hass",
                   new=unittest.mock.AsyncMock()), \
             patch("homeassistant.helpers.update_coordinator.CoordinatorEntity.async_will_remove_from_hass",
                   new=unittest.mock.AsyncMock()), \
             patch.object(camera, "async_write_ha_state"):
            await camera.async_added_to_hass()
            await self.wait_for(lambda: camera.webrtc_provider is not None)
            provider = camera.webrtc_provider
            self.assertIn(provider, camera.hass.data[DATA_WEBRTC_PROVIDERS])
            self.assertFalse(camera.camera_entity_capabilities.two_way_audio)
            await camera.async_will_remove_from_hass()
            await self.wait_for(lambda: camera.webrtc_provider is None)
            self.assertFalse(camera.hass.data[DATA_WEBRTC_PROVIDERS])
