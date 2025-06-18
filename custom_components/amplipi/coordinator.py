"""
    AmpliPi API data coordinator
    Used to synchronize the current AmpliPi state with all of the corresponding HA Entities
"""
from datetime import timedelta
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.entity_registry import async_get as async_get_entity_registry
from homeassistant.helpers import aiohttp_client
from typing import List
from pydantic import BaseModel
import async_timeout
import logging
from .models import Status, Source, Zone, Group, Stream
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

class AmpliPiDataSchema(BaseModel):
    status: Status
    latest: dict
    releases: List[dict]

class AmpliPiCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, config_entry, api):
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name="hacs_amplipi",
            update_interval=timedelta(seconds=2),
            always_update=True
        )
        self.api = api
        self.shown_notifications: List = []
        self.latest: dict = {
            "name": "default_name_that_isn't_a_version",
            "body": "You have not polled github. Please contact support@micro-nova.com if this issue persists.",
            "html_url": "https://github.com/micro-nova/AmpliPi/releases/tag/0.4.6",
        }
        self.releases: List[dict] = [{}]

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

        async def build_entity(entity, kind: str, cls, original_name: str):
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

        try:
            state = await self.api.get_status()
            state = state.dict()

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
            if status.info.latest_release != self.latest["name"]:
                session = aiohttp_client.async_get_clientsession(self.hass)
                self.latest, self.releases = await self.fetch_github_releases(session)

            return AmpliPiDataSchema(status=status, latest=self.latest, releases=self.releases)

        except Exception as e:
            raise Exception(f"Error fetching data: {e}")
        