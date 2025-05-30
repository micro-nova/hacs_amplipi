"""Support for interfacing with the AmpliPi Multizone home audio controller."""
# Disable warnings relating to imported libraries not being made with all of our typecheckers in mind
# pylint: disable=E0401
# mypy: disable-error-code="import-untyped, import-not-found"

from homeassistant.components.media_player import MediaPlayerEntity
from homeassistant.const import CONF_NAME
from pyamplipi.amplipi import AmpliPi

from entities.AmpliPiAnnouncer import AmpliPiAnnouncer
from entities.AmpliPiSource import AmpliPiSource
from entities.AmpliPiStream import AmpliPiStream
from entities.AmpliPiZone import AmpliPiZone
from utils import AmpliPiStateEntry
from const import (
    DOMAIN, AMPLIPI_OBJECT, CONF_VENDOR, CONF_VERSION, CONF_WEBAPP, )

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
