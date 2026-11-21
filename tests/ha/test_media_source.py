"""Netvue recordings through Home Assistant's media browser."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from datetime import timedelta

from aiohttp.test_utils import TestClient, TestServer
from homeassistant.components.media_player import BrowseError, async_process_play_media_url
from homeassistant.components.http.auth import async_sign_path
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
                self.assertEqual(int(response.headers["Content-Length"]), len(self.bridge.recording_content))
                self.assertEqual(response.headers["Accept-Ranges"], "bytes")
                partial = await client.get(
                    signed_url, headers={"Range": "bytes=2-5", "If-Range": '"recording"'}
                )
                self.assertEqual(partial.status, 206)
                self.assertEqual(partial.headers["Content-Range"], "bytes 2-5/15")
                self.assertEqual(await partial.read(), self.bridge.recording_content[2:6])
                self.assertEqual(self.bridge.recording_ranges[-1], ("bytes=2-5", '"recording"'))
                snapshot_url = async_sign_path(
                    self.hass, f"{media.url}?duration=12.5", timedelta(hours=24)
                )
                snapshot = await client.get(snapshot_url, headers={"Range": "bytes=2-5"})
                self.assertEqual(snapshot.status, 206)
                self.assertEqual(self.bridge.recording_durations[-1], "12.5")
                tampered = await client.get(snapshot_url.replace("duration=12.5", "duration=13"))
                self.assertEqual(tampered.status, 401)
                invalid = await client.get(signed_url, headers={"Range": "bytes=999-"})
                self.assertEqual(invalid.status, 416)
                self.assertEqual(invalid.headers["Content-Range"], "bytes */15")
                head = await client.head(signed_url)
                self.assertEqual(head.status, 200)
                self.assertEqual(int(head.headers["Content-Length"]), len(self.bridge.recording_content))
                thumbnail = await client.get(folder.children[0].thumbnail)
                self.assertEqual(thumbnail.status, 200)
                self.assertEqual(thumbnail.content_type, "image/jpeg")
                self.assertEqual(await thumbnail.read(), b"synthetic-jpeg")
                preview_url = async_sign_path(
                    self.hass,
                    f"/api/netvue_local/thumbnail/{entry.entry_id}/{recording_id}?offset=4",
                    timedelta(hours=24),
                )
                preview = await client.get(preview_url)
                self.assertEqual(preview.status, 200)
                self.assertEqual(self.bridge.recording_offsets[-1], "4")
                unsigned_thumbnail = await client.get(folder.children[0].thumbnail.split("?")[0])
                self.assertEqual(unsigned_thumbnail.status, 401)
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
                self.assertIsNone(self.bridge.recording_overlays[-1])
                annotated_url = async_sign_path(
                    self.hass,
                    f"/api/netvue_local/recordings/{entry.entry_id}/{recording_id}"
                    "?overlay=1&duration=12.5",
                    timedelta(hours=24),
                )
                annotated = await client.get(annotated_url)
                self.assertEqual(annotated.status, 200)
                self.assertEqual(self.bridge.recording_overlays[-1], "1")
                self.assertEqual(self.bridge.recording_durations[-1], "12.5")
                tampered = await client.get(annotated_url.replace("overlay=1", "overlay=0"))
                self.assertEqual(tampered.status, 401)
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
