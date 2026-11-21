"""Netvue recordings through Home Assistant's media browser."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer
from homeassistant.components.media_player import BrowseError, async_process_play_media_url
from homeassistant.components.media_source import (
    Unresolvable,
    async_browse_media,
    async_resolve_media,
)

import custom_components.netvue_local as netvue_local
from harness import HOST, HassTestCase


class MediaSourceTests(HassTestCase):
    async def test_browse_and_play_recording_through_authenticated_proxy(self):
        entry = await self.create_entry(name="Entrée")
        other = await self.create_entry(host="192.168.10.3", name="Jardin")
        recording_id = "a" * 32
        self.bridge.recordings[HOST] = [
            {"id": "b" * 32, "start": "2026-01-01T10:01:00Z", "duration": 0},
            {"id": recording_id, "start": "2026-01-01T10:00:00Z", "duration": 30}
        ]
        root = await async_browse_media(self.hass, "media-source://netvue_local")
        self.assertEqual([(child.title, child.media_content_id) for child in root.children], [
            ("Entrée", f"media-source://netvue_local/{entry.entry_id}"),
            ("Jardin", f"media-source://netvue_local/{other.entry_id}"),
        ])
        other_folder = await async_browse_media(self.hass, root.children[1].media_content_id)
        self.assertEqual(other_folder.children, [])
        folder = await async_browse_media(self.hass, root.children[0].media_content_id)
        self.assertEqual(len(folder.children), 1)
        self.assertTrue(folder.children[0].can_play)
        media = await async_resolve_media(self.hass, folder.children[0].media_content_id, None)
        self.assertEqual(media.mime_type, "video/mp4")

        with patch.object(netvue_local, "API_URL", self.bridge.url):
            async with TestClient(TestServer(self.hass.http.app)) as client:
                unsigned = await client.get(media.url)
                self.assertEqual(unsigned.status, 401)
                signed_url = async_process_play_media_url(
                    self.hass, media.url, allow_relative_url=True
                )
                response = await client.get(signed_url)
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers["Content-Disposition"], "inline")
                self.assertEqual(response.content_type, "video/mp4")
                self.assertEqual(await response.read(), self.bridge.recording_content)
                download_url = async_process_play_media_url(
                    self.hass,
                    f"/api/netvue_local/recordings/{entry.entry_id}/{recording_id}",
                    allow_relative_url=True,
                )
                download = await client.get(download_url)
                self.assertEqual(download.status, 200)
                self.assertEqual(download.headers["Content-Disposition"],
                                 'attachment; filename="recording.mp4"')
                self.assertEqual(await download.read(), self.bridge.recording_content)
                missing_url = async_process_play_media_url(
                    self.hass, f"/api/netvue_local/playback/{entry.entry_id}/{'c' * 32}",
                    allow_relative_url=True,
                )
                missing = await client.get(missing_url)
                self.assertEqual(missing.status, 404)
                wrong_camera_url = async_process_play_media_url(
                    self.hass, f"/api/netvue_local/playback/{other.entry_id}/{recording_id}",
                    allow_relative_url=True,
                )
                wrong_camera = await client.get(wrong_camera_url)
                self.assertEqual(wrong_camera.status, 404)

    async def test_invalid_or_unavailable_recordings_are_reported(self):
        entry = await self.create_entry()
        with self.assertRaises(BrowseError):
            await async_browse_media(self.hass, "media-source://netvue_local/no-such-camera")
        with self.assertRaises(Unresolvable):
            await async_resolve_media(
                self.hass, f"media-source://netvue_local/{entry.entry_id}/invalid", None
            )
        self.bridge.status_override = 503
        with self.assertRaises(BrowseError):
            await async_browse_media(self.hass, f"media-source://netvue_local/{entry.entry_id}")

    async def test_card_recording_action_refreshes_the_camera_state(self):
        entry = await self.create_entry()
        request = SimpleNamespace(app={"hass": self.hass}, json=AsyncMock(return_value={"enabled": True}))
        with (
            patch.object(netvue_local, "API_URL", self.bridge.url),
            patch.object(entry.runtime_data, "async_request_refresh", new_callable=AsyncMock) as refresh,
        ):
            response = await netvue_local.NetvueRecordingListView().post(request, entry.entry_id)
        self.assertEqual(response.status, 200)
        refresh.assert_awaited_once()
