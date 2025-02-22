"""Support for media browsing."""
from __future__ import annotations

from collections.abc import Callable
from functools import partial
from urllib.parse import quote_plus

from homeassistant.components import media_source
from homeassistant.components.media_player import BrowseMedia
from homeassistant.components.media_player.const import (
    MEDIA_CLASS_APP,
    MEDIA_CLASS_CHANNEL,
    MEDIA_CLASS_DIRECTORY,
    MEDIA_TYPE_APP,
    MEDIA_TYPE_APPS,
    MEDIA_TYPE_CHANNEL,
    MEDIA_TYPE_CHANNELS,
)
from homeassistant.components.media_player.errors import BrowseError
from homeassistant.core import HomeAssistant
from homeassistant.helpers.network import is_internal_request

from .coordinator import RokuDataUpdateCoordinator
from .helpers import format_channel_name

CONTENT_TYPE_MEDIA_CLASS = {
    MEDIA_TYPE_APP: MEDIA_CLASS_APP,
    MEDIA_TYPE_APPS: MEDIA_CLASS_APP,
    MEDIA_TYPE_CHANNEL: MEDIA_CLASS_CHANNEL,
    MEDIA_TYPE_CHANNELS: MEDIA_CLASS_CHANNEL,
}

CONTAINER_TYPES_SPECIFIC_MEDIA_CLASS = {
    MEDIA_TYPE_APPS: MEDIA_CLASS_DIRECTORY,
    MEDIA_TYPE_CHANNELS: MEDIA_CLASS_DIRECTORY,
}

PLAYABLE_MEDIA_TYPES = [
    MEDIA_TYPE_APP,
    MEDIA_TYPE_CHANNEL,
]

EXPANDABLE_MEDIA_TYPES = [
    MEDIA_TYPE_APPS,
    MEDIA_TYPE_CHANNELS,
]

GetBrowseImageUrlType = Callable[[str, str, "str | None"], str]


def get_thumbnail_url_full(
    coordinator: RokuDataUpdateCoordinator,
    is_internal: bool,
    get_browse_image_url: GetBrowseImageUrlType,
    media_content_type: str,
    media_content_id: str,
    media_image_id: str | None = None,
) -> str | None:
    """Get thumbnail URL."""
    if is_internal:
        if media_content_type == MEDIA_TYPE_APP and media_content_id:
            return coordinator.roku.app_icon_url(media_content_id)
        return None

    return get_browse_image_url(
        media_content_type,
        quote_plus(media_content_id),
        media_image_id,
    )


async def async_browse_media(
    hass: HomeAssistant,
    coordinator: RokuDataUpdateCoordinator,
    get_browse_image_url: GetBrowseImageUrlType,
    media_content_id: str | None,
    media_content_type: str | None,
) -> BrowseMedia:
    """Browse media."""
    if media_content_id is None:
        return await root_payload(
            hass,
            coordinator,
            get_browse_image_url,
        )

    if media_source.is_media_source_id(media_content_id):
        return await media_source.async_browse_media(hass, media_content_id)

    payload = {
        "search_type": media_content_type,
        "search_id": media_content_id,
    }

    response = await hass.async_add_executor_job(
        build_item_response,
        coordinator,
        payload,
        partial(
            get_thumbnail_url_full,
            coordinator,
            is_internal_request(hass),
            get_browse_image_url,
        ),
    )

    if response is None:
        raise BrowseError(f"Media not found: {media_content_type} / {media_content_id}")

    return response


async def root_payload(
    hass: HomeAssistant,
    coordinator: RokuDataUpdateCoordinator,
    get_browse_image_url: GetBrowseImageUrlType,
) -> BrowseMedia:
    """Return root payload for Roku."""
    device = coordinator.data

    children = [
        item_payload(
            {"title": "Apps", "type": MEDIA_TYPE_APPS},
            coordinator,
            get_browse_image_url,
        )
    ]

    if device.info.device_type == "tv" and len(device.channels) > 0:
        children.append(
            item_payload(
                {"title": "TV Channels", "type": MEDIA_TYPE_CHANNELS},
                coordinator,
                get_browse_image_url,
            )
        )

    try:
        browse_item = await media_source.async_browse_media(hass, None)

        # If domain is None, it's overview of available sources
        if browse_item.domain is None:
            if browse_item.children is not None:
                children.extend(browse_item.children)
        else:
            children.append(browse_item)
    except media_source.BrowseError:
        pass

    if len(children) == 1:
        return await async_browse_media(
            hass,
            coordinator,
            get_browse_image_url,
            children[0].media_content_id,
            children[0].media_content_type,
        )

    return BrowseMedia(
        title="Roku",
        media_class=MEDIA_CLASS_DIRECTORY,
        media_content_id="",
        media_content_type="root",
        can_play=False,
        can_expand=True,
        children=children,
    )


def build_item_response(
    coordinator: RokuDataUpdateCoordinator,
    payload: dict,
    get_browse_image_url: GetBrowseImageUrlType,
) -> BrowseMedia | None:
    """Create response payload for the provided media query."""
    search_id = payload["search_id"]
    search_type = payload["search_type"]

    thumbnail = None
    title = None
    media = None
    children_media_class = None

    if search_type == MEDIA_TYPE_APPS:
        title = "Apps"
        media = [
            {"app_id": item.app_id, "title": item.name, "type": MEDIA_TYPE_APP}
            for item in coordinator.data.apps
        ]
        children_media_class = MEDIA_CLASS_APP
    elif search_type == MEDIA_TYPE_CHANNELS:
        title = "TV Channels"
        media = [
            {
                "channel_number": channel.number,
                "title": format_channel_name(channel.number, channel.name),
                "type": MEDIA_TYPE_CHANNEL,
            }
            for channel in coordinator.data.channels
        ]
        children_media_class = MEDIA_CLASS_CHANNEL

    if title is None or media is None:
        return None

    return BrowseMedia(
        media_class=CONTAINER_TYPES_SPECIFIC_MEDIA_CLASS.get(
            search_type, MEDIA_CLASS_DIRECTORY
        ),
        media_content_id=search_id,
        media_content_type=search_type,
        title=title,
        can_play=search_type in PLAYABLE_MEDIA_TYPES and search_id,
        can_expand=True,
        children=[
            item_payload(item, coordinator, get_browse_image_url) for item in media
        ],
        children_media_class=children_media_class,
        thumbnail=thumbnail,
    )


def item_payload(
    item: dict,
    coordinator: RokuDataUpdateCoordinator,
    get_browse_image_url: GetBrowseImageUrlType,
) -> BrowseMedia:
    """
    Create response payload for a single media item.

    Used by async_browse_media.
    """
    thumbnail = None

    if "app_id" in item:
        media_content_type = MEDIA_TYPE_APP
        media_content_id = item["app_id"]
        thumbnail = get_browse_image_url(media_content_type, media_content_id, None)
    elif "channel_number" in item:
        media_content_type = MEDIA_TYPE_CHANNEL
        media_content_id = item["channel_number"]
    else:
        media_content_type = item["type"]
        media_content_id = ""

    title = item["title"]
    can_play = media_content_type in PLAYABLE_MEDIA_TYPES and media_content_id
    can_expand = media_content_type in EXPANDABLE_MEDIA_TYPES

    return BrowseMedia(
        title=title,
        media_class=CONTENT_TYPE_MEDIA_CLASS[media_content_type],
        media_content_type=media_content_type,
        media_content_id=media_content_id,
        can_play=can_play,
        can_expand=can_expand,
        thumbnail=thumbnail,
    )
