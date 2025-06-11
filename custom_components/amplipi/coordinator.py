"""
    AmpliPi API data coordinator
    Used to synchronize the current AmpliPi state with all of the corresponding HA Entities
"""
from datetime import timedelta
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from .models import Status, Source, Zone, Group, Stream
from homeassistant.helpers.entity_registry import async_get as async_get_entity_registry
from .const import DOMAIN

class AmpliPiCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, logger, config_entry, api):
        super().__init__(
            hass,
            logger,
            config_entry=config_entry,
            name="hacs_amplipi",
            update_interval=timedelta(seconds=2),
            always_update=True
        )
        self.api = api

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

            return Status(**state)

        except Exception as e:
            raise UpdateFailed(f"Error fetching data: {e}")
