"""Support for interfacing with the AmpliPi Multizone home audio controller."""
# pylint: disable=W1203
import logging
import operator
import re
from functools import reduce
from typing import List, Optional, Union
from enum import Enum
from pydantic import BaseModel

import validators
from homeassistant.components import media_source, persistent_notification
from homeassistant.components.media_player import MediaPlayerDeviceClass, MediaPlayerEntity, MediaPlayerEntityFeature, MediaType
from homeassistant.components.media_player.browse_media import (
    async_process_play_media_url,
)
from homeassistant.const import CONF_NAME, STATE_PLAYING, STATE_PAUSED, STATE_IDLE, STATE_UNKNOWN, STATE_OFF
from homeassistant.helpers.entity import DeviceInfo
from pyamplipi.amplipi import AmpliPi
from pyamplipi.models import ZoneUpdate, Source, SourceUpdate, GroupUpdate, Stream, Group, Zone, Announcement, \
    MultiZoneUpdate, PlayMedia

from .const import (
    DOMAIN, AMPLIPI_OBJECT, CONF_VENDOR, CONF_VERSION, CONF_WEBAPP, )

SUPPORT_AMPLIPI_DAC = (
        MediaPlayerEntityFeature.SELECT_SOURCE
        | MediaPlayerEntityFeature.PLAY_MEDIA
        | MediaPlayerEntityFeature.VOLUME_MUTE
        | MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.GROUPING
        | MediaPlayerEntityFeature.VOLUME_STEP
        | MediaPlayerEntityFeature.BROWSE_MEDIA
        | MediaPlayerEntityFeature.TURN_OFF
)

DEFAULT_SUPPORTED_COMMANDS = ( # Used to forcibly support a shortlist of commands regardless of sub-type
        MediaPlayerEntityFeature.TURN_OFF
        | MediaPlayerEntityFeature.TURN_ON
        )

SUPPORT_AMPLIPI_ANNOUNCE = (
        MediaPlayerEntityFeature.PLAY_MEDIA
        | MediaPlayerEntityFeature.BROWSE_MEDIA
        | MediaPlayerEntityFeature.VOLUME_SET
)

SUPPORT_LOOKUP_DICT = {
    'play': MediaPlayerEntityFeature.PLAY,
    'pause': MediaPlayerEntityFeature.PAUSE,
    'stop': MediaPlayerEntityFeature.STOP,
    'next': MediaPlayerEntityFeature.NEXT_TRACK,
    'prev': MediaPlayerEntityFeature.PREVIOUS_TRACK,
}
_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1

