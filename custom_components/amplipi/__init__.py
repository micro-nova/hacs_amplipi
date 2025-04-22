"""The AmpliPi integration."""
from __future__ import annotations
import os
import shutil

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_NAME, CONF_ID
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pyamplipi.amplipi import AmpliPi

from .const import DOMAIN, AMPLIPI_OBJECT, CONF_VENDOR, CONF_VERSION, CONF_WEBAPP, CONF_API_PATH

PLATFORMS = ["media_player"]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up AmpliPi from a config entry and ensure blueprints are installed."""
    
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        AMPLIPI_OBJECT: AmpliPi(
            f'http://{entry.data[CONF_HOST]}:{entry.data[CONF_PORT]}/api/',
            10,
            http_session=async_get_clientsession(hass)
        ),
        CONF_VENDOR: entry.data[CONF_VENDOR],
        CONF_NAME: entry.data[CONF_NAME],
        CONF_HOST: entry.data[CONF_HOST],
        CONF_PORT: entry.data[CONF_PORT],
        CONF_ID: entry.data[CONF_ID],
        CONF_VERSION: entry.data[CONF_VERSION],
        CONF_WEBAPP: entry.data[CONF_WEBAPP],
        CONF_API_PATH: entry.data[CONF_API_PATH],
    }

    # Copy all blueprints to Home Assistant's blueprints directory
    await hass.async_add_executor_job(copy_blueprints, hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


def copy_blueprints(hass: HomeAssistant):
    """Recursively copy all blueprints from the integration directory to Home Assistant's blueprints folder."""
    source_dir = os.path.join(os.path.dirname(__file__), "blueprints", "automation")
    dest_dir = os.path.join(hass.config.path("blueprints/automation/hacs_amplipi"))

    if not os.path.exists(source_dir):
        return

    os.makedirs(dest_dir, exist_ok=True)

    for root, _, files in os.walk(source_dir):
        for file in files:
            if file.endswith(".yaml"):
                src_path = os.path.join(root, file)
                rel_path = os.path.relpath(src_path, source_dir)
                dest_path = os.path.join(dest_dir, rel_path)

                os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                shutil.copy(src_path, dest_path)
