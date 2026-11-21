"""Expose Netvue recordings in the Home Assistant media browser."""

import logging
import math
import re
from datetime import datetime
from urllib.parse import quote

from aiohttp import ClientError
from homeassistant.components.media_player import BrowseError, MediaClass, MediaType
from homeassistant.components.media_source import (
    BrowseMediaSource,
    MediaSource,
    MediaSourceItem,
    PlayMedia,
    Unresolvable,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from . import _recording_entry
from . import bridge_api
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
_RECORDING_ID = re.compile(r"[0-9a-f]{32}\Z")


async def async_get_media_source(hass: HomeAssistant) -> "NetvueMediaSource":
    return NetvueMediaSource(hass)


class NetvueMediaSource(MediaSource):
    name = "Netvue"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(DOMAIN)
        self.hass = hass

    async def async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
        if not item.identifier:
            entries = [
                entry for entry in self.hass.config_entries.async_entries(DOMAIN)
                if entry.state is ConfigEntryState.LOADED
            ]
            return BrowseMediaSource(
                domain=DOMAIN, identifier=None, media_class=MediaClass.APP,
                media_content_type=MediaType.APP, title=self.name,
                can_play=False, can_expand=True, children_media_class=MediaClass.DIRECTORY,
                children=[
                    self._camera_item(entry.entry_id, entry.title)
                    for entry in sorted(entries, key=lambda entry: entry.title.casefold())
                ],
            )

        entry = _recording_entry(self.hass, item.identifier)
        if entry is None or entry.state is not ConfigEntryState.LOADED:
            raise BrowseError("Netvue camera not found")
        url = (
            f"{bridge_api.API_URL}/cameras/"
            f"{quote(entry.runtime_data.camera.host, safe='.')}/recordings"
        )
        try:
            async with async_get_clientsession(self.hass).get(url, timeout=5) as response:
                response.raise_for_status()
                data = await response.json()
        except (ClientError, TimeoutError, ValueError) as err:
            _LOGGER.warning("Unable to load Netvue recording history: %s", err)
            raise BrowseError("Netvue recording history unavailable") from err
        if not isinstance(data, dict) or not isinstance(data.get("recordings"), list):
            raise BrowseError("Invalid Netvue recording history")

        children = []
        for recording in data["recordings"]:
            if not isinstance(recording, dict):
                raise BrowseError("Invalid Netvue recording")
            recording_id = recording.get("id")
            start = recording.get("start")
            duration = recording.get("duration")
            if not isinstance(recording_id, str) or not _RECORDING_ID.fullmatch(recording_id):
                raise BrowseError("Invalid Netvue recording identifier")
            if type(duration) not in (int, float) or not math.isfinite(duration):
                raise BrowseError("Invalid Netvue recording duration")
            if duration <= 0:
                continue
            try:
                timestamp = datetime.fromisoformat(start)
                if timestamp.utcoffset() is None:
                    raise ValueError("timestamp has no timezone")
            except (TypeError, ValueError) as err:
                raise BrowseError("Invalid Netvue recording time") from err
            title = dt_util.as_local(timestamp).strftime("%Y-%m-%d %H:%M:%S")
            children.append(BrowseMediaSource(
                domain=DOMAIN, identifier=f"{entry.entry_id}/{recording_id}",
                media_class=MediaClass.VIDEO, media_content_type="video/mp4",
                title=title, can_play=True, can_expand=False,
            ))
        children.reverse()
        result = self._camera_item(entry.entry_id, entry.title)
        result.children = children
        return result

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        parts = item.identifier.split("/")
        if len(parts) != 2 or not _RECORDING_ID.fullmatch(parts[1]):
            raise Unresolvable("Invalid Netvue recording")
        entry = _recording_entry(self.hass, parts[0])
        if entry is None or entry.state is not ConfigEntryState.LOADED:
            raise Unresolvable("Netvue camera not found")
        return PlayMedia(
            f"/api/netvue_local/playback/{quote(entry.entry_id, safe='')}/{parts[1]}",
            "video/mp4",
        )

    @staticmethod
    def _camera_item(entry_id: str, title: str) -> BrowseMediaSource:
        return BrowseMediaSource(
            domain=DOMAIN, identifier=entry_id, media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.APP, title=title,
            can_play=False, can_expand=True, children_media_class=MediaClass.VIDEO,
        )