async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the AmpliPi MultiZone Audio Controller"""
    shared_state: list[AmpliPiStateEntry] = []
    hass_entry = hass.data[DOMAIN][config_entry.entry_id]

    amplipi: AmpliPi = hass_entry[AMPLIPI_OBJECT]
    vendor = hass_entry[CONF_VENDOR]
    name = hass_entry[CONF_NAME]
    version = hass_entry[CONF_VERSION]
    image_base_path = f'{hass_entry[CONF_WEBAPP]}'

    status = await amplipi.get_status()
    sources: list[MediaPlayerEntity] = [
        AmpliPiSource(DOMAIN, source, status.streams, vendor, version, image_base_path, amplipi, shared_state)
        for source in status.sources]

    zones: list[MediaPlayerEntity] = [
        AmpliPiZone(DOMAIN, zone, None, status.streams, status.sources, vendor, version, image_base_path, amplipi, shared_state)
        for zone in status.zones]

    groups: list[MediaPlayerEntity] = [
        AmpliPiZone(DOMAIN, None, group, status.streams, status.sources, vendor, version, image_base_path, amplipi, shared_state)
        for group in status.groups]

    streams: list[MediaPlayerEntity] = [
        AmpliPiStream(DOMAIN, stream, status.sources, vendor, version, image_base_path, amplipi, shared_state)
        for stream in status.streams
    ]

    announcer: list[MediaPlayerEntity] = [
        AmpliPiAnnouncer(DOMAIN, vendor, version, image_base_path, amplipi)
    ]

    async_add_entities(sources + zones + groups + streams + announcer)


async def async_remove_entry(hass, entry) -> None:
    pass

class AmpliPiType(Enum):
    STREAM = "stream"
    SOURCE = "source"
    ZONE = "zone"
    GROUP = "group"


class AmpliPiStateEntry(BaseModel):
    original_name: str
    unique_id: str
    friendly_name: str
    entity_id: str
    amplipi_type: AmpliPiType

class AmpliPiMediaPlayer(MediaPlayerEntity):
    # The amplipi-side id
    _id: int

    # Home assistant side immutible id
    _unique_id: str

    # Lists of zones and groups related to the entity, either because the entity is a zone or a group or because they're connected to the entity
    _zones: list[Zone] = []
    _groups: List[Group] = []

    # List of all known streams
    _streams: List[Stream] = []

    # Home assistant particulars that are populated at entity instantiation via hass
    _vendor: str
    _version: str
    _client: AmpliPi
    _domain: str
    _image_base_path: str # Where the album art metadata is stored on home assistant
    _amplipi_type: AmpliPiType

    # Was the last polling cycle successful?
    _last_update_successful: bool = False

    # List of various arbitrary extra state attributes. Home assistant expects this list to exist, but it doesn't necessarily contain anything in most of our cases.
    _extra_attributes: List = []

    # The currently connected source or stream connected to the entity. If the entity is a source or stream, these are aliased forms of their local self._source or self._stream.
    _current_stream: Optional[Stream] = None
    _current_source: Optional[Source] = None

    # Does home assistant let you interact with the entity? False by default, made true if the entity is able to poll properly.
    _available: bool = False

    # Should the media player be set to STATE_OFF?
    _is_off: bool = False

    # The displayname of the entity. Also what is passed to async_select_source via dropdown menus.
    _name: str
    
    # A single variable shared by all instances of AmpliPiMediaPlayer as received by async_setup_entry. Used to share mappings of user assigned names and ids with the original default names and ids provided by this integration
    _shared_state: list[AmpliPiStateEntry]

    def update_shared_state_entry(self, original_name: str): # Cannot be invoked during __init__ of child classes as self.hass hasn't been instantiated until after __init__ completes
        """Look up self in hass.states and record relevant states to shared_states array"""
        state = self.hass.states.get(self.entity_id)
        if state:
            entry = AmpliPiStateEntry(
                original_name=original_name,
                unique_id=self._unique_id,
                friendly_name=state.attributes.get("friendly_name"),
                entity_id=self.entity_id,
                amplipi_type=self._amplipi_type
            )

            # Only update if there is new information
            if self.find_shared_entry_by_value(self._unique_id) != entry:
                self._shared_state[:] = [
                    e for e in self._shared_state if e.unique_id != self._unique_id
                ]

                self._shared_state.append(entry)

    def find_shared_entry_by_value(self, value: str) -> Union[AmpliPiStateEntry, None]:
        """Find what dict within the shared_states array has a given value and return said dict"""
        for entry in self._shared_state:
            if value in entry.model_dump().values():
                return entry
        return None
    
    def find_shared_entries_by_type(self, entry_type: AmpliPiType) -> Union[list[AmpliPiStateEntry], None]:
        """Return all entries of a given amplipi_type"""
        ret = []
        for entry in self._shared_state:
            if entry.amplipi_type == entry_type:
                ret.append(entry)
        return ret if len(ret) > 0 else None
            
    def extract_amplipi_id_from_unique_id(self, uid: str) -> Union[int, None]:
        """Extract all digits from a string and return them"""
        # Useful for getting amplipi-side ids out of entity ids due to the entity unique id being formatted as one "media_player.amplipi_{stream, group, zone, or source}_{amplipi-side id}"
        # Examples:
        # media_player.amplipi_stream_1000
        # media_player.amplipi_source_1
        match = re.search(r"\d+", uid)
        if match:
            return int(match.group())
        if uid != "amplipi_announcement":
            # amplipi_announcement is the only amplipi entity without numbers in its id
            # Filter against that before sending an error message so you don't print an error every few seconds whenever a source polls for the source list
            _LOGGER.error(f"extract_amplipi_id_from_unique_id could not determine entity ID: {uid}")
        return None

    def available_streams(self, source: Source):
        """Returns the available streams (generally all of them minus three of the four RCAs) relative to the provided source"""
        streams: List[str] = ['None']
        if self._streams is not None:
            # Excludes every RCA except for the one related to the given source
            RCAs = [996, 997, 998, 999]
            rca_selectable = RCAs[source.id]
            for entity in self._shared_state:
                if entity.amplipi_type == AmpliPiType.STREAM:
                    amplipi_id = self.extract_amplipi_id_from_unique_id(entity.unique_id)
                    if amplipi_id == rca_selectable or amplipi_id not in RCAs:
                        streams.append(entity.friendly_name if entity.friendly_name not in [None, 'None'] else entity.original_name)
        return streams
    
    async def async_connect_stream_to_source(self, stream: Stream, source: Optional[Source] = None):
        """Connects the stream to a source. If a source is not provided, searches for an available source."""
        _LOGGER.info(f"Stream {stream.name} attempting to connect to source {source}")
        source_id = None
        if stream.type == "rca":
            # RCAs are hardware constrained to only being able to use one specific source
            # If that source is busy, free it up without interrupting a users music
            if self._current_source is not None and source is not None and source.id != self._current_source.id:
                raise Exception("RCA streams can only connect to sources with the same ID")

            state = await self._client.get_status()
            source = state.sources[stream.id - 996]
            # It would be cleaner to do the following, but pyamplipi doesn't support RCA stream's index value atm:
            # source = state.sources[self._stream.index]
            if source.input not in [None, "None"]:
                await self.swap_source(source.id)

        if source is not None:
            source_id = source.id
        else:
            available_source = await self.find_source()
            if available_source:
               source_id = available_source.id
            else:
                persistent_notification.create(self.hass, f"Stream {stream.name} could not find an available source to connect to, all sources in use.\n\nPlease disconnect a source or provide one to override and try again.", f"{self._name} could not connect", f"{self._id}_connection_error")
                raise Exception("All sources are in use, disconnect a source or select one to override and try again.")
            
        if source_id is not None:
            await self._client.set_source(
                source_id,
                SourceUpdate(
                    input=f'stream={stream.id}'
                )
            )
            await self.async_update()
            return source_id
    
    async def async_connect_zones_to_source(self, source: Source, zones: Optional[List[int]], groups: Optional[List[int]]):
        """Connects zones and/or groups to the provided source"""
        if source is not None:
            await self._client.set_zones(
                MultiZoneUpdate(
                    zones=zones,
                    groups=groups,
                    update=ZoneUpdate(
                        source_id=source.id
                    )
                )
            )

    async def async_connect_zones_to_stream(self, stream: Stream, zones: Optional[List[int]], groups: Optional[List[int]]):
        """Connects zones and/or groups to the source of the selected stream. If stream does not have a source, select one"""
        state = await self._client.get_status()
        source_id = next((s.id for s in state.sources if s.input == f"stream={stream.id}"), None)
        if source_id is None:
            source_id = await self.async_connect_stream_to_source(stream)
        
        if source_id is not None:
            await self.async_connect_zones_to_source(state.sources[source_id], zones, groups)


    async def get_amplipi_entity(self, entity: str):
        """Take a name/id string, pull the full entry from shared state, and decode the entity's unique_id to find the related amplipi-side object"""
        entry = self.find_shared_entry_by_value(entity)
        if entry:
            state = await self._client.get_status()

            collection = None
            if entry.amplipi_type == AmpliPiType.SOURCE:
                collection = state.sources
            elif entry.amplipi_type == AmpliPiType.ZONE:
                collection = state.zones
            elif entry.amplipi_type == AmpliPiType.GROUP:
                collection = state.groups
            elif entry.amplipi_type == AmpliPiType.STREAM:
                collection = state.streams

            if collection:
                amplipi_id = self.extract_amplipi_id_from_unique_id(entry.unique_id)
                match = next((item for item in collection if item.id == amplipi_id), None)
                if match:
                    return match

    def build_url(self, img_url):
        """Returns the directory where album art metadata is kept"""
        if img_url is None:
            return None

        # if we have a full url, go ahead and return it
        if validators.url(img_url):
            return img_url

        # otherwise it might be a relative path.
        new_url = f'{self._image_base_path}/{img_url}'

        if validators.url(new_url):
            return new_url

        return None

    async def find_source(self) -> Source:
        """Find first available source and return it. If no sources are available, returns None."""
        sources = await self._client.get_sources()
        for source in sources:
            if source.input in ['', 'None', None]:
                return source
        return None
    
    async def swap_source(self, old_source: int, new_source: Optional[int] = None):
        """Moves a stream from one source to another, ensuring all zones follow. Generally only used for RCA streams, but able to be used by anyone."""
        state = await self._client.get_status()
        
        moved_stream: Stream = next(filter(lambda s: state.sources[old_source].input == f"stream={s.id}", state.streams), None)
        if moved_stream is not None and moved_stream.type != "rca":
            # RCA streams each have an associated source to output them due to hardware constraints
            if new_source is None:
                source = await self.find_source()
                if source:
                    new_source = source.id

            if new_source is not None:
                await self._client.set_source(
                    new_source,
                    SourceUpdate(
                        input=f'stream={moved_stream.id}'
                    )
                )

                moved_zones = [z.id for z in state.zones if z.source_id == old_source]
                await self._client.set_zones(
                    MultiZoneUpdate(
                        zones=moved_zones,
                        update=ZoneUpdate(
                            source_id=new_source
                        )
                    )
                )
                await self.async_update()

    async def async_update(self): # Meant to be overridden by child classes, only here so that the parent context can also use it inside of other functions
        """Retrieve latest state."""
        raise NotImplementedError("Subclasses should implement this method")
    
    async def async_volume_up(self):
        """Increases volume by 1%"""
        if hasattr(self, "volume_up"):
            await self.hass.async_add_executor_job(self.volume_up)
            return

        if self.volume_level is not None and self.volume_level < 1:
            await self.async_set_volume_level(min(1, self.volume_level + 0.01))

    async def async_volume_down(self):
        """Decreases volume by 1%"""
        if hasattr(self, "volume_down"):
            await self.hass.async_add_executor_job(self.volume_down)
            return

        if self.volume_level is not None and self.volume_level > 0:
            await self.async_set_volume_level(max(0, self.volume_level - 0.01))
       
    async def async_media_play(self):
        if self._current_stream is not None:
            await self._client.play_stream(self._current_stream.id)
        await self.async_update()

    async def async_media_stop(self):
        if self._current_stream is not None:
            await self._client.stop_stream(self._current_stream.id)
        await self.async_update()

    async def async_media_pause(self):
        if self._current_stream is not None:
            await self._client.pause_stream(self._current_stream.id)
        await self.async_update()

    async def async_media_previous_track(self):
        if self._current_stream is not None:
            await self._client.previous_stream(self._current_stream.id)
        await self.async_update()

    async def async_media_next_track(self):
        if self._current_stream is not None:
            await self._client.next_stream(self._current_stream.id)
        await self.async_update()

    @property
    def available(self):
        """Is the entity able to be used by the user? Should always return True so long as the entity is loaded."""
        return self._available
    
    @property
    def should_poll(self):
        """Polling needed."""
        return True
    
    @property
    def entity_registry_enabled_default(self):
        """Return if the entity should be enabled when first added to the entity registry."""
        return True

    @property
    def unique_id(self):
        """Return unique ID for this device."""
        return self._unique_id
    
    @property
    def name(self):
        """Return the name of the entity"""
        return "AmpliPi: " + self._name
    
    @property
    def source(self): # This is handled as a default as it's used by streams, groups, and zones but for sources this is overridden
        """Returns the current source playing, if this is wrong it won't show up as the selected source on HomeAssistant"""
        if self._current_source in [None, "None"]:
            return "None"
        return f'Source {self._current_source.id + 1}'
    


