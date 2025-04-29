"""Support for interfacing with the AmpliPi Multizone home audio controller."""
# pylint: disable=W1203
import logging
import operator
import re
from functools import reduce
from typing import List, Optional, Union

import validators
from homeassistant.components import media_source
from homeassistant.components.media_player import MediaPlayerDeviceClass, MediaPlayerEntity, MediaPlayerEntityFeature, MediaType
from homeassistant.components.media_player.browse_media import (
    async_process_play_media_url,
)
from homeassistant.const import CONF_NAME, STATE_PLAYING, STATE_PAUSED, STATE_IDLE, STATE_UNKNOWN
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
    hass_entry = hass.data[DOMAIN][config_entry.entry_id]

    amplipi: AmpliPi = hass_entry[AMPLIPI_OBJECT]
    vendor = hass_entry[CONF_VENDOR]
    name = hass_entry[CONF_NAME]
    version = hass_entry[CONF_VERSION]
    image_base_path = f'{hass_entry[CONF_WEBAPP]}'

    status = await amplipi.get_status()

    sources: list[MediaPlayerEntity] = [
        AmpliPiSource(DOMAIN, source, status.streams, vendor, version, image_base_path, amplipi)
        for source in status.sources]

    zones: list[MediaPlayerEntity] = [
        AmpliPiZone(DOMAIN, zone, None, status.streams, status.sources, vendor, version, image_base_path, amplipi)
        for zone in status.zones]

    groups: list[MediaPlayerEntity] = [
        AmpliPiZone(DOMAIN, None, group, status.streams, status.sources, vendor, version, image_base_path, amplipi)
        for group in status.groups]

    announcer: list[MediaPlayerEntity] = [
        AmpliPiAnnouncer(DOMAIN, vendor, version, image_base_path, amplipi)
    ]

    async_add_entities(sources + zones + groups + announcer)


async def async_remove_entry(hass, entry) -> None:
    pass


class AmpliPiMediaPlayer(MediaPlayerEntity):
    _zones: list[Zone] = []
    _groups: List[Group] = []
    _vendor: str
    _version: str
    _client: AmpliPi
    _last_update_successful: bool = False
    _image_base_path: str
    _extra_attributes: List = []
    _current_stream: Stream
    _current_source: Source
    _available: bool = True
    _name: str
    _unique_id: str

    async def get_entity(self, entity: str):
        """Compare a name/id string and compare against polled state to return the referenced entity"""
        # removes "media_player.amplipi_{word}_" from the given string and then replace the underscores with spaces,
        # functionally converting the entity IDs to the names while leaving names unnaffected
        def decode_name(s: str) -> str:
            return re.sub(r"media_player\.amplipi_[^_]+_", "", s).replace("_", " ").lower()
        
        state = await self._client.get_status()

        decoded_entity_name = decode_name(entity)
        # In hacs_amplipi sources are named source 1-4, in amplipi (and the polled state) they're named output 1-4
        # Match a regex prior to str.replace() just in case the user has a stream name containing the word source
        if re.match(r"source [\d]", decoded_entity_name):
            decoded_entity_name = decoded_entity_name.replace("source", "output")

        for collection in (state.sources, state.zones, state.groups):
            match = next((item for item in collection if decode_name(item.name) == decoded_entity_name), None)
            if match:
                return match

        stream_id = await self.extract_stream_id(entity)
        return next((s for s in state.streams if stream_id == s.id), None)

    def encode_stream_names(self, streams: Union[Stream, List[Stream]]) -> Union[Stream, List[Stream]]:
        """Processes one or more stream names to include 'AmpliPi Stream {stream.id}: {stream.name}'"""
        def encode_name(stream: Stream):
            # Need to check if name was already processed so you don't get stuff like:
            # "AmpliPi 996: AmpliPi 996: AmpliPi 996: Input 1" due to back population
            if f"AmpliPi {stream.id}:" not in stream.name:
                stream.name = f"AmpliPi {stream.id}: {stream.name}"
            return stream
        
        if isinstance(streams, List):
            for stream in streams:
                stream = encode_name(stream)
            return streams
        else:
            return encode_name(streams)

    async def extract_stream_id(self, stream: str):
        """Pulls the stream.id out of a stream entity's name or entity_id"""
        # Example stream entity id: media_player.amplipi_1000_groove_salad
        # Example stream name: Amplipi 1000: Groove Salad
        match = re.search(r"\d+", stream) # Find first uninterrupted substring of digits
        if match:
            output = match.group()
            if int(output) > 995: # all stream ids are above 995
                return output
        _LOGGER.error("extract_stream_id could not determine stream ID")

    def extract_source_id_from_name(self, source: str):
        return int(''.join(re.findall(r'\d', source))[0]) - 1

    def build_url(self, img_url):
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
        if hasattr(self, "volume_up"):
            await self.hass.async_add_executor_job(self.volume_up)
            return

        if self.volume_level is not None and self.volume_level < 1:
            await self.async_set_volume_level(min(1, self.volume_level + 0.01))

    async def async_volume_down(self):
        if hasattr(self, "volume_down"):
            await self.hass.async_add_executor_job(self.volume_down)
            return

        if self.volume_level is not None and self.volume_level > 0:
            await self.async_set_volume_level(max(0, self.volume_level - 0.01))
       
    async def async_media_play(self):
        await self._client.play_stream(self._current_stream.id)
        await self.async_update()

    async def async_media_stop(self):
        await self._client.stop_stream(self._current_stream.id)
        await self.async_update()

    async def async_media_pause(self):
        await self._client.pause_stream(self._current_stream.id)
        await self.async_update()

    async def async_media_previous_track(self):
        await self._client.previous_stream(self._current_stream.id)
        await self.async_update()

    async def async_media_next_track(self):
        await self._client.next_stream(self._current_stream.id)
        await self.async_update()

    @property
    def available(self):
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
        """Return the name of the zone."""
        return "AmpliPi: " + self._name


