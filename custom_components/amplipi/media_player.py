"""Support for interfacing with the AmpliPi Multizone home audio controller."""
import logging
from typing import List

import validators
from homeassistant.components.cover import SUPPORT_STOP
from homeassistant.components.media_player import MediaPlayerEntity, SUPPORT_VOLUME_MUTE, \
    SUPPORT_VOLUME_SET, SUPPORT_SELECT_SOURCE, SUPPORT_PLAY_MEDIA, SUPPORT_PLAY
from homeassistant.components.media_player.const import SUPPORT_PAUSE, SUPPORT_NEXT_TRACK, MEDIA_TYPE_MUSIC, \
    SUPPORT_PREVIOUS_TRACK, SUPPORT_TURN_ON, SUPPORT_TURN_OFF
from homeassistant.const import CONF_NAME, STATE_OFF, STATE_PLAYING, STATE_PAUSED, STATE_IDLE, STATE_UNKNOWN
from homeassistant.helpers.entity import DeviceInfo
from pyamplipi.amplipi import AmpliPi
from pyamplipi.models import ZoneUpdate, Source, SourceUpdate, GroupUpdate, Stream, Group, Zone, Announcement, \
    MultiZoneUpdate

from .const import (
    DOMAIN, AMPLIPI_OBJECT, CONF_VENDOR, CONF_VERSION, CONF_WEBAPP, )

SUPPORT_AMPLIPI_DAC = (
        SUPPORT_SELECT_SOURCE
        | SUPPORT_PLAY_MEDIA
        | SUPPORT_VOLUME_MUTE
        | SUPPORT_VOLUME_SET
    # | SUPPORT_VOLUME_STEP
)

SUPPORT_AMPLIPI_MEDIA = (
        SUPPORT_AMPLIPI_DAC
        | SUPPORT_STOP
        | SUPPORT_PLAY
        | SUPPORT_PAUSE
        | SUPPORT_PREVIOUS_TRACK
        | SUPPORT_NEXT_TRACK
)

SUPPORT_AMPLIPI_ZONE = (
        SUPPORT_SELECT_SOURCE
        | SUPPORT_VOLUME_MUTE
        | SUPPORT_VOLUME_SET
        | SUPPORT_TURN_ON
        | SUPPORT_TURN_OFF
)

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1

DB_MAX = -80
DB_MIN = 0


def build_url(api_base_path, img_url):
    if img_url is None:
        return None

    # if we have a full url, go ahead and return it
    if validators.url(img_url):
        return img_url

    # otherwise it might be a relative path.
    new_url = f'{api_base_path}/{img_url}'

    if validators.url(new_url):
        return new_url

    return None


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
        AmpliPiZone(DOMAIN, zone, None, status.streams, sources, vendor, version, image_base_path, amplipi)
        for zone in status.zones]

    groups: list[MediaPlayerEntity] = [
        AmpliPiZone(DOMAIN, None, group, status.streams, sources, vendor, version, image_base_path, amplipi)
        for group in status.groups]

    async_add_entities(sources + zones)


async def async_remove_entry(hass, entry) -> None:
    pass


def db_to_pct(decibels: float) -> float:
    _LOGGER.debug(f'using decibel {decibels} with result {1 - (decibels - DB_MIN) / (DB_MAX - DB_MIN)}')
    return 1 - (decibels - DB_MIN) / (DB_MAX - DB_MIN)


def pct_to_db(percentage: float) -> float:
    _LOGGER.debug(f'using percentage {percentage} with result {DB_MAX - ((DB_MAX - DB_MIN) * percentage)}')
    return DB_MAX - ((DB_MAX - DB_MIN) * percentage)