class AmpliPiSource(AmpliPiMediaPlayer):
    """Representation of an AmpliPi Source Input, of which 4 are supported (Hard Coded)."""

    def __init__(self, namespace: str, source: Source, streams: List[Stream], vendor: str, version: str,
                 image_base_path: str, client: AmpliPi, shared_state: list[AmpliPiStateEntry]):
        self._streams: List[Stream] = streams
        self._shared_state = shared_state
        self._amplipi_type = AmpliPiType.SOURCE
        self._source = source

        self._id = source.id
        self._domain = namespace
        self._image_base_path = image_base_path
        self._name = self.get_original_name()
        self._vendor = vendor
        self._version = version
        self._available = True

        # Aliased so that AmpliPiMediaPlayer functions know to use the same source while also using a variable name that doesn't imply that the source can change within a source entity
        self._current_source = self._source

        self._client = client
        self._unique_id = f"{namespace}_source_{self._source.id}"
        self.entity_id = f"media_player.{self._unique_id}"
        self._attr_device_class = MediaPlayerDeviceClass.RECEIVER

    def get_original_name(self):
        return f"Source {self._id + 1}"

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
            await self._client.set_source(
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
            await self.async_update()

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
        await self._client.play_media(
            PlayMedia(
                source_id=self._source.id,
                media=media_id
            )
        )
        pass

    async def async_select_source(self, source):
        if self._source is not None and self._source.name == source:
            await self._client.set_source(
                self._id,
                SourceUpdate(
                    input='local'
                )
            )
        elif source == 'None':
            await self._client.set_source(
                self._id,
                SourceUpdate(
                    input='None'
                )
            )
        else:
            # Process both the input and the known name in case the entity_id is sent back for processing
            stream_hacs_entity = self.find_shared_entry_by_value(source)
            stream_id = self.extract_amplipi_id_from_unique_id(stream_hacs_entity.unique_id)
            if stream_id is not None:
                await self._client.set_source(
                    self._id,
                    SourceUpdate(
                        input=f'stream={stream_id}'
                    )
                )
            else:
                _LOGGER.warning(f'Select Source {source} called but a match could not be found in the stream cache, '
                                f'{self._streams}')
        await self.async_update()

    def clear_playlist(self):
        pass

    def set_shuffle(self, shuffle):
        pass

    def set_repeat(self, repeat):
        pass

    @property
    def supported_features(self):
        """Return flag of media commands that are supported."""

        supported_features = SUPPORT_AMPLIPI_DAC
        if self._source is not None and self._source.info is not None and len(self._source.info.supported_cmds) > 0:
            supported_features = supported_features | reduce(
                operator.or_,
                [
                    SUPPORT_LOOKUP_DICT.get(key) for key
                    in (SUPPORT_LOOKUP_DICT.keys() & self._source.info.supported_cmds)
                ]
            )
        return supported_features | DEFAULT_SUPPORTED_COMMANDS

    @property
    def media_content_type(self):
        """Content type of current playing media."""
        return MediaType.MUSIC

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for this device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.unique_id)},
            model="AmpliPi MultiZone Source",
            name=self._name,
            manufacturer=self._vendor,
            sw_version=self._version,
            configuration_url=self._image_base_path,
        )

    # name: str | None
    # connections: set[tuple[str, str]]
    # identifiers: set[tuple[str, str]]
    # manufacturer: str | None
    # model: str | None
    # suggested_area: str | None
    # sw_version: str | None
    # via_device: tuple[str, str]
    # entry_type: str | None
    # default_name: str
    # default_manufacturer: str
    # default_model: str

    async def async_update(self):
        """Retrieve latest state."""
        _LOGGER.info(f'Retrieving state for source {self._source.id}')

        try:
            state = await self._client.get_status()
            source = next(filter(lambda z: z.id == self._source.id, state.sources), None)
            streams = state.streams
        except Exception:
            self._last_update_successful = False
            _LOGGER.error(f'Could not update source {self._source.id}')
            return

        if not source:
            self._last_update_successful = False
            return

        groups = list(filter(lambda z: z.source_id == self._source.id, state.groups))
        zones = list(filter(lambda z: z.source_id == self._source.id, state.zones))

        self.sync_state(source, streams, zones, groups)

    def sync_state(self, state: Source, streams: List[Stream], zones: List[Zone], groups: List[Group]):
        self._source = state

        self._streams = streams

        self._current_stream = None

        if 'stream=' in state.input and 'stream=local' not in state.input and self._streams is not None:
            stream_id = int(self._source.input.split('=')[1])
            self._current_stream = next(filter(lambda s: s.id == stream_id, self._streams), None)

        self._zones = zones
        self._groups = groups
        self.update_shared_state_entry(self.get_original_name())
        self._last_update_successful = True

        info = self._source.info

        if info is not None:
            track_name = info.track
            if track_name is None:
                track_name = info.name

            self._attr_media_album_artist = info.artist
            self._attr_media_album_name = info.album
            self._attr_media_title = track_name
            self._attr_media_track = info.track
            if self._current_stream is not None:
                self._attr_app_name = self._current_stream.type
            else:
                self._attr_app_name = None
            self._attr_media_image_url = self.build_url(info.img_url)
            self._attr_media_channel = info.station
        else:
            self._attr_media_album_artist = None
            self._attr_media_album_name = None
            self._attr_media_title = None
            self._attr_media_track = None
            self._attr_app_name = None
            self._attr_media_image_url = None
            self._attr_media_channel = None

    @property
    def state(self):
        """Return the state of the source."""
        if self._is_off and self._current_stream is None:
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
        # if self._source.vol_delta is None:
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
            elif self._current_stream is not None:
                stream = self.find_shared_entry_by_value(self._current_stream.name)
                if stream:
                    return stream.friendly_name if stream.friendly_name not in [None, 'None'] else stream.original_name
        return 'None'

    @property
    def source_list(self):
        """List of available input sources."""
        return self.available_streams(self._source)

    async def _update_source(self, update: SourceUpdate):
        await self._client.set_source(self._source.id, update)
        await self.async_update()

    async def _update_zones(self, update: MultiZoneUpdate):
        # zones = await self._client.get_zones()
        # associated_zones = filter(lambda z: z.source_id == self._source.id, zones)
        await self._client.set_zones(update)
        await self.async_update()

    async def _update_groups(self, update: GroupUpdate):
        groups = await self._client.get_groups()
        associated_groups = filter(lambda g: g.source_id == self._source.id, groups)
        for group in associated_groups:
            await self._client.set_group(group.id, update)
        await self.async_update()

    @property
    def extra_state_attributes(self):
        zone_list = []
        for zone in self._zones:
            zone_list.append(zone.id)
        return {"amplipi_source_id" : self._id,
                "amplipi_source_zones" : zone_list}

