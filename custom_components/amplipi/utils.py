from typing import Union

from .models import Source, Stream


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
