"""Synthetic REST signalling tests; no WebRTC media or camera is started."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from aiohttp import ClientSession, web
from homeassistant.components.camera import WebRTCAnswer, WebRTCCandidate, WebRTCError
from homeassistant.components.web_rtc import DATA_ICE_SERVERS
from homeassistant.exceptions import HomeAssistantError
from webrtc_models import RTCIceCandidateInit

from custom_components.netvue_local import camera as camera_platform
from custom_components.netvue_local.const import CONF_HOST, CONF_STREAM_URL
from custom_components.netvue_local.webrtc import NetvueWebRTCProvider


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
        self.ice_status = 204
        self.ice_payload = None
        self.client = ClientSession()
        app = web.Application()
        base = "/api/v1/cameras/{host}/webrtc/{session}"

        async def ice_handler(request):
            self.requests.append((request.method, request.path, request.query_string, None))
            if self.ice_status in (204, 404, 503):
                return web.Response(status=self.ice_status)
            return web.json_response(self.ice_payload, status=self.ice_status)

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
        app.router.add_get("/api/v1/webrtc/ice", ice_handler)
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

    async def setup_camera_platform(self):
        coordinator = SimpleNamespace(
            data=None,
            last_update_success=True,
            async_add_listener=lambda listener: lambda: None,
        )
        hass = SimpleNamespace(
            config=SimpleNamespace(webrtc=SimpleNamespace(ice_servers=[])),
            data={DATA_ICE_SERVERS: []},
        )
        entry = SimpleNamespace(
            options={},
            runtime_data=coordinator,
            data={CONF_HOST: "192.168.10.2", CONF_STREAM_URL: "rtsp://127.0.0.1:8554/a"},
            entry_id="entry",
            title="Netvue",
        )
        entities = []
        await camera_platform.async_setup_entry(
            hass, entry, entities.extend,
        )
        self.assertEqual(len(entities), 1)
        entities[0].hass = hass
        return entities[0]

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

    async def test_turn_404_keeps_the_regular_camera_entity(self):
        self.ice_status = 404

        entity = await self.setup_camera_platform()

        self.assertIsInstance(entity, camera_platform.NetvueCamera)
        self.assertNotIsInstance(entity, camera_platform.NetvueTurnCamera)
        self.assertEqual(self.requests[0][1], "/api/v1/webrtc/ice")

    async def test_turn_503_keeps_native_webrtc_without_turn_servers(self):
        self.ice_status = 503

        entity = await self.setup_camera_platform()

        self.assertIsInstance(entity, camera_platform.NetvueTurnCamera)
        config = entity.async_get_webrtc_client_configuration().to_frontend_dict()
        self.assertFalse(any(
            url.startswith(("turn:", "turns:"))
            for server in config["configuration"]["iceServers"]
            for url in server["urls"]
        ))

    async def test_malformed_turn_responses_are_reported_without_credentials(self):
        malformed_payloads = (
            {
                "ice_servers": "temporary-user",
                "credential": "temporary-password",
                "expires_in": 1800,
            },
            {
                "ice_servers": [{
                    "urls": [123],
                    "username": "temporary-user",
                    "credential": "temporary-password",
                }],
                "expires_in": 1800,
            },
            {
                "ice_servers": [{
                    "urls": ["turns:turn.example:443?transport=tcp"],
                    "username": "temporary-user",
                    "credential": {"value": "temporary-password"},
                }],
                "expires_in": 1800,
            },
        )
        self.ice_status = 200
        for payload in malformed_payloads:
            with self.subTest(payload=payload):
                self.ice_payload = payload
                with self.assertLogs("custom_components.netvue_local.camera",
                                     level="WARNING") as captured:
                    entity = await self.setup_camera_platform()
                self.assertIsInstance(entity, camera_platform.NetvueCamera)
                self.assertNotIsInstance(entity, camera_platform.NetvueTurnCamera)
                logged = "\n".join(captured.output)
                self.assertIn("Unable to read Netvue TURN configuration", logged)
                self.assertNotIn("temporary-user", logged)
                self.assertNotIn("temporary-password", logged)

    async def test_turn_credentials_are_removed_from_public_configuration_at_expiry(self):
        self.ice_status = 200
        self.ice_payload = {
            "ice_servers": [{
                "urls": ["turns:turn.example:443?transport=tcp"],
                "username": "temporary-user",
                "credential": "temporary-password",
            }],
            "expires_in": 60,
        }
        now = [100.0]
        with patch("custom_components.netvue_local.webrtc.monotonic",
                   side_effect=lambda: now[0]):
            entity = await self.setup_camera_platform()
            configuration = entity.async_get_webrtc_client_configuration()
            initial_servers = configuration.to_frontend_dict()["configuration"]["iceServers"]
            self.assertIn(self.ice_payload["ice_servers"][0], initial_servers)

            now[0] = 160.0
            expired = entity.async_get_webrtc_client_configuration()

        expired_servers = expired.to_frontend_dict()["configuration"]["iceServers"]
        self.assertNotIn(self.ice_payload["ice_servers"][0], expired_servers)

    async def test_push_to_talk_offer_is_forwarded(self):
        offer = (
            "v=0\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\na=recvonly\r\n"
            "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=sendrecv\r\n"
            "m=application 9 UDP/DTLS/SCTP webrtc-datachannel\r\n"
        )
        await self.provider.async_handle_async_webrtc_offer(
            self.camera, offer, "talk", lambda _: None)
        await self.wait_for(lambda: any(r[0] == "PUT" for r in self.requests))
        self.assertEqual(next(r for r in self.requests if r[0] == "PUT")[3], {"offer": offer})

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
        self.assertEqual(messages, [WebRTCError(
            "netvue_webrtc_busy", "WebRTC camera has too many viewers")])
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

    async def test_four_concurrent_sessions_then_rejection_without_handoff(self):
        self.block_delete = True
        for index in range(4):
            await self.provider.async_handle_async_webrtc_offer(
                self.camera, "offer", f"s{index}", lambda _: None)
        await self.wait_for(lambda: sum(r[0] == "PUT" for r in self.requests) == 4)
        with self.assertRaisesRegex(HomeAssistantError, "too many viewers"):
            await self.provider.async_handle_async_webrtc_offer(
                self.camera, "offer", "s4", lambda _: None)
        self.assertEqual(len(self.provider._sessions), 4)
        self.assertEqual(sum(r[0] == "PUT" for r in self.requests), 4)
        self.provider.async_close_session("s0")
        await asyncio.wait_for(self.delete_started.wait(), 2)
        # The slot frees only when the DELETE completed; no previous-session wait exists.
        with self.assertRaisesRegex(HomeAssistantError, "too many viewers"):
            await self.provider.async_handle_async_webrtc_offer(
                self.camera, "offer", "s5", lambda _: None)
        self.delete_continue.set()
        await self.wait_for(lambda: "s0" not in self.provider._sessions)
        await self.provider.async_handle_async_webrtc_offer(
            self.camera, "offer", "s6", lambda _: None)
        await self.wait_for(lambda: sum(r[0] == "PUT" for r in self.requests) == 5)

    async def test_sessions_are_isolated(self):
        await self.provider.async_handle_async_webrtc_offer(
            self.camera, "offer", "one", lambda _: None)
        await self.provider.async_handle_async_webrtc_offer(
            self.camera, "offer", "two", lambda _: None)
        await self.wait_for(lambda: sum(r[0] == "PUT" for r in self.requests) == 2)
        self.provider.async_close_session("one")
        await self.wait_for(lambda: "one" not in self.provider._sessions)
        self.assertIn("two", self.provider._sessions)
        await self.provider.async_on_webrtc_candidate("two", RTCIceCandidateInit("candidate:2"))
        self.assertEqual(sum(r[0] == "DELETE" for r in self.requests), 1)
        self.assertTrue(any(r[0] == "POST" and "/two/" in r[1] for r in self.requests))

    async def test_cancelled_shutdown_waits_for_delete(self):
        self.block_delete = True
        await self.provider.async_handle_async_webrtc_offer(
            self.camera, "offer", "one", lambda _: None)
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
