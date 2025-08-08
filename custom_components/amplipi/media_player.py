"""Support for interfacing with the AmpliPi Multizone home audio controller."""
# pylint: disable=W1203
from homeassistant.components.media_player import MediaPlayerEntity
from homeassistant.const import CONF_NAME

from .coordinator import AmpliPiDataClient
from .const import (
    DOMAIN, AMPLIPI_OBJECT, CONF_VENDOR, CONF_VERSION, CONF_WEBAPP, )

from .media_players.base import AmpliPiMediaPlayer
from .media_players.source import AmpliPiSource
from .media_players.stream import AmpliPiStream
from .media_players.zone import AmpliPiZone
from .media_players.announce import AmpliPiAnnouncer

async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the AmpliPi MultiZone Audio Controller"""
    hass_entry = hass.data[DOMAIN][config_entry.entry_id]

    amplipi_coordinator: AmpliPiDataClient = hass_entry[AMPLIPI_OBJECT]
    vendor = hass_entry[CONF_VENDOR]
    name = hass_entry[CONF_NAME]
    version = hass_entry[CONF_VERSION]
    image_base_path = f'{hass_entry[CONF_WEBAPP]}'

    status = amplipi_coordinator.data if amplipi_coordinator.data is not None else await amplipi_coordinator.get_status()
    sources: list[AmpliPiMediaPlayer] = [
        AmpliPiSource(DOMAIN, source, status.streams, vendor, version, image_base_path, amplipi_coordinator)
        for source in status.sources]

    zones: list[AmpliPiMediaPlayer] = [
        AmpliPiZone(DOMAIN, zone, None, status.streams, status.sources, vendor, version, image_base_path, amplipi_coordinator)
        for zone in status.zones]

    groups: list[AmpliPiMediaPlayer] = [
        AmpliPiZone(DOMAIN, None, group, status.streams, status.sources, vendor, version, image_base_path, amplipi_coordinator)
        for group in status.groups]
    
    streams: list[AmpliPiMediaPlayer] = [
        AmpliPiStream(DOMAIN, stream, status.sources, vendor, version, image_base_path, amplipi_coordinator)
        for stream in status.streams
    ]

    announcer: list[MediaPlayerEntity] = [
        AmpliPiAnnouncer(DOMAIN, vendor, version, image_base_path, amplipi_coordinator)
    ]

    async_add_entities(sources + zones + groups + streams + announcer)
