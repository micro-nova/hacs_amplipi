"""Support for interfacing with the AmpliPi Multizone home audio controller's audio inputs (Known as Streams)."""
# pylint: disable=W1203
import logging
from typing import List, Optional

from homeassistant.const import STATE_PLAYING, STATE_PAUSED, STATE_IDLE, STATE_UNKNOWN, STATE_OFF
from pyamplipi.models import ZoneUpdate, SourceUpdate, MultiZoneUpdate

from .AmpliPiMediaPlayer import AmpliPiMediaPlayer
from ..coordinator import AmpliPiDataClient
from ..models import Source, Group, Zone, Stream
from ..utils import get_fixed_source_id, has_fixed_source

_LOGGER = logging.getLogger(__name__)

class AmpliPiStream(AmpliPiMediaPlayer):
    """Representation of an AmpliPi Stream. Supports Audio volume
        and mute controls and the ability to change the current 'source' a
        stream is tied to"""

    def __init__(self, namespace: str, stream: Stream,
                 sources: List[Source],
                 vendor: str, version: str, image_base_path: str,
                 client: AmpliPiDataClient):
        super().__init__(client)
        self._stream: Stream = stream
        self._source = None
        self._zones: List[Zone] = []
        self._groups: List[Group] = []
        self._sources = sources
        self._domain = namespace

        self._id = stream.id
        self._name = stream.name
        self._attr_name = self._name
        self._unique_id = stream.unique_id
        self.entity_id = stream.entity_id
        # not a real device class, but allows us to match streams and only streams with the start_streaming blueprint's streams dropdown
        # note that device class doesn't actually define an entity as a device and is just a specific header to filter by
        self._attr_device_class = "stream"
        
        self._image_base_path = image_base_path
        self._vendor = vendor
        self._version = version
        self._data_client = client
        self._attr_source_list = [
            'None',
            'Any',
            'Source 1',
            'Source 2',
            'Source 3',
            'Source 4',
        ] if self._stream.type != "rca" else ['None', f'Source {get_fixed_source_id(self._stream) + 1}'] # +1 to the source id to provide the proper text for the source
        self._available = False
        self._is_off: bool = True

    async def _update_zones(self, update: ZoneUpdate):
        if self._source is not None:
            multi_update = MultiZoneUpdate(
                groups=[g.id for g in self._groups],
                zones=[z.id for z in self._zones],
                update=update
            )
            await self._data_client.set_zones(multi_update)
            

    async def async_toggle(self):
        if not self._is_off or self._source is not None:
            await self.async_turn_off()
        else:
            await self.async_turn_on()

    async def async_turn_on(self):
        if await self.find_source(): # Autoconnect stream if there is space
            await self.async_connect_stream_to_source(self._stream)
        elif has_fixed_source(self._stream): # Autoconnect stream if it can only ever connect to one source, regardless of space
            goal_source = next((s for s in self._sources if s.id == get_fixed_source_id(self._stream)), None)
            if goal_source:
                await self.async_connect_stream_to_source(self._stream, goal_source)

        self._is_off = False

    async def async_turn_off(self):
        try:
            if self._source is not None:
                _LOGGER.info(f"Disconnecting stream from source {self._source.name}")
                await self._update_zones(
                    ZoneUpdate(
                        source_id=-1,
                    )
                )
                await self._data_client.set_source(
                    self._source.id,
                    SourceUpdate(
                        input='None'
                    )
                )
        except AttributeError:
            # There can be a race condition where self._source is not None and then becomes None by the time self._source.name or self._source.id is read
            # This happens if the user (or another entity/automation) disconnects the stream from the source while (or shortly before, due to the polling rate) this function runs
            # I'd rather suppress the error and ensure that the current state is as accurate as possible due to the error functionally being "Cannot disconnect due to already being disconnected"
            _LOGGER.debug(f"{self._name} had trouble disconnecting from a source")
        finally:
            self._is_off = True
            

    async def async_mute_volume(self, mute):
        if mute is None:
            return

        if self._source is not None:
            _LOGGER.info(f"setting mute to {mute}")
            await self._update_zones(
                ZoneUpdate(
                    mute=mute,
                )
            )

    async def async_set_volume_level(self, volume):
        if volume is None:
            return
        if self._source is not None:
            await self._update_zones(
                ZoneUpdate(
                    vol_f=volume,
                    mute=False,
                )
            )

    @property
    def media_content_type(self):
        """Content type of current playing media."""
        return "speaker"

    def sync_state(self):
        """Retrieve latest state."""
        _LOGGER.info(f'Retrieving state for stream {self._id}')
        state = self._data_client.data
        if state is not None:
            groups = []
            zones = []

            try:
                stream = next(filter(lambda s: s.id == self._id, state.streams), None)
                if stream is not None:
                    current_source = next((s for s in state.sources if s.input == f"stream={stream.id}"), None)
                    if current_source is not None:
                        groups = [group for group in state.groups if group.source_id == current_source.id]
                        zones = [zone for zone in state.zones if zone.source_id == current_source.id]
                else:
                    self._last_update_successful = False
                    return
            except Exception as e:
                self._last_update_successful = False
                _LOGGER.error(f'Could not update stream {self._id} due to error: {e}')
                return

            self._available = self._stream is not None

            self._stream = stream
            self._sources = state.sources
            self._source = current_source
            if current_source: # Cannot be off while connected, but can be on while disconnected
                self._is_off = False
            self._zones = zones
            self._groups = groups
            self.get_song_info(self._source)
            self._last_update_successful = True

    @property
    def state(self):
        """Update local states and return the media player state of the stream."""
        self.sync_state()

        if self._is_off and self._source is None:
            return STATE_OFF
        elif self._last_update_successful is False:
            return STATE_UNKNOWN
        elif self._source is None or self._source.id == -1 or self._source.info is None or self._source.info.state is None:
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
        return STATE_IDLE


    @property
    def volume_level(self):
        """Volume level of the media player (0..1)."""
        if self._source is not None:
            if len(self._zones) > 0:
                vols = [zone.vol_f for zone in self._zones]
                return sum(vols) / len(vols)
        return None

    @property
    def is_volume_muted(self) -> bool:
        """Boolean if all connected zones are currently muted. If no zones connected, return muted."""
        if self._source is not None:
            for zone in self._zones:
                if not zone.mute:
                    return False
        return True
 
    async def async_select_source(self, source: Optional[str] = None):
        # This is a home assistant MediaPlayer built-in function, so the source being passed in isn't the same as an amplipi source
        # the argument "source" can either be the name or entity_id of a zone, group, or amplipi source
        # As such, this info must be sorted and then sent down the proper logical path
        if source:
            if source == "None" and self._source is not None:
                await self._data_client.set_source(
                    self._source.id,
                    SourceUpdate(
                        input='None'
                    )
                )
            elif source == "Any" and self._source is None:
                await self.async_connect_stream_to_source(self._stream)
            else:
                amplipi_entity = self.get_entry_by_value(source)
                if isinstance(amplipi_entity, Source):
                    await self.async_connect_stream_to_source(self._stream, amplipi_entity)
                else:
                    entry = self.get_entry_by_value(source)
                    if entry:
                        entry_id = self.extract_amplipi_id_from_unique_id(entry.unique_id)
                        if isinstance(amplipi_entity, Zone):
                            await self.async_connect_zones_to_stream(self._stream, [entry_id], None)
                        elif isinstance(amplipi_entity, Group):
                            await self.async_connect_zones_to_stream(self._stream, None, [entry_id])

    @property
    def source_list(self):
        """List of available input sources."""
        return self._attr_source_list

    @property
    def extra_state_attributes(self):
        return {"stream_type" : self._stream.type}

    async def _update_available(self):
        if self._stream is None:
            return False
        return True
