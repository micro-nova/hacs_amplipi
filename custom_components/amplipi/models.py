from typing import List
from pydantic import BaseModel
from pyamplipi.models import Source as PySource, Group as PyGroup, Zone as PyZone, Status as PyStatus, Stream as PyStream

class AmpliPiHAEntity(BaseModel):
    """Map an AmpliPi Object as a HA Entity"""
    original_name: str
    unique_id: str
    friendly_name: str
    entity_id: str

class Source(PySource, AmpliPiHAEntity):
    """An audio source\nAlso includes some encoding relating to HomeAssistant, including the original name and unique id of the related entity"""

class Stream(PyStream, AmpliPiHAEntity):
    """Digital stream such as Pandora, AirPlay or Spotify\nAlso includes some encoding relating to HomeAssistant, including the original name and unique id of the related entity"""

class Group(PyGroup, AmpliPiHAEntity):
    """A group of zones that can share the same audio input and be controlled as a group ie. Upstairs. Volume, mute,
    and source_id fields are aggregates of the member zones.\nAlso includes some encoding relating to HomeAssistant, including the original name and unique id of the related entity"""

class Zone(PyZone, AmpliPiHAEntity):
    """Audio output to a stereo pair of speakers, typically belonging to a room.\nAlso includes some encoding relating to HomeAssistant, including the original name and unique id of the related entity"""

class Status(PyStatus):
    """Collection of PyAmpliPi objects with the PyAmpliPiExtension mixin that allows calls to GET /api to have home assistant entity data encoded within"""
    sources: List[Source] = []
    zones: List[Zone] = []
    group: List[Group] = []
    