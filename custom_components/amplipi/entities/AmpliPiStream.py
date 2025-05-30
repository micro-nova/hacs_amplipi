# Disable warnings relating to imported libraries not being made with all of our typecheckers in mind
# pylint: disable=E0401
# mypy: disable-error-code="import-untyped, import-not-found"
import operator
from functools import reduce
from typing import List, Optional

from homeassistant.const import STATE_PLAYING, STATE_PAUSED, STATE_IDLE, STATE_UNKNOWN, STATE_OFF
from homeassistant.helpers.entity import DeviceInfo
from pyamplipi.amplipi import AmpliPi
from pyamplipi.models import ZoneUpdate, Source, SourceUpdate, Stream, Group, Zone, MultiZoneUpdate

from AmpliPiMediaPlayer import AmpliPiMediaPlayer
from utils import _LOGGER, AmpliPiStateEntry, AmpliPiType
from const import DEFAULT_SUPPORTED_COMMANDS, SUPPORT_LOOKUP_DICT, SUPPORT_AMPLIPI_DAC

class AmpliPiStream(AmpliPiMediaPlayer):
    """
        Representation of an AmpliPi Stream.
        Supports audio volume and mute controls and the ability
        to change the current 'source' a stream is tied to
    """

    def __init__(self, namespace: str, stream: Stream,
                 sources: List[Source],
                 vendor: str, version: str, image_base_path: str,
                 client: AmpliPi, shared_state: list[AmpliPiStateEntry]):
        self._stream: Stream = stream
        self._current_stream = self._stream # Make an alias for use with inherited functions while keeping local verbiage more correct
        self._current_source = None
        self._current_zones: List[Zone] = []
        self._current_groups: List[Group] = []
        self._sources = sources
        self._domain = namespace
        self._shared_state = shared_state
        self._amplipi_type = AmpliPiType.STREAM

        self._id = stream.id
        self._name = stream.name
        self._unique_id = f"{namespace}_stream_{stream.id}"
        self.entity_id = f"media_player.{self._unique_id}"
        
        self._image_base_path = image_base_path
        self._vendor = vendor
        self._version = version
        self._client = client
        # not a real device class, but allows us to match streams and only streams with the start_streaming blueprint's streams dropdown
        self._attr_device_class = "stream"
        self._attr_source_list = [
            'None',
            'Any',
            'Source 1',
            'Source 2',
            'Source 3',
            'Source 4',
        ] if self._stream.type != "rca" else ['None', f'Source {self._stream.id - 995}']
        self._available = False
        self._extra_attributes = []
        self._is_off: bool = True

    async def _update_zones(self, update: ZoneUpdate):
        if self._current_source is not None:
            multi_update = MultiZoneUpdate(
                groups=[g.id for g in self._current_groups],
                zones=[z.id for z in self._current_zones],
                update=update
            )
            await self._client.set_zones(multi_update)
            await self.async_update()

    async def async_toggle(self):
        if not self._is_off or self._current_source is not None:
            await self.async_turn_off()
        else:
            await self.async_turn_on()

    async def async_turn_on(self):
        if await self.find_source(): # Autoconnect stream if there is space
            await self.async_connect_stream_to_source(self._stream)
        elif self._stream.type == "rca": # Autoconnect stream if it can only ever connect to one source, regardless of space
            goal_source = next((s for s in self._sources if s.id == self._id - 996), None)
            if goal_source:
                await self.async_connect_stream_to_source(self._stream, goal_source)

        self._is_off = False

    async def async_turn_off(self):
        try:
            if self._current_source is not None:
                _LOGGER.info(f"Disconnecting stream from source {self._current_source.name}")
                await self._update_zones(
                    ZoneUpdate(
                        source_id=-1,
                    )
                )
                await self._client.set_source(
                    self._current_source.id,
                    SourceUpdate(
                        input='None'
                    )
                )
        except AttributeError:
            # There can be a race condition where self._current_source is not None and then becomes None by the time self._current_source.name or self._current_source.id is read
            # This happens if the user (or another entity/automation) disconnects the stream from the source while (or shortly before, due to the polling rate) this function runs
            # I'd rather suppress the error and ensure that the current state is as accurate as possible due to the error functionally being "Cannot disconnect due to already being disconnected"
            _LOGGER.debug(f"{self._name} had trouble disconnecting from a source")
        finally:
            self._is_off = True
            await self.async_update()

    async def async_mute_volume(self, mute):
        if mute is None:
            return

        if self._current_source is not None:
            _LOGGER.info(f"setting mute to {mute}")
            await self._update_zones(
                ZoneUpdate(
                    mute=mute,
                )
            )

    async def async_set_volume_level(self, volume):
        if volume is None:
            return
        if self._current_source is not None:
            await self._update_zones(
                ZoneUpdate(
                    vol_f=volume,
                    mute=False,
                )
            )

    @property
    def supported_features(self):
        """Return flag of media commands that are supported."""

        supported_features = SUPPORT_AMPLIPI_DAC
        if self._current_source is not None and self._current_source.info is not None and len(self._current_source.info.supported_cmds) > 0:
            supported_features = supported_features | reduce(
                operator.or_,
                [
                    SUPPORT_LOOKUP_DICT.get(key) for key
                    in (SUPPORT_LOOKUP_DICT.keys() & self._current_source.info.supported_cmds)
                ]
            )
        return supported_features | DEFAULT_SUPPORTED_COMMANDS

    @property
    def media_content_type(self):
        """Content type of current playing media."""
        return "speaker"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for this device."""
        via_device = None
        if self._current_source is not None:
            via_device = (self._domain, f"{self._domain}_source_{self._current_source.id}")

        return DeviceInfo(
            identifiers={(self._domain, self.unique_id)},
            model="AmpliPi Stream",
            name=self._name,
            manufacturer=self._vendor,
            sw_version=self._version,
            configuration_url=self._image_base_path,
            via_device=via_device,
        )

    async def async_update(self):
        """Retrieve latest state."""
        _LOGGER.info(f'Retrieving state for stream {self._id}')
        groups = []
        zones = []

        try:
            state = await self._client.get_status()
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

        await self._get_extra_attributes()
        self._available = await self._update_available()
        self.sync_state(stream, state.sources, current_source, zones, groups)


    def sync_state(self, stream: Stream, sources: List[Source], current_source, zones, groups):
        self._stream = stream
        self._sources = sources
        self._current_source = current_source
        if current_source: # Cannot be off while connected, but can be on while disconnected
            self._is_off = False
        self._last_update_successful = True
        self._current_zones = zones
        self._current_groups = groups
        self.update_shared_state_entry(self._stream.name)

        info = None

        if self._current_source is not None:
            info = self._current_source.info

        # Used to suppress warnings from the following if-else block relating to MyPy being unable to determine a type for all of these variables
        self._attr_media_album_artist: Optional[str]
        self._attr_media_album_name: Optional[str]
        self._attr_media_title: Optional[str]
        self._attr_media_track: Optional[str]
        self._attr_media_image_url: Optional[str]
        self._attr_media_channel: Optional[str]

        if info is not None:
            self._attr_media_album_artist = info.artist
            self._attr_media_album_name = info.album
            self._attr_media_title = info.name
            self._attr_media_track = info.track
            self._attr_media_image_url = self.build_url(info.img_url)
            self._attr_media_channel = info.station
        else:
            self._attr_media_album_artist = None
            self._attr_media_album_name = None
            self._attr_media_title = None
            self._attr_media_track = None
            self._attr_media_image_url = None
            self._attr_media_channel = None

    @property
    def state(self):
        """Return the state of the stream."""
        if self._is_off and self._current_source is None:
            return STATE_OFF
        elif self._last_update_successful is False:
            return STATE_UNKNOWN
        elif self._current_source is None or self._current_source.id == -1 or self._current_source.info is None or self._current_source.info.state is None:
            return STATE_IDLE
        elif self._current_source.info.state in (
                'paused'
        ):
            return STATE_PAUSED
        elif self._current_source.info.state in (
                'playing'
        ):
            return STATE_PLAYING
        elif self._current_source.info.state in (
                'stopped'
        ):
            return STATE_IDLE
        return STATE_IDLE


    @property
    def volume_level(self):
        """Volume level of the media player (0..1)."""
        if self._current_source is not None:
            if len(self._current_zones) > 0:
                vols = [zone.vol_f for zone in self._current_zones]
                return sum(vols) / len(vols)
        return None

    @property
    def is_volume_muted(self) -> bool:
        """Boolean if all connected zones are currently muted. If no zones connected, return muted."""
        if self._current_source is not None:
            for zone in self._current_zones:
                if not zone.mute:
                    return False
        return True
 
    async def async_select_source(self, source: Optional[str] = None):
        # This is a home assistant MediaPlayer built-in function, so the source being passed in isn't the same as an amplipi source
        # the argument "source" can either be the name or entity_id of a zone, group, or amplipi source
        # As such, this info must be sorted and then sent down the proper logical path
        if source:
            if source == "None" and self._current_source is not None: 
                await self._client.set_source(
                    self._current_source.id,
                    SourceUpdate(
                        input='None'
                    )
                )
            elif source == "Any" and self._current_source is None:
                await self.async_connect_stream_to_source(self._stream)
            else:
                amplipi_entity = await self.get_amplipi_entity(source)
                if isinstance(amplipi_entity, Source):
                    await self.async_connect_stream_to_source(self._stream, amplipi_entity)
                else:
                    entry = self.find_shared_entry_by_value(source)
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
        return self._extra_attributes

    async def _get_extra_attributes(self):
        if self._current_source is not None:
            self._extra_attributes = {"amplipi_source_id" : self._current_source.id }
        else:
            self._extra_attributes = {"amplipi_source_id" : None }

    async def _update_available(self):
        if self._stream is None:
            return False
        return True
