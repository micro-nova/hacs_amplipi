# Disable warnings relating to imported libraries not being made with all of our typecheckers in mind
# pylint: disable=E0401
# mypy: disable-error-code="import-untyped, import-not-found"
import re
import validators
from typing import List, Optional, Union

from homeassistant.components import persistent_notification
from homeassistant.components.media_player import MediaPlayerEntity

from pyamplipi.amplipi import AmpliPi
from pyamplipi.models import ZoneUpdate, Source, SourceUpdate, Stream, Group, Zone, MultiZoneUpdate

from utils import AmpliPiType, AmpliPiStateEntry, _LOGGER

class AmpliPiMediaPlayer(MediaPlayerEntity):
    """Parent class for AmpliPi MediaPlayer entities. Enforces common variables and provides common functions"""
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
    