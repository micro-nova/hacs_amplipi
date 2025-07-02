"""
    AmpliPi API data coordinator
    Used to synchronize the current AmpliPi state with all of the corresponding HA Entities
"""
from datetime import timedelta
from typing import Optional, Union, Callable

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.entity_registry import async_get as async_get_entity_registry
from homeassistant.helpers import aiohttp_client
from typing import List
from pydantic import BaseModel
import async_timeout
import logging
from .models import Status, Source, Zone, Group, Stream

from pyamplipi.amplipi import AmpliPi
from pyamplipi.models import SourceUpdate, ZoneUpdate, MultiZoneUpdate, GroupUpdate, PlayMedia, Announcement, Status as PyStatus, Source as PySource, Stream as PyStream, Group as PyGroup, Zone as PyZone, Status as PyStatus

from .models import Status, Source, Zone, Group, Stream
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

class AmpliPiDataSchema(BaseModel):
    status: Status
    latest: dict
    releases: List[dict]

class AmpliPiDataClient(DataUpdateCoordinator, AmpliPi):
    def __init__(self, hass, config_entry, endpoint, timeout, http_session):
        super().__init__(
            hass,
            _LOGGER,
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

    async def fetch_github_releases(self, session) -> tuple[dict, List[dict]]:
        """Fetch latest and all releases from GitHub using aiohttp."""
        base_url = "https://api.github.com/repos/micro-nova/AmpliPi/releases"
        try:
            with async_timeout.timeout(10):
                async with session.get(f"{base_url}/latest") as latest_resp:
                    latest: dict = await latest_resp.json()
                async with session.get(base_url) as releases_resp:
                    releases: List[dict] = await releases_resp.json()
            return latest, releases
        except Exception as e:
            _LOGGER.warning(f"Failed to fetch GitHub releases: {e}")
            return {}, [{}]
    
    async def _async_update_data(self) -> AmpliPiDataSchema:
        """Fetch data from API endpoint and pre-process into lookup tables."""
        return await self.get_status()

    async def set_data(self, state: PyStatus) -> AmpliPiDataSchema:
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
            latest = self.latest
            releases = self.releases
            if self.latest is None or status.info.latest_release != self.latest.get("name"):
                session = aiohttp_client.async_get_clientsession(self.hass)
                latest, releases = await self.fetch_github_releases(session)
            
            data = AmpliPiDataSchema(status=status, latest=latest, releases=releases)
            self.async_set_updated_data(data)
            return data

        except Exception as e:
            raise UpdateFailed(f"Error fetching data: {e}") from e
        
    @property
    def status(self) -> Optional[Status]:
        return self.data.status if self.data is not None else None
    
    @property
    def latest(self) -> Optional[dict]:
        return self.data.latest if self.data is not None else None
    
    @property
    def releases(self) -> Optional[List[dict]]:
        return self.data.releases if self.data is not None else None
        
    def intecept_and_consume(func: Callable):
        """Intercept the return of a function and consume the data into the data coordinator"""
        async def wrapper(self, *args, **kwargs):
            resp = await func(self, *args, **kwargs)
            return await self.set_data(resp.dict())
        return wrapper

    @intecept_and_consume
    async def get_status(self) -> AmpliPiDataSchema:
        return await super().get_status()

    @intecept_and_consume
    async def set_source(self, source_id: int, source_update: SourceUpdate) -> AmpliPiDataSchema:
        return await super().set_source(source_id, source_update)
        
    @intecept_and_consume
    async def set_zone(self, zone_id: int, zone_update: ZoneUpdate) -> AmpliPiDataSchema:
        return await super().set_zone(zone_id, zone_update)

    @intecept_and_consume
    async def set_zones(self, zone_update: MultiZoneUpdate) -> AmpliPiDataSchema:
        return await super().set_zones(zone_update)
        
    @intecept_and_consume
    async def play_media(self, media: PlayMedia) -> AmpliPiDataSchema:
        return await super().play_media(media)

    @intecept_and_consume
    async def set_group(self, group_id, update: GroupUpdate) -> AmpliPiDataSchema:
        return await super().set_group(group_id, update)

    @intecept_and_consume
    async def announce(self, announcement: Announcement, timeout: Optional[int] = None) -> AmpliPiDataSchema:
        return await super().announce(announcement, timeout)

    @intecept_and_consume
    async def play_stream(self, stream_id: int) -> AmpliPiDataSchema:
        return await super().play_stream(stream_id)

    @intecept_and_consume
    async def pause_stream(self, stream_id: int) -> AmpliPiDataSchema:
        return await super().pause_stream(stream_id)

    @intecept_and_consume
    async def previous_stream(self, stream_id: int) -> AmpliPiDataSchema:
        return await super().previous_stream(stream_id)

    @intecept_and_consume
    async def next_stream(self, stream_id: int) -> AmpliPiDataSchema:
        return await super().previous_stream(stream_id)

    @intecept_and_consume
    async def stop_stream(self, stream_id: int) -> AmpliPiDataSchema:
        return await super().stop_stream(stream_id)
        