class AmpliPiSource(MediaPlayerEntity):
    """Representation of an AmpliPi Source Input, of which 4 are supported (Hard Coded)."""

    @property
    def should_poll(self):
        """Polling needed."""
        return True

    def __init__(self, namespace: str, source: Source, streams: List[Stream], vendor: str, version: str,
                 image_base_path: str, client: AmpliPi):
        self._streams = streams
        self._id = source.id
        self._current_stream = None
        self._image_base_path = image_base_path
        self._zones = []
        self._groups = []
        self._name = f"Source {self._id + 1}"
        self._vendor = vendor
        self._version = version
        self._source = source
        self._client = client
        self._unique_id = f"{namespace}_source_{source.id}"
        self._last_update_successful = False

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
        await self._update_zones(
            MultiZoneUpdate(
                zones=[z.id for z in self._zones],
                groups=[z.id for z in self._groups],
                update=ZoneUpdate(
                    vol=round(pct_to_db(volume))
                )
            )
        )

    async def async_media_play(self):
        await self._client.play_stream(self._current_stream.id)
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

    async def async_play_media(self, media_type, media_id, **kwargs):
        _LOGGER.warning(f'Play Media {media_type} {media_id} {kwargs}')

        if media_type is MEDIA_TYPE_MUSIC:
            _LOGGER.warning(f'This might be a TTS announcement..')
            await self._client.announce(
                Announcement(
                    source_id=self._source.id,
                    media=media_id,
                    vol=round(pct_to_db(.5)),
                )
            )

        pass

    async def async_select_source(self, source):

        if self._source is not None and self._source.name == source:
            await self._update_source(SourceUpdate(
                input=f'local'
            ))
        else:
            stream = next(filter(lambda z: z.name == source, self._streams), None)
            if stream is None:
                _LOGGER.warning(f'Select Source {source} called but a match could not be found in the stream cache, '
                                f'{self._streams}')
                pass
            else:
                await self._update_source(SourceUpdate(
                    input=f'stream={stream.id}'
                ))

    def clear_playlist(self):
        pass

    def set_shuffle(self, shuffle):
        pass

    def set_repeat(self, repeat):
        pass

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

    @property
    def supported_features(self):
        """Return flag of media commands that are supported."""

        if self._current_stream is not None:
            # stream_id = int(self._source.input.split('=')[1])
            # stream = next(filter(lambda z: z.id == stream_id, self._streams), None)

            if self._current_stream is not None and self._current_stream.type in (
                    'spotify',
                    'pandora'
            ):
                return SUPPORT_AMPLIPI_MEDIA

        return SUPPORT_AMPLIPI_DAC

    @property
    def media_content_type(self):
        """Content type of current playing media."""
        return "speaker"

    @property
    def entity_registry_enabled_default(self):
        """Return if the entity should be enabled when first added to the entity registry."""
        return True

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

    @property
    def unique_id(self):
        """Return unique ID for this device."""
        return self._unique_id

    @property
    def name(self):
        """Return the name of the zone."""
        return self._name

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
        if 'stream=' in state.input and 'stream=local' not in state.input:
            stream_id = int(self._source.input.split('=')[1])
            self._current_stream = next(filter(lambda z: z.id == stream_id, self._streams), None)

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
            self._attr_media_image_url = build_url(self._image_base_path, info.img_url)
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
        """Return the state of the zone."""
        if self._last_update_successful is False:
            return STATE_UNKNOWN
        elif self._source is None:
            return STATE_OFF
        elif self._source.info is None or self._source.info.state is None:
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
        group = next(filter(lambda z: z.vol_delta is not None, self._groups), None)
        zone = next(filter(lambda z: z.vol is not None, self._zones), None)
        if group is not None:
            return db_to_pct(group.vol_delta)
        elif zone is not None:
            return db_to_pct(zone.vol)
        return STATE_UNKNOWN

        # return db_to_pct(self._source.vol_delta)

    @property
    def is_volume_muted(self) -> bool:
        """Boolean if volume is currently muted."""
        if self._source.mute is None:
            group = next(filter(lambda z: z.mute is not None, self._groups), None)
            zone = next(filter(lambda z: z.mute is not None, self._zones), None)
            if group is not None:
                return group.mute
            elif zone is not None:
                return zone.mute
            return STATE_UNKNOWN

        return self._source.mute

    @property
    def source_list(self):
        """List of available input sources."""
        streams = [stream.name for stream in self._streams]
        if self._source is not None:
            streams += [self._source.name]
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