class AmpliPiZone(AmpliPiMediaPlayer):
    """Representation of an AmpliPi Zone and/or Group. Supports Audio volume
        and mute controls and the ability to change the current 'source' a
        zone is tied to"""

    def __init__(self, namespace: str, zone, group,
                 streams: List[Stream], sources: List[Source],
                 vendor: str, version: str, image_base_path: str,
                 client: AmpliPi, shared_state: list[AmpliPiStateEntry]):
        self._sources = sources
        self._split_group: bool = False
        self._domain = namespace
        self._shared_state = shared_state
        self._zone = zone
        self._group = group
        self._name = self.get_original_name()

        if group is not None:
            self._id = group.id
            self._unique_id = f"{namespace}_group_{self._id}"
            self._amplipi_type = AmpliPiType.GROUP
        else:
            self._id = zone.id
            self._unique_id = f"{namespace}_zone_{self._id}"
            self._amplipi_type = AmpliPiType.ZONE

        self.entity_id = f"media_player.{self._unique_id}"
        self._streams = streams
        self._image_base_path = image_base_path
        self._vendor = vendor
        self._version = version
        self._enabled = False
        self._client = client
        self._attr_source_list = [
            'None',
            'Source 1',
            'Source 2',
            'Source 3',
            'Source 4',
        ]
        self._attr_device_class = MediaPlayerDeviceClass.SPEAKER

    def get_original_name(self):
        return self._group.name if self._group else self._zone.name

    async def async_toggle(self):
        if self._is_off:
            await self.async_turn_on()
        else:
            await self.async_turn_off()

    async def async_turn_on(self):
        # update zone/group to a disconnected but not off state
        # this allows it to be configured from HA without forcing a specific connection 
        no_source_update = ZoneUpdate(source_id=-1)
        if self._group is not None:
            _LOGGER.info(f"Turning group {self._name} on")
            await self._update_group(
                MultiZoneUpdate(
                    groups=[self._group.id],
                    update=no_source_update
                )
            )
        else:
            _LOGGER.info(f"Turning zone {self._name} on")
            await self._update_zone(no_source_update)
        self._is_off = False

    async def async_turn_off(self):
        # update zone/group to have a disconnected source state that indicates to HA that the zone/group is off
        source_off_update = ZoneUpdate(source_id=-2)
        if self._group is not None:
            _LOGGER.info(f"Turning group {self._name} off")
            await self._update_group(
                MultiZoneUpdate(
                    groups=[self._group.id],
                    update=source_off_update
                )
            )
        else:
            _LOGGER.info(f"Turning zone {self._name} off")
            await self._update_zone(source_off_update)
        self._is_off = True

    async def async_mute_volume(self, mute):
        if mute is None:
            return
        _LOGGER.info(f"setting mute to {mute}")
        if self._group is not None:
            await self._update_group(
                MultiZoneUpdate(
                    groups=[self._group.id],
                    update=ZoneUpdate(
                        mute=mute,
                    )
                )
            )
        else:
            await self._update_zone(ZoneUpdate(
                mute=mute
            ))

    async def async_set_volume_level(self, volume):
        if volume is None:
            return
        
        if self._group is not None:
            self._group.vol_f = volume
        elif self._zone is not None:
            self._zone.vol_f = volume
    
        _LOGGER.info(f"setting volume to {volume}")
        if self._group is not None:
            await self._update_group(
                MultiZoneUpdate(
                    groups=[self._group.id],
                    update=ZoneUpdate(
                        vol_f=volume
                    )
                )
            )
        else:
            await self._update_zone(ZoneUpdate(
                vol_f=volume
            ))

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
        if self._group is not None:
            model = "AmpliPi Group"
        else:
            model = "AmpliPi Zone"

        via_device = None

        if self._current_source is not None:
            via_device = (DOMAIN, f"{DOMAIN}_source_{self._current_source.id}")

        return DeviceInfo(
            identifiers={(DOMAIN, self.unique_id)},
            model=model,
            name=self._name,
            manufacturer=self._vendor,
            sw_version=self._version,
            configuration_url=self._image_base_path,
            via_device=via_device,
        )

    async def async_update(self):
        """Retrieve latest state."""
        _LOGGER.info(f'Retrieving state for source {self._id}')

        zone = None
        group = None
        enabled = False

        try:
            state = await self._client.get_status()
            if self._group is not None:
                group: Group = next(filter(lambda z: z.id == self._id, state.groups), None)
                if not group:
                    self._last_update_successful = False
                    return
                any_enabled_zone = next(filter(lambda z: z.id in group.zones, state.zones), None)

                if any_enabled_zone is not None:
                    enabled = True
                
                connected_sources = [state.zones[zone_index].source_id for zone_index in group.zones]
                # Is every zone connected to the same source?
                self._split_group = len(set(connected_sources)) != 1
            else:
                zone = next(filter(lambda z: z.id == self._id, state.zones), None)
                if not zone:
                    self._last_update_successful = False
                    return
                enabled = not zone.disabled
        except Exception:
            self._last_update_successful = False
            _LOGGER.error(f'Could not update {"group" if self._group is not None else "zone"} {self._id}')
            return

        await self._get_extra_attributes()
        self._available = await self._update_available()
        self.sync_state(zone, group, state.streams, state.sources, enabled)
        self.sync_state(zone, group, state.streams, state.sources, enabled)

    def sync_state(self, zone: Zone, group: Group, streams: List[Stream],
                   sources: List[Source], enabled: bool):
        self._zone = zone
        self._group = group
        self._streams = streams
        self._sources = sources
        self._last_update_successful = True
        self._enabled = enabled
        self.update_shared_state_entry(self.get_original_name())

        info = None
        self._current_source = None

        # When a zone is off it connects to source_id -2, groups also yield the source_id that all requisite zones are already connected to
        if self._group is not None:
            self._current_source = next(filter(lambda s: self._group.source_id == s.id, sources), None)
            self._is_off = self._group.source_id == -2
                
        elif self._zone.source_id is not None:
            self._current_source = next(filter(lambda s: self._zone.source_id == s.id, sources), None)
            self._is_off = self._zone.source_id == -2

        if self._current_source is not None:
            info = self._current_source.info

        if self._current_source is not None and 'stream=' in self._current_source.input and 'stream=local' not in self._current_source.input:
            stream_id = int(self._current_source.input.split('=')[1])
            self._current_stream = next(filter(lambda z: z.id == stream_id, self._streams), None)

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
        """Return the state of the zone.""" 
        if self._is_off and self._current_source is None:
            return STATE_OFF
        elif self._last_update_successful is False or self._split_group:
            return STATE_UNKNOWN
        elif self._current_source is None or self._current_source == -1 or self._current_source.info is None or self._current_source.info.state is None:
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
        elif self._current_source.info.state in (
                'stopped'
        ):
            return STATE_IDLE

        return STATE_IDLE

    @property
    def volume_level(self):
        """Volume level of the media player (0..1)."""
        if self._group is not None:
            return self._group.vol_f
        elif self._zone is not None:
            return self._zone.vol_f
        return None

    @property
    def is_volume_muted(self) -> bool:
        """Boolean if volume is currently muted."""
        if self._group is not None:
            return self._group.mute
        else:
            return self._zone.mute
 
    async def async_select_source(self, source: str):
        # This is a home assistant MediaPlayer built-in function, so the source being passed in isn't the same as an amplipi source
        # the argument "source" can either be the name or entity_id of a stream or amplipi source, or the string "None" to signify being disconnected
        # As such, this info must be sorted and then sent down the proper logical path
        if source == "None":
            disconnect_update = ZoneUpdate(source_id=-1)
            if self._group is not None:
                await self._update_group(
                    MultiZoneUpdate(
                        groups=[self._group.id],
                        update=disconnect_update
                    )
                )
            else:
                await self._update_zone(disconnect_update)
        else:
            entity = await self.get_amplipi_entity(source)
            args = (entity, None, [self._id]) if self._group is not None else (entity, [self._id], None)
            if isinstance(entity, Stream):
                await self.async_connect_zones_to_stream(*args)
            elif isinstance(entity, Source):
                await self.async_connect_zones_to_source(*args)

    async def _update_zone(self, update: ZoneUpdate):
        await self._client.set_zone(self._id, update)
        await self.async_update()

    async def _update_group(self, update: MultiZoneUpdate):
        await self._client.set_zones(update)
        await self.async_update()

    @property
    def source_list(self):
        """List of available input sources."""
        return self._attr_source_list

    @property
    def source(self):
        """Returns the current source playing, if this is wrong it won't show up as the selected source on HomeAssistant"""
        if self._current_source in [None, "None"]:
            return "None"
        return f'Source {self._current_source.id + 1}'

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

        #No source, see if we can find an empty one
        if self._current_source is None:
            sources = await self._client.get_sources()
            for source in sources:
                if source is not None and source.input in ['', 'None', None]:
                    self._current_source = source
            
            if self._current_source is None:
                raise Exception("Not attached to a source and all sources are in use. Clear out a source or select an already existing one and try again.")
                

        media_id = async_process_play_media_url(self.hass, media_id)
        await self._client.play_media(
            PlayMedia(
                source_id=self._current_source.id,
                media=media_id,
            )
        )
        pass

    @property
    def extra_state_attributes(self):
        return self._extra_attributes

    async def _get_extra_attributes(self):
        if self._group is not None:
            state = await self._client.get_status()
            zone_ids = []

            for zone_id in self._group.zones:
                for state_zone in state.zones:
                    if state_zone.id == zone_id and not state_zone.disabled:
                        zone_ids.append(zone_id)
            self._extra_attributes = {"amplipi_zones" : zone_ids}

            #if self._zone_num_cache != len(zone_ids):
                #self.hass.bus.fire("group_change_event", {"group_change": True})
        else:
            self._extra_attributes = {"amplipi_zone_id" : self._zone.id}

    async def _update_available(self):
        state = await self._client.get_status()
        if self._group is not None:
            for zone_id in self._group.zones:
                for state_zone in state.zones:
                    if state_zone.id == zone_id and not state_zone.disabled:
                        return True
            return False
        elif self._zone is None or self._zone.disabled:
            return False
        return True

