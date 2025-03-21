from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from typing import Any
import logging
import aiohttp
import json
from .coordinator import AmpliPiDataClient

from .const import DOMAIN, UPDATER_URL, AMPLIPI_OBJECT

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the AmpliPi MultiZone Audio Controller"""
    hass_entry = hass.data[DOMAIN][config_entry.entry_id]
    updater_url = hass_entry[UPDATER_URL]

    data_coordinator = hass_entry[AMPLIPI_OBJECT]
    update: list[UpdateEntity] = [
        AmpliPiUpdate(coordinator=data_coordinator, updater_url=updater_url)
    ]

    async_add_entities(update)

class AmpliPiUpdate(CoordinatorEntity, UpdateEntity):

    def __init__(self, coordinator: AmpliPiDataClient, updater_url):
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
        if self.coordinator.status is not None:
            return getattr(self.coordinator.status.info, "version", None)
        return None

    @property
    def latest_version(self):
        if self.coordinator.status is not None:
            return getattr(self.coordinator.status.info, "latest_release", None)
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
        return UpdateEntityFeature.INSTALL | UpdateEntityFeature.RELEASE_NOTES # TODO: Support UpdateEntityFeature.SPECIFIC_VERSION

    def get_update_info(self, update: str = "latest"):
        if self.coordinator.data is not None:
            if update == "latest":
                return self.coordinator.latest
            else:
                for release in self.coordinator.releases:
                    if release["name"] == update:
                        return release
        return None

    async def async_install(self, version: str = "latest", backup: bool = False, **kwargs: Any):
        async def watch_and_restart():
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self._updater_url}/update/install/progress") as resp:
                    async for line in resp.content:
                        # Example lines:
                        # b'data: {"message": "starting installation", "type": "info"}\r\n'
                        # b'\r\n'
                        # b'data: {"message": "Got amplipi release: micro-nova-AmpliPi-ffcec58", "type": "info"}\r\n'
                        # b'\r\n'
                        # b'event: ping\r\n'
                        # b'data: 2025-06-19 14:28:19.371839\r\n'
                        previous_line_message = "first line"
                        decoded_line = line.decode("utf-8")
                        if "message" in decoded_line:
                            data_json = decoded_line[len("data: "):].strip()
                            data = json.loads(data_json)

                            if data["message"] != "  ":
                                if data["type"] == "info":
                                    # It might seem odd to make something literally labeled info be printed as not info level, but this is very loud
                                    self._LOGGER.debug(data["message"])
                                elif data["type"] == "warning":
                                    self._LOGGER.warning(data["message"])
                                elif data["type"] == "failure":
                                    self._LOGGER.error(data["message"])
                                else:
                                    self._LOGGER.info(data["message"])

                            if data["type"] in ("success", "failed"):
                                if data["type"] == "success":
                                    session.post(f"{self._updater_url}/update/restart", timeout=aiohttp.ClientTimeout(total=1000))
                                else:
                                    raise Exception(f"Update failed on: {previous_line_message}")
                                break
                            previous_line_message = data["message"]

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
                        raise Exception("AmpliPi System Update download failed")

                async with session.get(
                    f"{self._updater_url}/update/install",
                    timeout=aiohttp.ClientTimeout(total=1000)
                ) as install_resp:
                    if install_resp.status != 200:
                        raise Exception("AmpliPi System Update installation failed")

            await watch_and_restart()

        except Exception as e:
            self._LOGGER.error(f"Update failed due to error: {e}")
        finally:
            self.in_progress = False
            await self.coordinator.async_request_refresh()

    async def async_release_notes(self):
        return self.release_summary
        