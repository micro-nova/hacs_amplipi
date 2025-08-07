"""Support for interfacing with the AmpliPi Multizone home audio controller."""
# pylint: disable=W1203
import logging

from homeassistant.components import media_source
from homeassistant.components.media_player import MediaPlayerEntity, MediaType
from homeassistant.components.media_player.browse_media import (
    async_process_play_media_url,
)
from homeassistant.const import STATE_IDLE
from pyamplipi.amplipi import AmpliPi
from pyamplipi.models import Announcement

from ..const import SUPPORT_AMPLIPI_ANNOUNCE

_LOGGER = logging.getLogger(__name__)

class AmpliPiAnnouncer(MediaPlayerEntity):
    # Doesn't need to extend AmpliPiMediaPlayer due to being far simpler than those components
    @property
    def should_poll(self):
        """Polling needed."""
        return True

    def __init__(self, namespace: str,
                 vendor: str, version: str, image_base_path: str,
                 client: AmpliPi):
        self._source = None

        self._unique_id = f"{namespace}_announcement"
        self.entity_id = f"media_player.{self._unique_id}"
        self._vendor = vendor
        self._version = version
        self._enabled = True
        self._data_client = client
        self._last_update_successful = True
        self._available = True
        self._extra_attributes: dict = {}
        self._image_base_path = image_base_path
        self._name = "Announcement Channel"
        self._attr_name = self._name
        self._volume = 0.5

    @property
    def available(self):
        return self._available
    
    @property
    def supported_features(self):
        self._attr_app_name = "AmpliPi Announcement Channel"
        return SUPPORT_AMPLIPI_ANNOUNCE
    
    @property
    def media_content_type(self):
        """Content type of current playing media."""
        return MediaType.MUSIC
    
    @property
    def entity_registry_enabled_default(self):
        """Return if the entity should be enabled when first added to the entity registry."""
        return True

    @property
    def volume_level(self):
        return self._volume

    @property
    def unique_id(self):
        """Return unique ID for this entity."""
        return self._unique_id

    @property
    def state(self):
        return STATE_IDLE

    async def async_browse_media(self, media_content_type=None, media_content_id=None):
        """Implement the websocket media browsing helper."""
        return await media_source.async_browse_media(
            self.hass,
            media_content_id,
            content_filter=lambda item: item.media_content_type.startswith("audio/"),
        )

    async def async_play_media(self, media_type, media_id, **kwargs):
        _LOGGER.debug(f'Play Media {media_type} {media_id} {kwargs}')
        if media_source.is_media_source_id(media_id):
            play_item = await media_source.async_resolve_media(self.hass, media_id)
            media_id = play_item.url
            _LOGGER.info(f'Playing media source: {play_item} {media_id}')

        media_id = async_process_play_media_url(self.hass, media_id)
        await self._data_client.announce(
            Announcement(
                media=media_id,
                vol_f=self._volume
            )
        )
        pass


    async def async_set_volume_level(self, volume):
        if volume is None:
            return
        self._volume = volume