class AmpliPiAnnouncer(MediaPlayerEntity):
    # Doesn't need to extend AmpliPiMediaPlayer due to being far simpler than those components
    @property
    def should_poll(self):
        """Polling needed."""
        return True

    def __init__(self, namespace: str,
                 vendor: str, version: str, image_base_path: str,
                 client: AmpliPi):
        self._current_source = None

        self._unique_id = f"{namespace}_announcement"
        self.entity_id = f"media_player.{self._unique_id}"
        self._vendor = vendor
        self._version = version
        self._enabled = True
        self._client = client
        self._last_update_successful = True
        self._available = True
        self._extra_attributes: List = []
        self._image_base_path = image_base_path
        self._name = "AmpliPi Announcement"
        self._volume = 0.5
        self._attr_device_class = MediaPlayerDeviceClass.SPEAKER

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
    def device_info(self) -> DeviceInfo:
        """Return device info for this device."""
        model = "AmpliPi Announcement Channel"

        return DeviceInfo(
            identifiers={(DOMAIN, self.unique_id)},
            model=model,
            name=self._name,
            manufacturer=self._vendor,
            sw_version=self._version,
            configuration_url=self._image_base_path
        )

    @property
    def volume_level(self):
        return self._volume

    @property
    def unique_id(self):
        """Return unique ID for this device."""
        return self._unique_id

    @property
    def name(self):
        """Return the name of the zone."""
        return "AmpliPi: " + self._name

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
        await self._client.announce(
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


class AmpliPiStream(AmpliPiMediaPlayer):
    """Representation of an AmpliPi Stream. Supports Audio volume
        and mute controls and the ability to change the current 'source' a
        stream is tied to"""

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
            via_device = (DOMAIN, f"{DOMAIN}_source_{self._current_source.id}")

        return DeviceInfo(
            identifiers={(DOMAIN, self.unique_id)},
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
