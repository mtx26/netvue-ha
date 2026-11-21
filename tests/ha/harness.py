"""Isolated Home Assistant harness: real core, real flows/registries, fake local bridge.

The Home Assistant version is pinned in requirements-homeassistant.txt.
pytest-homeassistant-custom-component is deliberately not used: its latest
release pins homeassistant==2026.2.3 and does not import with 2026.11.x.
Every test gets a fresh HA instance in a temporary config directory, a
loopback fake bridge manager and a patched RTSP probe; no real camera, network
or production Home Assistant is involved.
"""

import asyncio
import socket
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.resolver import ThreadedResolver
from homeassistant import bootstrap, config_entries, core, loader
from homeassistant.auth import auth_manager_from_config
from homeassistant.helpers import entity_registry as er

from custom_components.netvue_local import bridge_api
from custom_components.netvue_local.const import DOMAIN

HOST = "192.168.10.2"
STREAM_NAME = "netvue_192_168_10_2"


class _Resolver(ThreadedResolver):
    async def real_close(self) -> None:  # HA closes its resolver through this hook
        await self.close()


class FakeBridge:
    """Minimal Netvue Bridge session manager (same contract as the Go API)."""

    def __init__(self) -> None:
        self.cameras: dict[str, dict] = {}
        self.discovered: list[str | dict] = []
        self.requests: list[tuple[str, str]] = []
        self.status_override: int | None = None
        self.turn_response: dict | None = None
        self.recordings: dict[str, list[dict]] = {}
        self.protected: list[dict] = []
        self.recording_content = b"synthetic-video"
        self.recording_ranges: list[tuple[str | None, str | None]] = []
        self.recording_durations: list[str | None] = []
        self.recording_overlays: list[str | None] = []
        self.recording_offsets: list[str | None] = []
        self.port = _free_port()
        self._runner: web.AppRunner | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/api/v1"

    async def start(self) -> None:
        app = web.Application()
        base = "/api/v1/cameras"
        app.router.add_post("/api/v1/discovery", self._discovery)
        app.router.add_get("/api/v1/webrtc/ice", self._ice)
        app.router.add_post(base, self._create)
        app.router.add_get(base + "/{host}", self._get)
        app.router.add_post(base + "/{host}/start", self._start)
        app.router.add_delete(base + "/{host}", self._delete)
        app.router.add_get(base + "/{host}/recordings", self._recordings)
        app.router.add_get(base + "/{host}/recordings/{id}/download", self._recording_download)
        app.router.add_get(base + "/{host}/recordings/{id}/thumbnail", self._recording_thumbnail)
        app.router.add_get(base + "/{host}/protected", self._protected_list)
        app.router.add_get("/api/v1/recordings/protected/health", self._protected_health)
        app.router.add_delete(base + "/{host}/protected/{id}", self._protected_delete)
        app.router.add_get(base + "/{host}/protected/{id}/download", self._protected_download)
        app.router.add_post(base + "/{host}/recording/{mode}", self._recording_mode)
        session = base + "/{host}/webrtc/{session}"
        app.router.add_route("*", session, self._webrtc)
        app.router.add_route("*", session + "/{tail:.*}", self._webrtc)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        await web.TCPSite(self._runner, "127.0.0.1", self.port).start()

    async def stop(self) -> None:
        """Make the manager unreachable (connection refused)."""
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    def _forced(self, request):
        self.requests.append((request.method, request.path))
        if self.status_override is not None:
            return web.Response(status=self.status_override)
        return None

    async def _discovery(self, request):
        return self._forced(request) or web.json_response(
            {"cameras": [{"host": host} if isinstance(host, str) else host
                         for host in self.discovered]})

    async def _ice(self, request):
        if self.turn_response is None:
            return web.Response(status=204)
        return web.json_response(self.turn_response)

    async def _recordings(self, request):
        if (forced := self._forced(request)) is not None:
            return forced
        return web.json_response({"recordings": self.recordings.get(request.match_info["host"], [])})

    async def _protected_list(self, request):
        return web.json_response({"protected": self.protected})

    async def _protected_health(self, request):
        return web.json_response({"healthy": True, "free_bytes": 100, "min_free_bytes": 50})

    async def _protected_delete(self, request):
        recording_id = request.match_info["id"]
        if not any(item["id"] == recording_id for item in self.protected):
            return web.json_response({"error": "not found"}, status=404)
        self.protected = [item for item in self.protected if item["id"] != recording_id]
        return web.json_response({"status": "deleted"})

    async def _protected_download(self, request):
        if not any(item["id"] == request.match_info["id"] for item in self.protected):
            return web.Response(status=404)
        content = self.recording_content
        headers = {"Accept-Ranges": "bytes", "ETag": '"protected"'}
        if request.headers.get("Range"):
            start = request.http_range.start or 0
            stop = min(request.http_range.stop or len(content), len(content))
            if start >= len(content):
                return web.Response(
                    status=416, headers={"Content-Range": f"bytes */{len(content)}"}
                )
            headers["Content-Range"] = f"bytes {start}-{stop - 1}/{len(content)}"
            return web.Response(status=206, body=content[start:stop], headers=headers)
        return web.Response(body=content, headers=headers, content_type="video/mp4")

    async def _recording_download(self, request):
        if (forced := self._forced(request)) is not None:
            return forced
        recordings = self.recordings.get(request.match_info["host"], [])
        if not any(item["id"] == request.match_info["id"] for item in recordings):
            return web.Response(status=404)
        self.recording_ranges.append((request.headers.get("Range"), request.headers.get("If-Range")))
        self.recording_durations.append(request.query.get("duration"))
        self.recording_overlays.append(request.query.get("overlay"))
        headers = {
            "Content-Disposition": 'attachment; filename="recording.mp4"',
            "Accept-Ranges": "bytes",
            "ETag": '"recording"',
        }
        if request.headers.get("Range") and request.headers.get("If-Range", '"recording"') == '"recording"':
            start = request.http_range.start or 0
            stop = min(request.http_range.stop or len(self.recording_content), len(self.recording_content))
            if start >= len(self.recording_content):
                return web.Response(status=416, headers={"Content-Range": f"bytes */{len(self.recording_content)}"})
            headers["Content-Range"] = f"bytes {start}-{stop - 1}/{len(self.recording_content)}"
            return web.Response(
                status=206, body=self.recording_content[start:stop],
                content_type="video/mp4", headers=headers,
            )
        return web.Response(
            body=self.recording_content,
            content_type="video/mp4",
            headers=headers,
        )

    async def _recording_thumbnail(self, request):
        if (forced := self._forced(request)) is not None:
            return forced
        recordings = self.recordings.get(request.match_info["host"], [])
        if not any(item["id"] == request.match_info["id"] for item in recordings):
            return web.Response(status=404)
        self.recording_offsets.append(request.query.get("offset"))
        return web.Response(body=b"synthetic-jpeg", content_type="image/jpeg")

    async def _recording_mode(self, request):
        if (forced := self._forced(request)) is not None:
            return forced
        return web.json_response({
            "manual": request.match_info["mode"] == "start",
            "continuous": False,
            "active": True,
        })

    async def _create(self, request):
        if (forced := self._forced(request)) is not None:
            return forced
        data = await request.json()
        camera = {**data, "rtsp_url": f"rtsp://127.0.0.1:8554/{data['stream_name']}",
                  "state": "supervising"}
        self.cameras[data["host"]] = camera
        return web.json_response(camera, status=202)

    async def _get(self, request):
        if (forced := self._forced(request)) is not None:
            return forced
        camera = self.cameras.get(request.match_info["host"])
        return web.json_response(camera) if camera else web.Response(status=404)

    async def _start(self, request):
        if (forced := self._forced(request)) is not None:
            return forced
        camera = self.cameras.get(request.match_info["host"])
        if camera is None:
            return web.Response(status=404)
        camera.update(state="supervising")
        camera.pop("error", None)
        return web.json_response(camera, status=202)

    async def _webrtc(self, request):
        """Signalling endpoints: accept everything, never produce media events."""
        self.requests.append((request.method, request.path))
        if request.method == "GET":
            await asyncio.sleep(0.1)
            return web.json_response({"events": []})
        return web.Response(status=201 if request.method == "PUT" else 204)

    async def _delete(self, request):
        if (forced := self._forced(request)) is not None:
            return forced
        found = self.cameras.pop(request.match_info["host"], None)
        return web.Response(status=200 if found else 404)


