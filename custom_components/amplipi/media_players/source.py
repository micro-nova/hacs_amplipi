"""Support for interfacing with the AmpliPi Multizone home audio controller's four Sources."""
# pylint: disable=W1203
import logging
from typing import List
from homeassistant.components import media_source
from homeassistant.components.media_player import MediaType
from homeassistant.components.media_player.browse_media import (
    async_process_play_media_url,
)
from homeassistant.const import STATE_PLAYING, STATE_PAUSED, STATE_IDLE, STATE_UNKNOWN, STATE_OFF
from pyamplipi.models import ZoneUpdate, SourceUpdate, GroupUpdate, MultiZoneUpdate, PlayMedia

from .media_player import AmpliPiMediaPlayer
from ..coordinator import AmpliPiDataClient
from ..models import Source, Stream
from ..utils import get_fixed_source_id

_LOGGER = logging.getLogger(__name__)

class AmpliPiSource(AmpliPiMediaPlayer):
    """Representation of an AmpliPi Source Input, of which 4 are supported (Hard Coded)."""

    def __init__(self, namespace: str, source: Source, streams: List[Stream], vendor: str, version: str,
                 image_base_path: str, client: AmpliPiDataClient):
        super().__init__(client)
        self._streams: List[Stream] = streams
        self._source = source

        self._id = source.id
        self._domain = namespace
        self._image_base_path = image_base_path
        self._vendor = vendor
        self._version = version
        self._available = True

        self._data_client = client
        
        self._name = source.original_name
        self._attr_name = self._name
        self._unique_id = source.unique_id
        self.entity_id = source.entity_id
        self._attr_device_class = "source"

    async def async_toggle(self):
        if self._is_off:
            await self.async_turn_on()
        else:
            await self.async_turn_off()

    async def async_turn_on(self):
        # Unlike zones and groups, sources don't have anything within them on the amplipi side that says they're off
        # Flipping the value of _is_off only effects what "@property state" later on outputs
        _LOGGER.info(f"Turning source {self._name} on")
        self._is_off = False

    async def async_turn_off(self):
        if self._source is not None:
            _LOGGER.info(f"Turning source {self._name} off, disconnecting all zones and streams")
            await self._data_client.set_source(
                self._id,
                SourceUpdate(
                    input='None'
                )
            )
            await self._update_zones(
                MultiZoneUpdate(
                    zones=[z.id for z in self._zones],
                    groups=[z.id for z in self._groups],
                    update=ZoneUpdate(
                        source_id=-1,
                    )
                )
            )
            self._is_off = True

    async def async_mute_volume(self, mute):
        if mute is None:
            return

        if self._source is not None:
            _LOGGER.info(f"setting mute to {mute}")
            await self._update_zones(
                MultiZoneUpdate(
                    zones=[z.id for z in self._zones],
                    groups=[z.id for z in self._groups],
                    update=ZoneUpdate(
                        mute=mute,
                    )
                )
            )

    async def async_set_volume_level(self, volume):
        if volume is None:
            return
        _LOGGER.info(f"setting volume to {volume}")
        
        group = next(filter(lambda z: z.vol_f is not None, self._groups), None)
        zone = next(filter(lambda z: z.vol_f is not None, self._zones), None)
        if group is not None:
            group.vol_f = volume
        elif zone is not None:
            zone.vol_f = volume
        
        await self._update_zones(
            MultiZoneUpdate(
                zones=[z.id for z in self._zones],
                groups=[z.id for z in self._groups],
                update=ZoneUpdate(
                    vol_f=volume
                )
            )
        )

    async def async_browse_media(self, media_content_type=None, media_content_id=None):
        """Implement the websocket media browsing helper."""
        return await media_source.async_browse_media(
            self.hass,
            media_content_id,
            content_filter=lambda item: item.media_content_type.startswith("audio/"),
        )

    async def async_join_players(self, group_members):
        """Join `group_members` as a player group with the current player."""

    async def async_unjoin_player(self):
        """Remove this player from any group."""

    async def async_play_media(self, media_type, media_id, **kwargs):
        _LOGGER.debug(f'Play Media {media_type} {media_id} {kwargs}')

        if media_source.is_media_source_id(media_id):
            play_item = await media_source.async_resolve_media(self.hass, media_id)
            media_id = play_item.url
            _LOGGER.info(f'Playing media source: {play_item} {media_id}')

        media_id = async_process_play_media_url(self.hass, media_id)
        await self._data_client.play_media(
            PlayMedia(
                source_id=self._source.id,
                media=media_id
            )
        )
        pass

    async def async_select_source(self, source):
        if self._source is not None and self._source.name == source:
            await self._data_client.set_source(
                self._id,
                SourceUpdate(
                    input=f"stream={get_fixed_source_id(self._source)}"
                )
            )
        elif source == 'None':
            await self._data_client.set_source(
                self._id,
                SourceUpdate(
                    input='None'
                )
            )
        else:
            # Process both the input and the known name in case the entity_id is sent back for processing
            stream_hacs_entity = self.get_entry_by_value(source)
            stream_id = self.extract_amplipi_id_from_unique_id(stream_hacs_entity.unique_id)
            if stream_id is not None:
                await self._data_client.set_source(
                    self._id,
                    SourceUpdate(
                        input=f'stream={stream_id}'
                    )
                )
            else:
                _LOGGER.warning(f'Select Source {source} called but a match could not be found in the stream cache, '
                                f'{self._streams}')

    def clear_playlist(self):
        pass

    def set_shuffle(self, shuffle):
        pass

    def set_repeat(self, repeat):
        pass

    @property
    def media_content_type(self):
        """Content type of current playing media."""
        return MediaType.MUSIC

    def sync_state(self):
        """Retrieve latest state."""
        _LOGGER.info(f'Retrieving state for source {self._source.id}')
        state = self._data_client.data
        if state is not None:
            try:
                source = next(filter(lambda z: z.id == self._source.id, state.sources), None)
                streams = state.streams
            except Exception:
                self._last_update_successful = False
                _LOGGER.error(f'Could not update source {self._source.id}')
                return

            if not source:
                self._last_update_successful = False
                return

            self._source = source
            self._streams = streams
            self._stream = None

            if 'stream=' in source.input and 'stream=local' not in source.input and self._streams is not None:
                stream_id = int(self._source.input.split('=')[1])
                self._stream = next(filter(lambda s: s.id == stream_id, self._streams), None)

            self._zones = list(filter(lambda z: z.source_id == self._source.id, state.zones))
            self._groups = list(filter(lambda z: z.source_id == self._source.id, state.groups))
            self.get_song_info(self._source)
            self._last_update_successful = True

    @property
    def state(self):
        """Update local states and return the media player state of the source."""
        self.sync_state()
        
        if self._is_off and self._stream is None:
            return STATE_OFF
        elif self._last_update_successful is False:
            return STATE_UNKNOWN
        elif self._source.info is None or self._source.info.state is None or self._source.info.state == "disconnected":
            return STATE_IDLE
        elif self._source.info.state in (
                'paused'
        ):
            return STATE_PAUSED
        elif self._source.info.state in (
                'playing'
        ):
            return STATE_PLAYING
        elif self._source.info.state in (
                'stopped'
        ):
            return STATE_IDLE
        elif self._source.info.state in (
                'stopped'
        ):
            return STATE_IDLE

        return STATE_IDLE

    @property
    def volume_level(self):
        """Volume level of the media player (0..1)."""
        group = next(filter(lambda z: z.vol_f is not None, self._groups), None)
        zone = next(filter(lambda z: z.vol_f is not None, self._zones), None)
        if group is not None:
            return group.vol_f
        elif zone is not None:
            return zone.vol_f
        return None

    @property
    def is_volume_muted(self) -> bool:
        """Boolean if volume is currently muted."""
        group = next(filter(lambda z: z.mute is not None, self._groups), None)
        zone = next(filter(lambda z: z.mute is not None, self._zones), None)
        if group is not None:
            return group.mute
        elif zone is not None:
            return zone.mute
        return False

    @property
    def source(self):
        if self._source is not None:
            if self._source.input == 'local':
                return self._source.name
            elif self._stream is not None:
                stream = self.get_entry_by_value(self._stream.name)
                if stream:
                    return stream.friendly_name if stream.friendly_name not in [None, 'None'] else stream.original_name
        return 'None'

    @property
    def source_list(self):
        """List of available input sources."""
        return self.available_streams(self._source)

    async def _update_source(self, update: SourceUpdate):
        await self._data_client.set_source(self._source.id, update)

    async def _update_zones(self, update: MultiZoneUpdate):
        # zones = await self._data_client.get_zones()
        # associated_zones = filter(lambda z: z.source_id == self._source.id, zones)
        await self._data_client.set_zones(update)

    async def _update_groups(self, update: GroupUpdate):
        groups = await self._data_client.get_groups()
        associated_groups = filter(lambda g: g.source_id == self._source.id, groups)
        for group in associated_groups:
            await self._data_client.set_group(group.id, update)

    @property
    def extra_state_attributes(self):
        zone_list = []
        for zone in self._zones:
            zone_list.append(zone.id)
        return {
            "amplipi_source_id" : self._id,
            "amplipi_source_zones" : zone_list,
            "stream_connected": self._stream is not None
        }
