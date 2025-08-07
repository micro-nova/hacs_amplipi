"""Constants for the AmpliPi integration."""
from homeassistant.components.media_player import MediaPlayerEntityFeature

DOMAIN = "amplipi"
CONF_VENDOR = "vendor"
CONF_VERSION = "version"
AMPLIPI_OBJECT = "amplipi_object"
CONF_WEBAPP = "webapp"
CONF_API_PATH = "api_path"


SUPPORT_AMPLIPI_ANNOUNCE = (
        MediaPlayerEntityFeature.PLAY_MEDIA
        | MediaPlayerEntityFeature.BROWSE_MEDIA
        | MediaPlayerEntityFeature.VOLUME_SET
)

SUPPORT_AMPLIPI_DAC = (
        MediaPlayerEntityFeature.SELECT_SOURCE
        | MediaPlayerEntityFeature.PLAY_MEDIA
        | MediaPlayerEntityFeature.VOLUME_MUTE
        | MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.GROUPING
        | MediaPlayerEntityFeature.VOLUME_STEP
        | MediaPlayerEntityFeature.BROWSE_MEDIA
)

DEFAULT_SUPPORTED_COMMANDS = ( # Used to forcibly support a shortlist of commands regardless of sub-type
        MediaPlayerEntityFeature.TURN_OFF
        | MediaPlayerEntityFeature.TURN_ON
        )

SUPPORT_LOOKUP_DICT = {
    'play': MediaPlayerEntityFeature.PLAY,
    'pause': MediaPlayerEntityFeature.PAUSE,
    'stop': MediaPlayerEntityFeature.STOP,
    'next': MediaPlayerEntityFeature.NEXT_TRACK,
    'prev': MediaPlayerEntityFeature.PREVIOUS_TRACK,
}