class HassTestCase(unittest.IsolatedAsyncioTestCase):
    """Fresh real HomeAssistant per test; assert on states, registries and the bridge."""

    async def asyncSetUp(self) -> None:
        self.bridge = FakeBridge()
        await self.bridge.start()
        self.rtsp_available = True
        self._tmp = tempfile.TemporaryDirectory(prefix="netvue-ha-test-")
        patches = [
            patch.object(bridge_api, "API_URL", self.bridge.url),
            patch("custom_components.netvue_local.coordinator.async_probe_stream",
                  side_effect=self._probe),
            # http must never bind port 8123 (it would clash with a real HA on this host).
            patch("homeassistant.components.http.HomeAssistantHTTP.async_bind",
                  new_callable=AsyncMock),
            patch("homeassistant.components.http.HomeAssistantHTTP.start",
                  new_callable=AsyncMock),
            patch("homeassistant.components.http.async_get_source_ip",
                  new=AsyncMock(return_value="127.0.0.1")),
            # HA would otherwise open a real mDNS (zeroconf) socket for its DNS resolver.
            patch("homeassistant.helpers.aiohttp_client._async_make_resolver",
                  side_effect=lambda _hass: _Resolver()),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        hass = self.hass = core.HomeAssistant(self._tmp.name)
        loader.async_setup(hass)
        hass.config.skip_pip = True
        hass.config.components.add("homeassistant")
        hass.config_entries = config_entries.ConfigEntries(hass, {"homeassistant": {}})
        await bootstrap.async_load_base_functionality(hass)
        hass.auth = await auth_manager_from_config(hass, [], [])
        hass.set_state(core.CoreState.running)
        self.addAsyncCleanup(self._cleanup)

    async def _probe(self, _url: str) -> bool:
        return self.rtsp_available

    async def _cleanup(self) -> None:
        await self.hass.async_stop(force=True)
        await self.bridge.stop()
        await asyncio.get_running_loop().shutdown_default_executor()
        self._tmp.cleanup()

    # -- helpers ---------------------------------------------------------
    async def create_entry(self, host: str = HOST, name: str = "Test", **options):
        """Add a camera through the manual config flow, as a user would."""
        flow = self.hass.config_entries.flow
        result = await flow.async_init(DOMAIN, context={"source": "user"})
        result = await flow.async_configure(result["flow_id"], {"next_step_id": "manual"})
        result = await flow.async_configure(result["flow_id"], {"name": name, "host": host})
        assert result["type"] == "create_entry", result
        await self.hass.async_block_till_done()
        entry = result["result"]
        if options:
            manager = self.hass.config_entries.options
            result = await manager.async_init(entry.entry_id)
            result = await manager.async_configure(result["flow_id"], {"host": host, **options})
            assert result["type"] == "create_entry", result
            await self.hass.async_block_till_done()
        return entry

    async def add_raw_entry(self, data: dict, options: dict | None = None):
        """Add a pre-existing config entry (e.g. legacy data) without going through the flow."""
        entry = config_entries.ConfigEntry(
            data=data, domain=DOMAIN, title="Raw", version=1, minor_version=1,
            source="user", options=options or {}, unique_id=None, discovery_keys={},
            subentries_data=(),
        )
        await self.hass.config_entries.async_add(entry)
        await self.hass.async_block_till_done()
        return entry

    def entity_id(self, entry, domain: str, suffix: str | None = None) -> str:
        """Resolve an entity by unique id (entity names are translated)."""
        unique_id = entry.entry_id if suffix is None else f"{entry.entry_id}_{suffix}"
        found = er.async_get(self.hass).async_get_entity_id(domain, DOMAIN, unique_id)
        assert found is not None, f"no {domain} entity {unique_id}"
        return found

    def state(self, entry, domain: str, suffix: str | None = None):
        return self.hass.states.get(self.entity_id(entry, domain, suffix))

    async def refresh(self, entry) -> None:
        await entry.runtime_data.async_refresh()
        await self.hass.async_block_till_done()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
