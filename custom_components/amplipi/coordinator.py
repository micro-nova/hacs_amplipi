"""Api data coordinator for amplipi entities"""
from datetime import timedelta
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

class AmpliPiCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, logger, config_entry, my_api):
        super().__init__(
            hass,
            logger,
            config_entry=config_entry,
            name="hacs_amplipi",
            update_interval=timedelta(seconds=2),
            always_update=True
        )
        self.api = my_api

    async def _async_update_data(self):
        """Fetch data from API endpoint.

        This is the place to pre-process the data to lookup tables
        so entities can quickly look up their data.
        """
        try:
            state = await self.api.get_status()
            return state
        except Exception as e:
            raise UpdateFailed(f"Error fetching data: {e}")
        