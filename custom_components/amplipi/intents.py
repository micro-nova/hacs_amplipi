from homeassistant.helpers import intent
from homeassistant.core import HomeAssistant
from homeassistant.const import ATTR_ENTITY_ID
from typing import List

class IntentBase(intent.IntentHandler):
    """Parent class to custom home assistant voice commands"""
    intent_type: str

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        """Handle the intent."""
        raise NotImplementedError()
    
    # Slot name rules:
    # - 'source', 'stream', 'zone', 'group': return entities of the given type
    # - 'not_source', 'not_stream', 'not_zone', 'not_group': return all entities except for the given type
    # - 'speakers': return zones and groups
    # - 'all': return all entities
    def async_get_slot_list(self, hass: HomeAssistant, slot_name: str):
        """Dynamically generate slot values based on available entities"""
        def merge_lists(keys: List[str]) -> List[dict]:
            """Merge lists from the entities dict based on the received keys"""
            ret: List = []
            for key in keys:
                ret.extend(entities.get(key, []))
            return ret
        
            
        entities = {
            "streams": [
                {"value": state.name,"id": state.entity_id}
                for state in hass.states.async_all("media_player")
                if state.entity_id.startswith("media_player.amplipi_stream")
            ],
            "sources": [
                {"value": state.name,"id": state.entity_id}
                for state in hass.states.async_all("media_player")
                if state.entity_id.startswith("media_player.amplipi_source")
            ],
            "zones": [
                {"value": state.name,"id": state.entity_id}
                for state in hass.states.async_all("media_player")
                if state.entity_id.startswith("media_player.amplipi_zone")
            ],
            "groups": [
                {"value": state.name,"id": state.entity_id}
                for state in hass.states.async_all("media_player")
                if state.entity_id.startswith("media_player.amplipi_group")
            ],
        }

        if slot_name == "speakers":
            return merge_lists(["groups", "zones"])
        
        if slot_name == "all":
            return merge_lists(["streams", "sources", "groups", "zones"])
        
        if slot_name.startswith("not"):
            options = ["streams", "sources", "groups", "zones"]
            split = slot_name.split("_")
            options.remove(f"{split[1]}s")
            return merge_lists(options)

        return entities[f"{slot_name}s"]
    

class SourceSelect(IntentBase):
    """Verbally connect a source to a zone, group, or stream"""
    intent_type = "SourceSelect"

    async def async_handle(self, intent_obj):
        hass = intent_obj.hass
        source = intent_obj.slots["source"]["id"]
        entity_id = intent_obj.slots["not_source"]["id"]

        await hass.services.async_call(
            "media_player",
            "select_source",
            {
                ATTR_ENTITY_ID: entity_id,
                "source": source,
            },
            blocking=True,
            context=intent_obj.context,
        )

        return intent_obj.create_response(f"Set source of {entity_id} to {source}")