class AmpliPiSource(AmpliPiMediaPlayer):
    """Representation of an AmpliPi Source Input, of which 4 are supported (Hard Coded)."""

    def __init__(self, namespace: str, source: Source, streams: List[Stream], vendor: str, version: str,
                 image_base_path: str, client: AmpliPi):
        self._streams: List[Stream] = self.encode_stream_names(streams)

        self._id = source.id
        self._current_stream = None
        self._image_base_path = image_base_path
        self._zones = []
        self._groups = []
        self._name = f"Source {self._id + 1}"
        self._vendor = vendor
        self._version = version
        self._source = source
        self._current_source = self._source
        self._client = client
        self._unique_id = f"{namespace}_source_{source.id}"
        self._last_update_successful = False
        self._attr_device_class = MediaPlayerDeviceClass.RECEIVER
        self._is_off = False

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
            await self.async_update()

    async def async_mute_volume(self, mute):
        if mute is None:
            return

        if self._source is not None:
            _LOGGER.warning(f"setting mute to {mute}")
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
        _LOGGER.warning(f"setting volume to {volume}")
        
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

    async def async_media_play(self):
        await self._client.play_stream(self._current_stream.id)
        await self.async_update()

    async def async_media_stop(self):
        await self._client.stop_stream(self._current_stream.id)
        await self.async_update()

    async def async_media_pause(self):
        await self._client.pause_stream(self._current_stream.id)
        await self.async_update()

    async def async_media_previous_track(self):
        await self._client.previous_stream(self._current_stream.id)
        await self.async_update()

    async def async_media_next_track(self):
        await self._client.next_stream(self._current_stream.id)
        await self.async_update()

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
            await self.async_update()
        elif source == 'None':
            await self._client.set_source(
                self._id,
                SourceUpdate(
                    input='None'
                )
            )
            await self.async_update()
        else:
            # Process both the input and the known name in case the entity_id is sent back for processing
            stream_id = await self.extract_stream_id(source)
            if stream_id is not None:
                await self._client.set_source(
                    self._id,
                    SourceUpdate(
                        input=f'stream={stream_id}'
                    )
                )
                await self.async_update()
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
        return supported_features

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

        self._streams = self.encode_stream_names(streams)

        self._current_stream = None

        if 'stream=' in state.input and 'stream=local' not in state.input and self._streams is not None:
            stream_id = int(self._source.input.split('=')[1])
            self._current_stream = next(filter(lambda s: s.id == stream_id, self._streams), None)

        self._zones = zones
        self._groups = groups
        self._last_update_successful = True
        self._name = state.name

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
                return self._current_stream.name
        return 'None'

    @property
    def source_list(self):
        """List of available input sources."""
        streams = ['None']
        if self._streams is not None:
            streams += [stream.name for stream in self._streams if stream.id >= 1000 or stream.id - 996 == self._id]
        return streams

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

    async def async_turn_on(self):
        if self._is_group:
            await self._update_group(
                MultiZoneUpdate(
                    groups=[self._group.id],
                    update=ZoneUpdate(
                        disabled=False,
                    )
                )
            )
        else:
            await self._update_zone(
                ZoneUpdate(
                    disabled=False,
                )
            )
        #self.is_on = True

    def __init__(self, namespace: str, zone, group,
                 streams: List[Stream], sources: List[Source],
                 vendor: str, version: str, image_base_path: str,
                 client: AmpliPi):
        self._current_source = None
        self._sources = sources
        self._split_group: bool = False

        if group is not None:
            self._is_group = True
            self._id = group.id
            self._name = group.name
            self._unique_id = f"{namespace}_group_{self._id}"
        else:
            self._is_group = False
            self._id = zone.id
            self._name = zone.name
            self._unique_id = f"{namespace}_zone_{self._id}"

        self._streams = self.encode_stream_names(streams)
        self._image_base_path = image_base_path
        self._vendor = vendor
        self._version = version
        self._zone = zone
        self._group = group
        self._enabled = False
        self._client = client
        self._attr_source_list = [
            'None',
            'Source 1',
            'Source 2',
            'Source 3',
            'Source 4',
        ]
        self._available = False
        self._extra_attributes = []
        self._attr_device_class = MediaPlayerDeviceClass.SPEAKER
        self._current_stream = None
        self._is_off = False

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
        if self._group is None:
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
        if self._is_group:
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
        
        if self._is_group and self._group is not None:
            self._group.vol_f = volume
        elif self._zone is not None:
            self._zone.vol_f = volume
    
        _LOGGER.info(f"setting volume to {volume}")
        if self._is_group:
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
        return supported_features

    @property
    def media_content_type(self):
        """Content type of current playing media."""
        return "speaker"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for this device."""
        if self._is_group:
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
            if self._is_group:
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
        self._streams = self.encode_stream_names(streams)
        self._sources = sources
        self._last_update_successful = True
        self._enabled = enabled

        info = None
        self._current_source = None

        # When a zone is off it connects to source_id -2, groups also yield the source_id that all requisite zones are already connected to
        if self._is_group:
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
        if self._is_group and self._group is not None:
            return self._group.vol_f
        elif self._zone is not None:
            return self._zone.vol_f
        return None

    @property
    def is_volume_muted(self) -> bool:
        """Boolean if volume is currently muted."""
        if self._is_group:
            return self._group.mute
        else:
            return self._zone.mute

    async def async_select_source(self, source):
        # source can either be the string "None", the name of a source, or the source's entity ID depending on if this function was called from an automation or directly from the dropdown
        # If not "None", it will contain the source's ID number in it. use a regex to only return digits and then only use the first one in case we ever allow users to name their sources
        if source == 'None':
            source_id = -1
        else:
            source_id = self.extract_source_id_from_name(source)
        if source_id is not None:
            if self._is_group:
                await self._update_group(
                    MultiZoneUpdate(
                        groups=[self._group.id],
                        update=ZoneUpdate(
                            source_id=source_id
                        )
                    )
                )
            else:
                await self._update_zone(
                    ZoneUpdate(
                        source_id=source_id
                    )
                )
            await self.async_update()

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
        if self._is_group:
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
        if self._is_group:
            for zone_id in self._group.zones:
                for state_zone in state.zones:
                    if state_zone.id == zone_id and not state_zone.disabled:
                        return True
            return False
        elif self._zone is None or self._zone.disabled:
            return False
        return True

    async def async_media_play(self):
        await self._client.play_stream(self._current_stream.id)
        await self.async_update()

    async def async_media_stop(self):
        await self._client.stop_stream(self._current_stream.id)
        await self.async_update()

    async def async_media_pause(self):
        await self._client.pause_stream(self._current_stream.id)
        await self.async_update()

    async def async_media_previous_track(self):
        await self._client.previous_stream(self._current_stream.id)
        await self.async_update()

    async def async_media_next_track(self):
        await self._client.next_stream(self._current_stream.id)
        await self.async_update()

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
        self._vendor = vendor
        self._version = version
        self._enabled = True
        self._client = client
        self._last_update_successful = True
        self._available = True
        self._extra_attributes = []
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
