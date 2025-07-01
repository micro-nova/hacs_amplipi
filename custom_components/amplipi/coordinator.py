"""
    AmpliPi API data coordinator
    Used to synchronize the current AmpliPi state with all of the corresponding HA Entities
"""
from datetime import timedelta
from typing import Optional, Union

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.entity_registry import async_get as async_get_entity_registry

from pyamplipi.amplipi import AmpliPi
from pyamplipi.models import SourceUpdate, ZoneUpdate, MultiZoneUpdate, GroupUpdate, PlayMedia, Announcement, Status as PyStatus, Source as PySource, Stream as PyStream, Group as PyGroup, Zone as PyZone, Status as PyStatus

from .models import Status, Source, Zone, Group, Stream
from .const import DOMAIN

class AmpliPiDataClient(DataUpdateCoordinator, AmpliPi):
    def __init__(self, hass, logger, config_entry, endpoint, timeout, http_session):
        super().__init__(
            hass,
            logger,
            config_entry=config_entry,
            name="hacs_amplipi",
            update_interval=timedelta(seconds=2),
            always_update=True
        )

        AmpliPi.__init__(
            self,
            endpoint=endpoint,
            timeout=timeout,
            http_session=http_session
        )

    async def get_friendly_name(self, entity_id):
        """Look up entity in hass.states and get the friendly name"""
        state = self.hass.states.get(entity_id)
        if state:
            return state.attributes.get("friendly_name")
        
    async def get_entity_id_from_unique_id(self, unique_id: str):
        """Gets entity_id from the entity registry using the unique_id"""
        registry = async_get_entity_registry(self.hass)
        for entry in registry.entities.values():
            if entry.unique_id == unique_id:
                return entry.entity_id
        return None
    
    async def _async_update_data(self) -> Status:
        """Fetch data from API endpoint and pre-process into lookup tables."""
        return await self.get_status()
        

    async def set_data(self, state: PyStatus) -> Status:
        """
        Take in a Status object from the AmpliPi API and add home assistant specific encoding to it before pushing it to global state.
        Returns the newly encoded Status object just so that _async_update_data has something to return as well.
        """
        async def build_entity(entity: Union[PySource, PyZone, PyGroup, PyStream], kind: str, cls, original_name: str):
            try:
                unique_id = f"{DOMAIN}_{kind}_{entity['id']}"
                entity_id = await self.get_entity_id_from_unique_id(unique_id) or f"media_player.{unique_id}"
                friendly_name = await self.get_friendly_name(entity_id) or original_name
                return cls(
                    **entity,
                    original_name=original_name,
                    unique_id=unique_id,
                    entity_id=entity_id,
                    friendly_name=friendly_name,
                )
            except TypeError as e:
                self.logger.error(f"Original name = {original_name}, entity = {entity}")
                raise TypeError(e) from e

        try:
            state["sources"] = [
                await build_entity(entity, "source", Source, f"Source {entity['id'] + 1}")
                for entity in state["sources"]
            ]

            state["zones"] = [
                await build_entity(entity, "zone", Zone, entity["name"])
                for entity in state["zones"]
            ]

            state["groups"] = [
                await build_entity(entity, "group", Group, entity["name"])
                for entity in state["groups"]
            ]

            state["streams"] = [
                await build_entity(entity, "stream", Stream, entity["name"])
                for entity in state["streams"]
            ]

            status = Status(**state)
            self.async_set_updated_data(status)
            return status

        except Exception as e:
            raise UpdateFailed(f"Error fetching data: {e}") from e
        
    async def get_status(self) -> Status:
        resp = await super().get_status()
        status = await self.set_data(resp.dict())
        return status

    async def set_source(self, source_id: int, source_update: SourceUpdate) -> Status:
        resp = await super().set_source(source_id, source_update)
        status = await self.set_data(resp.dict())
        return status
        
    async def set_zone(self, zone_id: int, zone_update: ZoneUpdate) -> Status:
        resp = await super().set_zone(zone_id, zone_update)
        status = await self.set_data(resp.dict())
        return status

    async def set_zones(self, zone_update: MultiZoneUpdate) -> Status:
        resp = await super().set_zones(zone_update)
        status = await self.set_data(resp.dict())
        return status
        
    async def play_media(self, media: PlayMedia) -> Status:
        resp = await super().play_media(media)
        status = await self.set_data(resp.dict())
        return status

    async def set_group(self, group_id, update: GroupUpdate) -> Status:
        resp = await super().set_group(group_id, update)
        status = await self.set_data(resp.dict())
        return status

    async def announce(self, announcement: Announcement, timeout: Optional[int] = None) -> Status:
        resp = await super().announce(announcement, timeout)
        status = await self.set_data(resp.dict())
        return status

    async def play_stream(self, stream_id: int) -> Status:
        resp = await super().play_stream(stream_id)
        status = await self.set_data(resp.dict())
        return status

    async def pause_stream(self, stream_id: int) -> Status:
        resp = await super().pause_stream(stream_id)
        status = await self.set_data(resp.dict())
        return status

    async def previous_stream(self, stream_id: int) -> Status:
        resp = await super().previous_stream(stream_id)
        status = await self.set_data(resp.dict())
        return status

    async def next_stream(self, stream_id: int) -> Status:
        resp = await super().previous_stream(stream_id)
        status = await self.set_data(resp.dict())
        return status

    async def stop_stream(self, stream_id: int) -> Status:
        resp = await super().stop_stream(stream_id)
        status = await self.set_data(resp.dict())
        return status
