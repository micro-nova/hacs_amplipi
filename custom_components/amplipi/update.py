from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from typing import Any
from homeassistant.const import STATE_UNKNOWN
import logging
import requests
import aiohttp
import json
from .coordinator import AmpliPiCoordinator

from .const import DOMAIN, UPDATER_URL, COORDINATOR

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the AmpliPi MultiZone Audio Controller"""
    hass_entry = hass.data[DOMAIN][config_entry.entry_id]
    updater_url = hass_entry[UPDATER_URL]

    data_coordinator = hass_entry[COORDINATOR]
    update: list[UpdateEntity] = [
        AmpliPiUpdate(coordinator=data_coordinator, updater_url=updater_url)
    ]

    async_add_entities(update)

class AmpliPiUpdate(CoordinatorEntity, UpdateEntity):

    def __init__(self, coordinator: AmpliPiCoordinator, updater_url):
        super().__init__(coordinator)

        self.title = "AmpliPi"
        self._LOGGER = _LOGGER
        self._updater_url = updater_url
        self.entity_id = "update.amplipi_system_update"
        self.domain = "update"
        self._release_summary = None
        self._release_url = None

    @property
    def installed_version(self):
        if self.coordinator.data is not None:
            return getattr(self.coordinator.data.status.info, "version", None)

    @property
    def latest_version(self):
        if self.coordinator.data is not None:
            return getattr(self.coordinator.data.status.info, "latest_release", None)
        return None

    @property
    def release_url(self):
        if self.installed_version != self.latest_version:
            update_info = self.get_update_info()
            if update_info is not None:
                self._release_url = update_info["html_url"]
        return self._release_url

    @property
    def release_summary(self):
        if self.installed_version != self.latest_version:
            update_info = self.get_update_info()
            if update_info is not None:
                self._release_summary = update_info["body"]
        return self._release_summary
    
    @property
    def should_poll(self):
        """Polling needed."""
        return True

    @property
    def available(self):
        """Is the entity able to be used by the user? Should always return True so long as the entity is loaded."""
        return True

    @property
    def name(self) -> str:
        return "AmpliPi System Update"

    @property
    def unique_id(self) -> str:
        return "amplipi_system_update"

    @property
    def supported_features(self):
        return UpdateEntityFeature.INSTALL | UpdateEntityFeature.RELEASE_NOTES | UpdateEntityFeature.SPECIFIC_VERSION

    def get_update_info(self, update: str = "latest"):
        if self.coordinator.data is not None:
            if update == "latest":
                return self.coordinator.data.latest
            else:
                for release in self.coordinator.data.releases:
                    if release["name"] == update:
                        return release
        return None

    async def async_install(self, version: str = "latest", backup: bool = False, **kwargs: Any):
        async def watch_and_restart():
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self._updater_url}/update/install/progress") as resp:
                    async for line in resp.content:
                        if line.startswith(b"data:"):
                            data_json = line[len(b"data:"):].strip()
                            data = json.loads(data_json.decode("utf-8"))
                            self._LOGGER.debug(data)
                            if data.get("type") in ("success", "failed"):
                                if data["type"] == "success":
                                    session.post(f"{self._updater_url}/update/restart", timeout=aiohttp.ClientTimeout(total=1000))
                                else:
                                    raise Exception(f"Update failed! {data}")
                                break

        self.in_progress = True
        try:
            info = self.get_update_info(version)

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self._updater_url}/update/download",
                    json={'url': info['tarball_url'], 'version': info['name']},
                    timeout=aiohttp.ClientTimeout(total=1000)
                ) as download_resp:
                    if download_resp.status != 200:
                        raise Exception("Download failed")

                async with session.get(
                    f"{self._updater_url}/update/install",
                    timeout=aiohttp.ClientTimeout(total=1000)
                ) as install_resp:
                    if install_resp.status != 200:
                        raise Exception("Install failed")

            await watch_and_restart()

        except Exception as e:
            self._LOGGER.error(f"Update failed due to error: {e}")
        finally:
            self.in_progress = False


    async def async_release_notes(self):
        return self.release_summary
        