class AmpliPiZone(MediaPlayerEntity):
    """Representation of an AmpliPi Zone and/or Group. Supports Audio volume
        and mute controls and the ability to change the current 'source' a
        zone is tied to"""

    @property
    def should_poll(self):
        """Polling needed."""
        return True

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

    async def async_turn_off(self):
        if self._is_group:
            await self._update_group(
                MultiZoneUpdate(
                    groups=[self._group.id],
                    update=ZoneUpdate(
                        disabled=True,
                    )
                )
            )
        else:
            await self._update_zone(
                ZoneUpdate(
                    disabled=True,
                )
            )

    def __init__(self, namespace: str, zone, group,
                 streams: List[Stream], sources: List[MediaPlayerEntity],
                 vendor: str, version: str, image_base_path: str,
                 client: AmpliPi):
        self._sources = sources
        self._is_group = group is not None

        if self._is_group:
            self._id = group.id
            self._name = group.name
            self._unique_id = f"{namespace}_group_{self._id}"
        else:
            self._id = zone.id
            self._name = zone.name
            self._unique_id = f"{namespace}_zone_{self._id}"

        self._streams = streams
        self._image_base_path = image_base_path
        self._vendor = vendor
        self._version = version
        self._zone = zone
        self._group = group
        self._enabled = False
        self._client = client
        self._last_update_successful = False

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
        _LOGGER.info(f"setting volume to {volume}")
        if self._is_group:
            await self._update_group(
                MultiZoneUpdate(
                    groups=[self._group.id],
                    update=ZoneUpdate(
                        vol=pct_to_db(volume)
                    )
                )
            )
        else:
            await self._update_zone(ZoneUpdate(
                vol=pct_to_db(volume)
            ))

    async def async_select_source(self, source):
        pass

    @property
    def supported_features(self):
        """Return flag of media commands that are supported."""
        return SUPPORT_AMPLIPI_ZONE

    @property
    def media_content_type(self):
        """Content type of current playing media."""
        return "speaker"

    @property
    def entity_registry_enabled_default(self):
        """Return if the entity should be enabled when first added to the entity registry."""
        return False

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for this device."""
        if self._is_group:
            model = "AmpliPi Group"
        else:
            model = "AmpliPi Zone"
        return DeviceInfo(
            identifiers={(DOMAIN, self.unique_id)},
            model=model,
            name=self._name,
            manufacturer=self._vendor,
            sw_version=self._version,
            configuration_url=self._image_base_path,
        )

    @property
    def unique_id(self):
        """Return unique ID for this device."""
        return self._unique_id

    @property
    def name(self):
        """Return the name of the zone."""
        return self._name

    async def async_update(self):
        """Retrieve latest state."""
        _LOGGER.info(f'Retrieving state for source {self._id}')

        zone = None
        group = None
        enabled = False

        try:
            state = await self._client.get_status()
            if self._is_group:
                group = next(filter(lambda z: z.id == self._id, state.groups), None)
                if not group:
                    self._last_update_successful = False
                    return
                any_enabled_zone = next(filter(lambda z: z.id in group.zones, state.zones), None)

                if any_enabled_zone is not None:
                    enabled = True
            else:
                zone = next(filter(lambda z: z.id == self._id, state.zones), None)
                if not zone:
                    self._last_update_successful = False
                    return
                enabled = not zone.disabled
            streams = state.streams
        except Exception:
            self._last_update_successful = False
            _LOGGER.error(f'Could not update source {self._id}')
            return

        self.sync_state(zone, group, streams, state.sources, enabled)

    def sync_state(self, zone: Zone, group: Group, streams: List[Stream],
                   sources: List[Source], enabled: bool):
        self._zone = zone
        self._group = group
        self._streams = streams
        self._sources = sources
        self._last_update_successful = True
        self._enabled = enabled

        info = None
        source = None

        if self._is_group:
            source = next(filter(lambda s: self._group.source_id == s.id, sources), None)
        elif self._zone.source_id is not None:
            source = next(filter(lambda s: self._zone.source_id == s.id, sources), None)

        if source is not None:
            info = source.info

        if info is not None:
            self._attr_media_album_artist = info.artist
            self._attr_media_album_name = info.album
            self._attr_media_title = info.name
            self._attr_media_track = info.track
            self._attr_media_image_url = build_url(self._image_base_path, info.img_url)
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
        if self._last_update_successful is False:
            return STATE_UNKNOWN
        elif self._enabled is True:
            return STATE_IDLE

        return STATE_OFF

    @property
    def volume_level(self):
        """Volume level of the media player (0..1)."""
        return db_to_pct(self._zone.vol)

    @property
    def is_volume_muted(self) -> bool:
        """Boolean if volume is currently muted."""
        if self._is_group:
            return self._group.mute
        else:
            return self._zone.mute

    @property
    def source_list(self):
        """List of available input sources."""
        return []

    async def _update_zone(self, update: ZoneUpdate):
        await self._client.set_zone(self._id, update)
        await self.async_update()

    async def _update_group(self, update: MultiZoneUpdate):
        await self._client.set_zones(update)
        await self.async_update()
