"""A collection of helper tools and functions for the AmpliPi home assistant plugin"""
import logging
from pydantic import BaseModel
from enum import Enum

_LOGGER = logging.getLogger(__name__)

class AmpliPiType(Enum):
    STREAM = "stream"
    SOURCE = "source"
    ZONE = "zone"
    GROUP = "group"


class AmpliPiStateEntry(BaseModel):
    original_name: str
    unique_id: str
    friendly_name: str
    entity_id: str
    amplipi_type: AmpliPiType
