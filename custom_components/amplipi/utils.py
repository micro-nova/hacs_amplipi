import logging
import re
from typing import Union, Optional

from .models import Source, Stream

_LOGGER = logging.getLogger(__name__)

def has_fixed_source(stream: Stream) -> bool:
    """Is the stream an RCA, and therefore does it have access to only one source"""
    return stream.type == "rca"

def get_fixed_source_id(entity: Union[Stream, Source]):
    """Get the id of the source associated with an RCA"""
    if isinstance(entity, Stream):
        if has_fixed_source(entity):
            return entity.id - 996
    elif isinstance(entity, Source):
        return entity.id + 996

def extract_amplipi_id_from_unique_id(uid: str) -> Optional[int]:
    """
        Extracts all digits from a string and returns them\n
        Useful for getting amplipi-side ids out of entity unique_ids due to the unique_id being formatted as "media_player.amplipi_{stream, group, zone, or source}_{amplipi-side id}"\n
        Examples:\n
        media_player.amplipi_stream_1000\n
        media_player.amplipi_source_0
    """
    match = re.search(r"\d+", uid)
    if match:
        return int(match.group())
    if uid != "amplipi_announcement":
        # amplipi_announcement is the only amplipi entity without numbers in its id
        # Filter against that before sending an error message so you don't print an error every few seconds whenever a source polls for the source list
        _LOGGER.error(f"extract_amplipi_id_from_unique_id could not determine entity ID: {uid}")
    return None
