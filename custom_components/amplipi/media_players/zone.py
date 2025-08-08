"""Support for interfacing with the AmpliPi Multizone home audio controller's audio outputs (Zones, Groups)."""
# pylint: disable=W1203
import logging
from typing import List

from homeassistant.components import media_source
from homeassistant.components.media_player import MediaPlayerDeviceClass
from homeassistant.components.media_player.browse_media import (
    async_process_play_media_url,
)
from homeassistant.const import STATE_PLAYING, STATE_PAUSED, STATE_IDLE, STATE_UNKNOWN, STATE_OFF
from homeassistant.helpers.entity import DeviceInfo
from pyamplipi.models import ZoneUpdate, MultiZoneUpdate, PlayMedia

from .media_player import AmpliPiMediaPlayer
from ..coordinator import AmpliPiDataClient
from ..const import DOMAIN
from ..models import Source, Group, Zone, Stream

_LOGGER = logging.getLogger(__name__)

class AmpliPiZone(AmpliPiMediaPlayer):
    """Representation of an AmpliPi Zone and/or Group. Supports Audio volume
        and mute controls and the ability to change the current 'source' a
        zone is tied to"""

    def __init__(self, namespace: str, zone: Zone, group: Group,
                 streams: List[Stream], sources: List[Source],
                 vendor: str, version: str, image_base_path: str,
                 client: AmpliPiDataClient):
        super().__init__(client)
        self._sources = sources
        self._split_group: bool = False
        self._domain = namespace
        self._zone = zone
        self._group = group

        if group is not None:
            self._id = group.id
            self._unique_id = group.unique_id
            self.entity_id = group.entity_id
            
        else:
            self._id = zone.id
            self._unique_id = zone.unique_id
            self.entity_id = zone.entity_id

        self.entity_id = f"media_player.{self._unique_id}"
        self._attr_name = None
        self._streams = streams
        self._image_base_path = image_base_path
        self._vendor = vendor
        self._version = version
        self._enabled = False
        self._data_client = client
        self._attr_source_list = [
            'None',
            'Source 1',
            'Source 2',
            'Source 3',
            'Source 4',
        ]
        self._attr_device_class = MediaPlayerDeviceClass.SPEAKER

    def get_original_name(self):
        """
            Stores the f-string of the default entity name schema\n
            For use when naming the entity during __init__ and when populating _amplipi_state.update_state_entry()
        """
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
            _LOGGER.info(f"Turning group {self.name} on")
            await self._update_group(
                MultiZoneUpdate(
                    groups=[self._group.id],
                    update=no_source_update
                )
            )
        else:
            _LOGGER.info(f"Turning zone {self.name} on")
            await self._update_zone(no_source_update)
        self._is_off = False

    async def async_turn_off(self):
        # update zone/group to have a disconnected source state that indicates to HA that the zone/group is off
        source_off_update = ZoneUpdate(source_id=-2)
        if self._group is not None:
            _LOGGER.info(f"Turning group {self.name} off")
            await self._update_group(
                MultiZoneUpdate(
                    groups=[self._group.id],
                    update=source_off_update
                )
            )
        else:
            _LOGGER.info(f"Turning zone {self.name} off")
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
    def media_content_type(self):
        """Content type of current playing media."""
        return "speaker"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for this device."""
        if self._group is not None:
            name = self._group.original_name
            model = "AmpliPi Group"
        else:
            name = self._zone.original_name
            model = "AmpliPi Zone"

        via_device = None

        if self._source is not None:
            via_device = (DOMAIN, f"{DOMAIN}_source_{self._source.id}")

        return DeviceInfo(
            identifiers={(DOMAIN, self.unique_id)},
            model=model,
            name=name,
            manufacturer=self._vendor,
            sw_version=self._version,
            configuration_url=self._image_base_path,
            via_device=via_device,
        )

    def sync_state(self):
        """Retrieve latest state."""
        _LOGGER.info(f'Retrieving state for source {self._id}')
        state = self._data_client.data
        if state is not None:
            zone = None
            group = None
            enabled = False

            try:
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


            if self._group is not None:
                for zone_id in self._group.zones:
                    for state_zone in state.zones:
                        if state_zone.id == zone_id and not state_zone.disabled:
                            self._available = True
                self._available = False
            elif self._zone is None or self._zone.disabled:
                self._available = False
            self._available = True

            self._zone = zone
            self._group = group
            self._streams = state.streams
            self._sources = state.sources
            self._last_update_successful = True
            self._enabled = enabled
            self._source = None

            # When a zone is off it connects to source_id -2, groups also yield the source_id that all requisite zones are already connected to
            if self._group is not None:
                self._source = next(filter(lambda s: self._group.source_id == s.id, state.sources), None)
                self._is_off = self._group.source_id == -2

            elif self._zone.source_id is not None:
                self._source = next(filter(lambda s: self._zone.source_id == s.id, state.sources), None)
                self._is_off = self._zone.source_id == -2

            if self._source is not None and 'stream=' in self._source.input and 'stream=local' not in self._source.input:
                stream_id = int(self._source.input.split('=')[1])
                self._stream = next(filter(lambda z: z.id == stream_id, self._streams), None)

            self.get_song_info(self._source)
            self._last_update_successful = True

    @property
    def state(self):
        """Update local states and return the media player state of the zone or group."""
        self.sync_state()
        
        if self._is_off and self._source is None:
            return STATE_OFF
        elif self._last_update_successful is False or self._split_group:
            return STATE_UNKNOWN
        elif self._source is None or self._source == -1 or self._source.info is None or self._source.info.state is None:
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
            entity = self.get_entry_by_value(source)
            args = (entity, None, [self._id]) if self._group is not None else (entity, [self._id], None)
            if isinstance(entity, Stream):
                await self.async_connect_zones_to_stream(*args)
            elif isinstance(entity, Source):
                await self.async_connect_zones_to_source(*args)

    async def _update_zone(self, update: ZoneUpdate):
        await self._data_client.set_zone(self._id, update)

    async def _update_group(self, update: MultiZoneUpdate):
        await self._data_client.set_zones(update)

    @property
    def source_list(self):
        """List of available input sources."""
        return self._attr_source_list

    @property
    def source(self):
        """Returns the current source playing, if this is wrong it won't show up as the selected source on HomeAssistant"""
        if self._source in [None, "None"]:
            return "None"
        return f'Source {self._source.id + 1}'

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

        # No source, see if we can find an empty one
        if self._source is None:
            sources = await self._data_client.get_sources()
            for source in sources:
                if source is not None and source.input in ['', 'None', None]:
                    self._source = source
            
            if self._source is None:
                raise Exception("Not attached to a source and all sources are in use. Clear out a source or select an already existing one and try again.")
                

        media_id = async_process_play_media_url(self.hass, media_id)
        await self._data_client.play_media(
            PlayMedia(
                source_id=self._source.id,
                media=media_id,
            )
        )
        pass

    @property
    def extra_state_attributes(self):
        # amplipi_zones and amplipi_zone_id are used by the group card of AmpliPi-HomeAssistant-Card to select related zone entities so they can be listed individually on the card
        # amplipi_zone_id is used in a similar way on the source cards as well
        if self._group is not None:
            return {
                "amplipi_zones": self._get_zone_ids(),
                "is_group": True,
                "stream_connected": self._stream is not None,
            }
        else:
            return {
                "stream_connected": self._stream is not None,
                "is_group": False,
                "amplipi_zone_id": self._zone.id,
            }

    def _get_zone_ids(self) -> List[int]:
        if self._group is not None:
            state = self._data_client.data
            zone_ids = []

            for zone_id in self._group.zones:
                for state_zone in state.zones:
                    if state_zone.id == zone_id and not state_zone.disabled:
                        zone_ids.append(zone_id)
            return zone_ids
        else:
            return self._zone.id

    async def _update_available(self):
        state = self._data_client.data
        if self._group is not None:
            for zone_id in self._group.zones:
                for state_zone in state.zones:
                    if state_zone.id == zone_id and not state_zone.disabled:
                        return True
            return False
        elif self._zone is None or self._zone.disabled:
            return False
        return True
